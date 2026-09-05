import time

import pytest
from sqlalchemy import select

from taskforge.models import Attempt, Event, Job
from taskforge.service import Conflict, QueueFull


def submit(service, **overrides):
    body = dict(task="fibonacci", payload={"n": 10}, priority=1, max_attempts=3, timeout_seconds=3)
    body.update(overrides)
    return service.submit(body)[0]


def test_priority_and_fifo(service):
    a = submit(service, priority=0)
    b = submit(service, priority=2)
    c = submit(service, priority=1)
    d = submit(service, priority=2)
    service.dispatch()
    assert [service.broker.pop() for _ in range(4)] == [b["id"], d["id"], c["id"], a["id"]]


def test_duplicate_delivery_only_one_claim(service):
    job = submit(service)
    first = service.claim(job["id"], "worker-a")
    assert first
    assert service.claim(job["id"], "worker-b") is None


def test_idempotent_submission(service):
    request = dict(task="fibonacci", payload={"n": 10}, priority=1, max_attempts=3, timeout_seconds=3)
    a, created = service.submit(request, "unique-key")
    b, duplicate = service.submit(request, "unique-key")
    assert created and not duplicate and a["id"] == b["id"]
    with pytest.raises(Conflict):
        service.submit({**request, "priority": 2}, "unique-key")


def test_queue_capacity(service):
    service.settings.max_queue_depth = 1
    submit(service)
    with pytest.raises(QueueFull):
        submit(service)


def test_success_is_durable(service):
    job = submit(service)
    claim = service.claim(job["id"], "worker-a")
    assert service.finish(job["id"], claim["token"], "succeeded", {"value": "55"})
    with service.sessions() as session:
        saved = session.get(Job, job["id"])
        assert saved.status == "succeeded" and saved.result["value"] == "55"
        assert session.get(Attempt, claim["token"]).status == "succeeded"
        assert len(session.scalars(select(Event).where(Event.job_id == job["id"])).all()) == 3
    assert service.claim(job["id"], "other") is None


def test_retry_then_exhaustion(service):
    service.settings.retry_base_seconds = 30
    job = submit(service, max_attempts=2)
    claim = service.claim(job["id"], "worker")
    service.finish(job["id"], claim["token"], "failed", error="transient")
    assert service.claim(job["id"], "early") is None
    with service.sessions.begin() as session:
        saved = session.get(Job, job["id"])
        assert saved.available_at > time.time()
        saved.available_at = time.time() - 1
    second = service.claim(job["id"], "worker")
    service.finish(job["id"], second["token"], "timed_out", error="timeout")
    with service.sessions() as session:
        saved = session.get(Job, job["id"])
        assert saved.status == "timed_out" and saved.attempts == 2
        assert saved.finished_at is not None


def test_expired_lease_recovery_and_fencing(service):
    job = submit(service)
    old = service.claim(job["id"], "old")
    with service.sessions.begin() as session:
        session.get(Job, job["id"]).lease_until = time.time() - 1
    assert not service.finish(job["id"], old["token"], "succeeded", {"bad": True})
    assert service.recover() == 1
    time.sleep(0.025)
    new = service.claim(job["id"], "new")
    assert new["token"] != old["token"]
    assert not service.finish(job["id"], old["token"], "succeeded", {"bad": True})
    assert service.finish(job["id"], new["token"], "succeeded", {"good": True})


def test_publish_failure_does_not_lose_job(service, monkeypatch):
    job = submit(service)
    publish = service.broker.publish
    monkeypatch.setattr(service.broker, "publish", lambda *_: (_ for _ in ()).throw(ConnectionError()))
    with pytest.raises(ConnectionError):
        service.dispatch()
    monkeypatch.setattr(service.broker, "publish", publish)
    assert service.dispatch() == 1
    assert service.broker.pop() == job["id"]


def test_pop_before_claim_crash_is_redelivered(service):
    job = submit(service)
    service.dispatch()
    assert service.broker.pop() == job["id"]
    time.sleep(0.025)
    service.dispatch()
    assert service.broker.pop() == job["id"]


def test_reconstruct_queue_after_redis_loss(service):
    job = submit(service)
    service.dispatch()
    service.broker.client.delete(service.broker.key(1))
    time.sleep(0.025)
    service.dispatch()
    assert service.broker.pop() == job["id"]


def test_worker_health_expiry(service):
    service.broker.heartbeat("w", None, 1)
    assert service.broker.workers()[0]["id"] == "w"
    service.broker.client.delete(f"{service.broker.prefix}:worker:w")
    assert service.broker.workers() == []
