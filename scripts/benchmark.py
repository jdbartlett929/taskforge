"""Isolated real-service benchmark. Does not alter existing job tables or queue keys."""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from taskforge.broker import Broker
from taskforge.config import Settings
from taskforge.db import database, initialize
from taskforge.models import Attempt, Job
from taskforge.service import Service


def run(args):
    source = os.environ["TEST_DATABASE_URL"]
    redis_url = os.environ["TEST_REDIS_URL"]
    schema = "tf_bench_" + uuid.uuid4().hex
    admin, _ = database(source)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = (
        make_url(source)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    settings = Settings(
        database_url=scoped,
        redis_url=redis_url,
        queue_prefix=schema,
        retry_base_seconds=0.05,
        dispatch_seconds=0.05,
        poll_seconds=0.01,
        redis_redelivery_seconds=2,
    )
    engine, sessions = database(scoped)
    initialize(engine)
    broker = Broker(Redis.from_url(redis_url, decode_responses=True), schema)
    service = Service(sessions, broker, settings)
    environment = os.environ.copy()
    for key, value in settings.model_dump().items():
        environment[f"TASKFORGE_{key.upper()}"] = str(value)
    processes = []
    started = time.monotonic()
    retry_ids = []
    try:
        for i in range(args.jobs):
            retry = i % 10 == 0
            job, _ = service.submit(
                {
                    "task": "retry_demo" if retry else "hash",
                    "payload": {"fail_until_attempt": 1}
                    if retry
                    else {"text": "benchmark", "iterations": 1000},
                    "priority": 1,
                    "max_attempts": 3,
                    "timeout_seconds": 10,
                }
            )
            if retry:
                retry_ids.append(job["id"])
        submitted = time.monotonic()
        processes = [
            subprocess.Popen(
                [sys.executable, "-m", "taskforge.worker", "--id", f"bench-{i}"],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for i in range(args.workers)
        ]
        deadline = time.monotonic() + args.deadline
        while time.monotonic() < deadline:
            with sessions() as session:
                jobs = session.scalars(select(Job)).all()
            if all(job.status in ("succeeded", "failed", "timed_out") for job in jobs):
                break
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("A benchmark worker exited")
            time.sleep(0.1)
        else:
            raise TimeoutError("Benchmark did not complete before its deadline")
        ended = time.monotonic()
        succeeded = sum(j.status == "succeeded" for j in jobs)
        recovered = sum(j.status == "succeeded" and j.attempts > 1 and j.id in retry_ids for j in jobs)
        with sessions() as session:
            attempts = session.scalars(select(Attempt)).all()
        latencies = sorted(j.finished_at - j.created_at for j in jobs)
        result = {
            "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "environment": {
                "os": platform.platform(),
                "python": platform.python_version(),
                "logical_cpus": os.cpu_count(),
                "github_run_id": os.getenv("GITHUB_RUN_ID"),
                "database": "PostgreSQL",
                "queue": "Redis",
            },
            "jobs": args.jobs,
            "configured_workers": args.workers,
            "workers_observed": len({a.worker_id for a in attempts}),
            "workload": "90% SHA-256 x1000, 10% deliberate fail-once jobs; all normal priority",
            "succeeded": succeeded,
            "terminal_failures": args.jobs - succeeded,
            "attempts": len(attempts),
            "deliberate_retry_jobs": len(retry_ids),
            "retry_jobs_recovered": recovered,
            "retry_recovery_percent": round(100 * recovered / len(retry_ids), 3) if retry_ids else None,
            "submit_seconds": round(submitted - started, 3),
            "drain_seconds": round(ended - submitted, 3),
            "end_to_end_seconds": round(ended - started, 3),
            "drain_jobs_per_second": round(args.jobs / (ended - submitted), 3),
            "end_to_end_jobs_per_second": round(args.jobs / (ended - started), 3),
            "mean_job_latency_seconds": round(statistics.mean(latencies), 3),
            "p95_job_latency_seconds": round(latencies[int((len(latencies) - 1) * 0.95)], 3),
            "method": "Direct service submission, then worker process startup and drain; excludes HTTP and network ingress.",
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        if succeeded != args.jobs:
            raise RuntimeError("Benchmark includes terminal job failures; inspect recorded results")
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        keys = list(broker.client.scan_iter(f"{schema}:*"))
        if keys:
            broker.client.delete(*keys)
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--deadline", type=int, default=600)
    parser.add_argument("--output", default="benchmark-results/latest.json")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 10000 or not 1 <= args.workers <= 32:
        parser.error("jobs must be 1..10000 and workers 1..32")
    run(args)
