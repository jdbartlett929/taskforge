# TaskForge

A distributed job-processing backend with a small operations console. Python/FastAPI accepts bounded computational tasks; Redis orders ready work; PostgreSQL persists jobs, attempts, results, and the event history.

**This is a backend project, not a simulated dashboard.** The console calls the same REST API that clients use. Jobs execute in isolated subprocesses. A separately labeled local preview is available when Docker is unavailable.

## Quick start — real services

Requires Docker Engine/Desktop with Docker Compose v2.

```bash
cp .env.example .env
# Edit .env: use two independent random secrets (at least 24 characters).
# Keep POSTGRES_PASSWORD URL-safe; a 32-byte hex value works.
docker compose up -d --build --scale worker=4
```

Open **http://localhost:8000**, enter the API key from your private `.env`, and click Connect. The key stays in browser memory, not localStorage. OpenAPI docs: **http://localhost:8000/docs**.

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "X-API-Key: $TASKFORGE_API_KEY" \
  -H "Idempotency-Key: example-primes-001" \
  -H "Content-Type: application/json" \
  -d '{"task":"primes","payload":{"limit":100000},"priority":2,"max_attempts":3,"timeout_seconds":10}'
```

Export `TASKFORGE_API_KEY` into your shell separately; Compose reads `.env` but does not export it to curl.

```bash
curl -H "X-API-Key: $TASKFORGE_API_KEY" http://localhost:8000/api/jobs/JOB_ID
docker compose logs -f worker
docker compose up -d --scale worker=8
docker compose stop
docker compose start
```

Named volumes preserve PostgreSQL history and Redis AOF through service restarts. Do not use `down -v` on data you want to retain.

## Included

- REST submission, paginated/filterable history, results, attempts, and timeline
- Three priority levels: 2 high, 1 normal, 0 low; FIFO by creation time within each level
- Multiple independent workers with atomic claims and per-attempt lease tokens
- Retry budget, exponential backoff (1, 2, 4… seconds, capped at 60)
- Hard execution timeout, subprocess termination, dead-worker lease recovery
- Redis heartbeat health monitoring; offline workers expire from the live fleet
- Request idempotency: same key + same normalized request returns the original job; different request returns 409
- Bounded task inputs, API-key protection, request-size limit, active-job admission limit
- JSON worker logs plus durable PostgreSQL job events
- Unit/API tests, real-service integration tests, Docker smoke tests, CI artifacts
- Benchmark harness that records actual throughput and retry recovery
- Docker image publishing workflow and an HTTPS Compose deployment overlay

## Architecture

```mermaid
flowchart LR
  C[Client / Operations console] -->|REST + API key| A[FastAPI]
  A -->|commit job + submitted event| P[(PostgreSQL)]
  D[Worker dispatch / recovery loop] -->|scan due durable jobs| P
  D -->|idempotent ZADD| R[(Redis priority queues)]
  R -->|atomic priority pop| W[Worker processes]
  W -->|row lock + fenced lease| P
  W --> X[Bounded task subprocess]
  X -->|result or timeout| W
  W -->|attempt + result + events| P
  W -->|expiring heartbeat| R
```

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for crash windows, transaction boundaries, and guarantees.

## Tasks

| Task | Payload example | Bound |
| --- | --- | --- |
| `primes` | `{"limit":100000}` | limit ≤ 1,000,000 |
| `fibonacci` | `{"n":100}` | n ≤ 1,000 |
| `hash` | `{"text":"hello","iterations":10000}` | text ≤ 4,096 chars; iterations ≤ 500,000 |
| `retry_demo` | `{"fail_until_attempt":2}` | deliberate transient failure; useful for recovery tests |
| `sleep` | `{"seconds":2}` | seconds ≤ 30; useful for timeout tests |

Only registered task types execute. Requests cannot submit Python, shell commands, file paths, or arbitrary executable code. Each job allows 1–6 attempts and a timeout of 0.1–60 seconds. Startup of the isolated process counts toward that timeout.

## REST API

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/jobs` | Durable submission; 202 new / 200 duplicate |
| GET | `/api/jobs?status=running&limit=20&offset=0` | Persistent history |
| GET | `/api/jobs/{id}` | Full job, attempts, timeline, result |
| GET | `/api/stats` | Status counts, retried jobs, recent p95 attempt duration |
| GET | `/api/workers` | Workers with unexpired heartbeats |
| GET | `/api/info` | Runtime mode / storage labels |
| GET | `/health/live` | Process liveness |
| GET | `/health/ready` | PostgreSQL + Redis reachability |

All `/api/*` routes require `X-API-Key`. Health probes and static console assets are public; they contain no job data. POST bodies are limited to 16 KiB. Use HTTPS for cloud traffic.

## Tests

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.lock
pip install --no-deps -e .
ruff check .
pytest -m "not integration" -q
```

The integration suite needs real services. Use disposable databases, not a production connection. Tests create a uniquely named schema and Redis namespace, then remove only those generated resources.

```bash
export TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/TEST_DATABASE
export TEST_REDIS_URL=redis://localhost:6379/0
pytest -q
```

CI provisions PostgreSQL and Redis containers, runs the full suite, measures 100 jobs across 8 workers, uploads benchmark/coverage/JUnit artifacts, builds the Docker image, and exercises retries through the actual Compose API. Integration tests are **skipped**, not silently emulated, if their environment variables are missing.

## Benchmark

```bash
python scripts/benchmark.py --jobs 100 --workers 8
# Larger measured run, when you have time and capacity:
python scripts/benchmark.py --jobs 10000 --workers 8 --deadline 1800
```

The workload is 90% SHA-256 ×1,000 and 10% fail-once tasks. Results include configured and observed workers, attempts, successful retry recovery, submission/drain/end-to-end timing, throughput, and p95 job latency. Direct service submission excludes HTTP overhead. Worker startup and subprocess overhead are included in drain time. Compare identical workloads and machines.

See [BENCHMARKS.md](docs/BENCHMARKS.md). Never turn an unrun 10,000-job command into a measured resume claim.

## Local preview without Docker

```bash
python scripts/local_preview.py
```

Open http://127.0.0.1:8000 and click Connect with an empty key. This explicitly uses **SQLite + emulated Redis and one local worker**. It executes the real task functions and retry/timeout paths, but does not verify PostgreSQL concurrency, Redis networking, or distributed throughput. Production mode requires PostgreSQL/Redis and a secret API key.

## Cloud deployment

[DEPLOYMENT.md](docs/DEPLOYMENT.md) covers an existing EC2/Linux host, HTTPS with Caddy, private database/Redis ports, RDS connection configuration, backups, and rollback. No paid resources are created by this repository. The container publication workflow publishes immutable commit tags to GHCR; infrastructure deployment remains an explicit operator action.

## Scope and honest limitations

This is an at-least-once processing system, not an exactly-once side-effect engine. Fencing protects job-state writes; a task calling an external service must use its own idempotency key. The bundled tasks have no external side effects.

A single PostgreSQL/Redis deployment is not a highly available cluster. Strict priorities can starve low-priority work. API keys are shared workspace credentials, not user accounts or tenant isolation. The initial schema initializer is idempotent, but future schema changes need explicit migrations. See architecture and deployment notes before extending it.

