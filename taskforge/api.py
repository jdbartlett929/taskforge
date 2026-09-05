import hmac
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from redis import Redis
from sqlalchemy import func, select, text

from taskforge.broker import Broker
from taskforge.config import Settings
from taskforge.db import database
from taskforge.models import Attempt, Event, Job
from taskforge.service import Conflict, QueueFull, Service, serialize
from taskforge.tasks import TaskName, validate_payload


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TaskName
    payload: dict = Field(default_factory=dict)
    priority: int = Field(default=1, ge=0, le=2)
    max_attempts: int = Field(default=3, ge=1, le=6)
    timeout_seconds: float = Field(default=10, ge=0.1, le=60, allow_inf_nan=False)

    @model_validator(mode="after")
    def task_payload(self):
        self.payload = validate_payload(self.task, self.payload)
        return self


def create_app(settings=None, sessions=None, broker=None):
    settings = settings or Settings()
    if not settings.allow_insecure_local and len(settings.api_key) < 24:
        raise RuntimeError("Set TASKFORGE_API_KEY to a secret of at least 24 characters")
    if sessions is None:
        _, sessions = database(settings.database_url)
    broker = broker or Broker(
        Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2),
        settings.queue_prefix,
    )
    service = Service(sessions, broker, settings)

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(
        title="TaskForge",
        version="0.1.0",
        lifespan=lifespan,
        description="Priority jobs, fenced worker leases, durable attempts and retry recovery.",
    )
    app.state.service = service

    def authenticate(x_api_key: Annotated[str | None, Header()] = None):
        if settings.allow_insecure_local and not settings.api_key:
            return
        if not x_api_key or not hmac.compare_digest(x_api_key, settings.api_key):
            raise HTTPException(401, "A valid X-API-Key header is required")

    auth = [Depends(authenticate)]

    @app.middleware("http")
    async def security_headers(request, call_next):
        # Bound JSON input before parsing. Content-Length is not trusted on its own.
        if request.method in ("POST", "PUT", "PATCH"):
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16384:
                    return Response("Request body too large", status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path == "/" or request.url.path.startswith("/static"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
            )
        return response

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready(response: Response):
        try:
            with sessions() as session:
                session.execute(text("SELECT 1"))
            broker.client.ping()
            return {"status": "ready"}
        except Exception:
            response.status_code = 503
            return {"status": "unavailable"}

    @app.post("/api/jobs", dependencies=auth, status_code=202)
    def submit(
        body: Submission,
        response: Response,
        idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
    ):
        try:
            job, created = service.submit(body.model_dump(), idempotency_key)
            response.status_code = 202 if created else 200
            response.headers["Location"] = f"/api/jobs/{job['id']}"
            return job
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/api/jobs", dependencies=auth)
    def jobs(
        status: Literal["queued", "running", "retry_wait", "succeeded", "failed", "timed_out"] | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
    ):
        with sessions() as session:
            statement = select(Job)
            count = select(func.count()).select_from(Job)
            if status:
                statement, count = statement.where(Job.status == status), count.where(Job.status == status)
            return {
                "total": session.scalar(count),
                "items": [
                    serialize(job)
                    for job in session.scalars(
                        statement.order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)
                    )
                ],
            }

    @app.get("/api/jobs/{job_id}", dependencies=auth)
    def detail(job_id: str):
        with sessions() as session:
            job = session.get(Job, job_id)
            if not job:
                raise HTTPException(404, "Job not found")
            data = serialize(job)
            data["history"] = [
                {"at": e.at, "kind": e.kind, "detail": e.detail}
                for e in session.scalars(select(Event).where(Event.job_id == job_id).order_by(Event.id))
            ]
            data["executions"] = [
                {
                    "number": a.number,
                    "worker_id": a.worker_id,
                    "status": a.status,
                    "started_at": a.started_at,
                    "finished_at": a.finished_at,
                    "error": a.error,
                }
                for a in session.scalars(
                    select(Attempt).where(Attempt.job_id == job_id).order_by(Attempt.number)
                )
            ]
            return data

    @app.get("/api/info", dependencies=auth)
    def info():
        preview = settings.database_url.startswith("sqlite")
        return {
            "mode": "local_preview" if preview else "distributed",
            "storage": "SQLite + emulated Redis / one local worker" if preview else "PostgreSQL + Redis",
        }

    @app.get("/api/workers", dependencies=auth)
    def workers():
        try:
            return {"items": broker.workers(), "stale_after_seconds": settings.worker_stale_seconds}
        except Exception as exc:
            raise HTTPException(503, "Worker monitoring unavailable") from exc

    @app.get("/api/stats", dependencies=auth)
    def stats():
        with sessions() as session:
            counts = dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
            retries = session.scalar(select(func.count()).select_from(Job).where(Job.attempts > 1))
            durations = session.scalars(
                select(Attempt.finished_at - Attempt.started_at)
                .where(Attempt.finished_at.is_not(None))
                .order_by(Attempt.finished_at.desc())
                .limit(1000)
            ).all()
        durations.sort()
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "jobs_retried": retries,
            "p95_attempt_seconds": durations[int((len(durations) - 1) * 0.95)] if durations else None,
            "at": time.time(),
        }

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(static / "index.html")

    return app
