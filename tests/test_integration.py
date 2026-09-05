import concurrent.futures
import os
import subprocess
import sys
import time

import pytest
from sqlalchemy import select

from taskforge.models import Attempt, Job
from tests.test_service import submit

pytestmark = pytest.mark.integration


def test_postgres_concurrent_claim_is_exclusive(integration_service):
    service = integration_service
    job = submit(service)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda n: service.claim(job["id"], f"worker-{n}"), range(8)))
    assert sum(c is not None for c in claims) == 1
    with service.sessions() as session:
        assert session.get(Job, job["id"]).attempts == 1


def test_real_redis_priority_and_recovery(integration_service):
    service = integration_service
    low = submit(service, priority=0)
    high = submit(service, priority=2)
    service.dispatch()
    assert service.broker.pop() == high["id"]
    service.broker.client.delete(service.broker.key(0))
    time.sleep(0.25)
    service.dispatch()
    assert service.broker.pop() == high["id"]  # unclaimed popped work returns
    assert service.broker.pop() == low["id"]


def test_multiple_worker_processes(integration_service):
    service = integration_service
    jobs = [
        submit(service, task="retry_demo", payload={"fail_until_attempt": 1}, timeout_seconds=5)
        for _ in range(6)
    ]
    jobs += [submit(service, task="sleep", payload={"seconds": 1}, timeout_seconds=0.1, max_attempts=1)]
    environment = os.environ.copy()
    for key, value in service.settings.model_dump().items():
        environment[f"TASKFORGE_{key.upper()}"] = str(value)
    workers = [
        subprocess.Popen(
            [sys.executable, "-m", "taskforge.worker", "--id", f"integration-{i}"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for i in range(3)
    ]
    try:
        deadline = time.monotonic() + 45
        statuses = []
        while time.monotonic() < deadline:
            with service.sessions() as session:
                saved = session.scalars(select(Job).where(Job.id.in_([j["id"] for j in jobs]))).all()
                statuses = [j.status for j in saved]
                if all(s in ("succeeded", "failed", "timed_out") for s in statuses):
                    break
            if any(p.poll() is not None for p in workers):
                pytest.fail("A worker exited unexpectedly")
            time.sleep(0.1)
        assert statuses.count("succeeded") == 6
        assert statuses.count("timed_out") == 1
        with service.sessions() as session:
            attempts = session.scalars(select(Attempt)).all()
            assert len(attempts) == 13
            assert len({a.worker_id for a in attempts}) >= 2
        assert service.broker.workers()
    finally:
        for process in workers:
            process.terminate()
        for process in workers:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
