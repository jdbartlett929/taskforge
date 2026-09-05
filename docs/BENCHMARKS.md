# Measurements

No 10,000-job throughput claim has been made.

The reproducible benchmark runs a mixture of SHA-256 jobs and deliberately transient failures against real PostgreSQL and Redis. Its JSON report identifies the machine, Python version, worker count, workload, timings, success count, and retry recovery.

## Verification status at implementation time

- Local unit/API suite: 37 tests passed on Windows / Python 3.12.
- Local concurrency substitutes: SQLite and emulated Redis; not a production throughput measurement.
- Real PostgreSQL/Redis integration and 100-job / 8-worker benchmark: results will be recorded after the CI run completes.
- Cloud deployment: prepared only; no paid infrastructure requested or created.

## Interpretation

`drain_jobs_per_second` includes worker startup, subprocess startup, retries, database writes, and draining all submitted jobs. `end_to_end_jobs_per_second` additionally includes direct service submission. Neither metric includes HTTP ingress. Retry recovery is the fraction of designated fail-once tasks that later succeed.

A short shared-runner measurement is a reproducibility check, not a service capacity promise. Report its sample size and environment. For a resume, use only the measured report's actual values and keep the workload qualifier.

Raw benchmark reports are uploaded as CI artifacts and may also be retained in this directory with the matching commit/run identifier.

