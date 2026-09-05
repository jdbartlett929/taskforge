# Deployment to an existing cloud server

No cloud resources have been provisioned. These files are ready for an existing Linux/EC2 host when you choose to deploy.

## Single-host Compose

1. Use an existing supported Linux host with Docker Engine and Compose v2. CPU/memory requirements depend on worker count and workload; start with 2 workers and observe memory/CPU before scaling.
2. Clone this repository and check out a tested commit.
3. Copy `.env.example` to `.env`; replace both secrets with independent random values. Keep the database password URL-safe (hex is suitable). Restrict file access: `chmod 600 .env`.
4. Run `docker compose up -d --build --scale worker=2`.
5. Verify `curl --fail http://127.0.0.1:8000/health/ready`. Submit a bounded test job with the private API key.
6. Set `DOMAIN` and `ACME_EMAIL` in `.env`. Point the domain's A/AAAA records at the server.
7. Allow inbound TCP 80/443 for the Caddy HTTPS proxy. Restrict SSH to your administration address. Do not open PostgreSQL 5432, Redis 6379, or application 8000 to the internet.
8. Run:

```bash
docker compose -f compose.yaml -f deploy/compose.cloud.yaml up -d --build --scale worker=2
```

The cloud overlay runs Caddy, requests HTTPS certificates for your domain, and proxies to the API inside the Compose network. Certificate issuance requires working DNS and reachable 80/443. The API's direct host binding remains loopback only.

## RDS / managed services

Set `TASKFORGE_DATABASE_URL` in an operator-maintained override for the API, migration, and worker services. Use a URL of the form `postgresql+psycopg://USER:PASSWORD@PRIVATE_RDS_ENDPOINT:5432/taskforge?sslmode=verify-full&sslrootcert=/run/certs/rds-ca.pem`. Mount the current AWS RDS CA bundle read-only in all application containers; obtain it from AWS's official trust store.

Do not disable certificate verification. Put RDS in private subnets with a security group allowing only the application hosts. Use a dedicated least-privilege application role, and separate schema-migration privileges as you evolve the schema.

For managed Redis, configure `TASKFORGE_REDIS_URL` using a `rediss://` URL and the provider's supported authentication. This project uses multi-key Lua on one Redis instance and is not configured for Redis Cluster hash slots.

## Operations

- Logs: `docker compose logs --since=10m worker api`.
- Workers: `GET /api/workers`; entries expire after the heartbeat TTL.
- Scaling: `docker compose up -d --scale worker=8`. More workers do not guarantee linear speedup.
- Back up PostgreSQL regularly and test restores. Redis is rebuildable from durable job rows, but PostgreSQL loss is not.
- Export important job history before implementing retention. Never manually delete running attempts.
- Rollback: check out the previous tested commit and rebuild/restart. This initial version does not change schemas between releases; future schema migrations need their own backward-compatibility plan.
- API key rotation: update `.env` and recreate application services. In-flight API clients need the new key.
- Shut down using `docker compose stop` to preserve volumes.
- Do not run test/benchmark credentials against production. Those tools require schema-creation permissions and create then drop their own isolated schemas.

## CI / image publishing

Pushes and pull requests run tests plus a Docker Compose smoke test. `release.yml` verifies the project and publishes a GHCR image with the commit SHA when you push a `v*` tag or manually dispatch it. It does not SSH into a server, change DNS, create EC2 instances, or allocate RDS.

Image publication, host deployment, database backups, and high availability are distinct steps. This repository supplies the first and a reproducible deployment recipe, not an unmeasured claim that a production cluster is running.

