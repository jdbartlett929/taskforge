import time
import uuid

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=10)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    available_at: Mapped[float] = mapped_column(Float, default=time.time)
    dispatch_at: Mapped[float] = mapped_column(Float, default=0)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        Index("ix_jobs_ready", "status", "available_at", "dispatch_at"),
        Index("ix_jobs_lease", "status", "lease_until"),
        Index("ix_jobs_created", "created_at"),
    )


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[float] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("job_id", "number"),)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    at: Mapped[float] = mapped_column(Float, default=time.time)
    kind: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str] = mapped_column(Text)
