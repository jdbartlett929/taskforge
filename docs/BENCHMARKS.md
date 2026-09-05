# Measurements

No 10,000-job throughput claim has been made.

The reproducible benchmark runs a mixture of SHA-256 jobs and deliberately transient failures against real PostgreSQL and Redis. Its JSON report identifies the machine, Python version, worker count, workload, timings, success count, and retry recovery.

## Verification status at implementation time

- Local unit/API suite: 37 tests passed on Windows / Python 3.12.
- Local concurrency substitutes: SQLite and emulated Redis; not a production throughput measurement.
- Full real PostgreSQL/Redis suite: **40 passed, 0 skipped, 0 failures**. Docker image build and two-worker Compose/API retry smoke test also passed.
- Cloud deployment: prepared only; no paid infrastructure requested or created.

## Recorded real-service run

[GitHub Actions run 33983544649](https://github.com/jdbartlett929/taskforge/actions/runs/33983544649), source commit `3ab40f9`, measured 2026-09-05 at 18:17:42 UTC. Shared Linux runner, 4 logical CPUs, Python 3.12.14, PostgreSQL 17 and Redis 7.4.

| Measurement | Result |
| --- | ---: |
| Jobs submitted / succeeded | 100 / 100 |
| Workers configured / observed executing | 8 / 8 |
| Recorded attempts | 110 |
| Fail-once jobs recovered | 10 / 10 (100%) |
| Terminal job failures | 0 |
| Direct submission time | 0.307 s |
| Startup + drain time | 25.228 s |
| End-to-end time | 25.535 s |
| Drain throughput | 3.964 jobs/s |
| End-to-end throughput | 3.916 jobs/s |
| p95 job latency | 24.710 s |

The workload was 90 SHA-256 jobs with 1,000 iterations each and 10 deliberate fail-once jobs. Per-job subprocess startup is included and dominates this small-task workload. This is a measured functional baseline, not an optimized throughput result or a 10,000-job test. The full machine-readable report is retained as `docs/benchmark-33983544649.json` and in the run's artifacts.

## Interpretation

`drain_jobs_per_second` includes worker startup, subprocess startup, retries, database writes, and draining all submitted jobs. `end_to_end_jobs_per_second` additionally includes direct service submission. Neither metric includes HTTP ingress. Retry recovery is the fraction of designated fail-once tasks that later succeed.

A short shared-runner measurement is a reproducibility check, not a service capacity promise. Report its sample size and environment. For a resume, use only the measured report's actual values and keep the workload qualifier.

Raw benchmark reports are uploaded as CI artifacts and may also be retained in this directory with the matching commit/run identifier.
