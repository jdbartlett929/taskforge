import hashlib
import json
import time
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from taskforge.models import Attempt, Event, Job

ACTIVE = ("queued", "retry_wait", "running")
READY = ("queued", "retry_wait")
TERMINAL = ("succeeded", "failed", "timed_out")


class Conflict(Exception):
    pass


class QueueFull(Exception):
    pass


def event(session, job, kind, detail):
    session.add(Event(job_id=job.id, kind=kind, detail=detail))


def serialize(job):
    return {
        column.name: getattr(job, column.name)
        for column in Job.__table__.columns
        if column.name not in ("lease_token", "request_hash", "dispatch_at")
    }


class Service:
    def __init__(self, sessions, broker, settings):
        self.sessions, self.broker, self.settings = sessions, broker, settings

    def submit(self, request: dict, key: str | None = None):
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        try:
            with self.sessions.begin() as session:
                if session.bind.dialect.name == "postgresql":
                    # Serialize intake accounting, not worker execution.
                    session.execute(text("SELECT pg_advisory_xact_lock(819237)"))
                if key:
                    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
                    if existing:
                        if existing.request_hash != fingerprint:
                            raise Conflict("Idempotency key already belongs to a different request")
                        return serialize(existing), False
                depth = session.scalar(select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE)))
                if depth >= self.settings.max_queue_depth:
                    raise QueueFull("Active job limit reached")
                job = Job(**request, request_hash=fingerprint, idempotency_key=key)
                session.add(job)
                session.flush()
                event(
                    session, job, "submitted", f"Priority {job.priority}; up to {job.max_attempts} attempts"
                )
                return serialize(job), True
        except IntegrityError:
            if key:
                with self.sessions() as session:
                    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
                    if existing and existing.request_hash == fingerprint:
                        return serialize(existing), False
                    if existing:
                        raise Conflict("Idempotency key already belongs to a different request") from None
            raise

    def claim(self, job_id, worker_id):
        now = time.time()
        with self.sessions.begin() as session:
            job = session.scalar(
                select(Job)
                .where(Job.id == job_id, Job.status.in_(READY), Job.available_at <= now)
                .with_for_update(skip_locked=True)
            )
            if not job:
                return None
            job.status = "running"
            job.attempts += 1
            job.worker_id = worker_id
            job.started_at = now
            job.finished_at = None
            job.lease_token = str(uuid.uuid4())
            job.lease_until = now + job.timeout_seconds + self.settings.lease_grace_seconds
            session.add(
                Attempt(
                    id=job.lease_token,
                    job_id=job.id,
                    number=job.attempts,
                    worker_id=worker_id,
                    started_at=now,
                )
            )
            event(session, job, "started", f"Attempt {job.attempts} on {worker_id}")
            return {
                "id": job.id,
                "task": job.task,
                "payload": job.payload,
                "attempt": job.attempts,
                "token": job.lease_token,
                "timeout": job.timeout_seconds,
            }

    def _failure(self, session, job, status, error, now):
        attempt = session.get(Attempt, job.lease_token)
        if attempt:
            attempt.status, attempt.error, attempt.finished_at = status, error, now
        job.error = error
        event(session, job, status, error)
        job.lease_token, job.lease_until = None, None
        if job.attempts < job.max_attempts:
            delay = min(60, self.settings.retry_base_seconds * 2 ** (job.attempts - 1))
            job.status, job.available_at, job.dispatch_at = "retry_wait", now + delay, 0
            event(session, job, "retry_scheduled", f"Retry in {delay:.3f}s")
        else:
            job.status = "timed_out" if status == "timed_out" else "failed"
            job.finished_at = now

    def finish(self, job_id, token, status, result=None, error=None):
        now = time.time()
        with self.sessions.begin() as session:
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            # Fencing prevents old workers from overwriting a recovered attempt.
            if not job or job.status != "running" or job.lease_token != token or job.lease_until <= now:
                return False
            if status == "succeeded":
                job.status, job.result, job.error, job.finished_at = status, result, None, now
                attempt = session.get(Attempt, token)
                attempt.status, attempt.finished_at = status, now
                job.lease_token, job.lease_until = None, None
                event(session, job, "succeeded", f"Attempt {job.attempts} completed")
            else:
                self._failure(session, job, status, error or "Task failed", now)
            return True

    def recover(self, limit=100):
        now = time.time()
        with self.sessions.begin() as session:
            jobs = session.scalars(
                select(Job)
                .where(Job.status == "running", Job.lease_until <= now)
                .order_by(Job.lease_until)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for job in jobs:
                self._failure(session, job, "lease_expired", "Worker lease expired before completion", now)
            return len(jobs)

    def dispatch(self, limit=500):
        now = time.time()
        with self.sessions.begin() as session:
            jobs = session.scalars(
                select(Job)
                .where(Job.status.in_(READY), Job.available_at <= now, Job.dispatch_at <= now)
                .order_by(Job.priority.desc(), Job.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for job in jobs:
                self.broker.publish(job.id, job.priority, job.created_at)
                job.dispatch_at = now + self.settings.redis_redelivery_seconds
            return len(jobs)
