import json
import time

from redis import Redis


class Broker:
    def __init__(self, client: Redis, prefix: str):
        self.client, self.prefix = client, prefix

    def key(self, priority: int) -> str:
        return f"{self.prefix}:ready:{priority}"

    def publish(self, job_id: str, priority: int, created_at: float):
        self.client.zadd(self.key(priority), {job_id: created_at})

    def pop(self) -> str | None:
        # Atomic pop across all priorities; strict priority among queued jobs.
        script = """
        for i = 1, #KEYS do
          local item = redis.call('ZPOPMIN', KEYS[i], 1)
          if #item > 0 then return item[1] end
        end
        return false
        """
        return self.client.eval(script, 3, *[self.key(p) for p in (2, 1, 0)])

    def heartbeat(self, worker_id: str, current_job: str | None, ttl: int = 10):
        self.client.set(
            f"{self.prefix}:worker:{worker_id}",
            json.dumps({"id": worker_id, "current_job": current_job, "last_seen": time.time()}),
            ex=ttl,
        )

    def workers(self) -> list[dict]:
        workers = []
        for key in self.client.scan_iter(f"{self.prefix}:worker:*"):
            value = self.client.get(key)
            if value:
                workers.append(json.loads(value))
        return sorted(workers, key=lambda w: w["id"])

    def depth(self) -> dict:
        return {str(p): self.client.zcard(self.key(p)) for p in (2, 1, 0)}
