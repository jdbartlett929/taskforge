"""Exercise the real API through the Compose network-published endpoint."""

import json
import os
import time
import urllib.request

base = os.getenv("TASKFORGE_BASE_URL", "http://localhost:8000")
headers = {"X-API-Key": os.environ["TASKFORGE_API_KEY"], "Content-Type": "application/json"}
request = urllib.request.Request(
    base + "/api/jobs",
    data=json.dumps({"task": "retry_demo", "payload": {"fail_until_attempt": 1}, "max_attempts": 3}).encode(),
    headers=headers,
    method="POST",
)
with urllib.request.urlopen(request, timeout=5) as response:
    job = json.load(response)
deadline = time.monotonic() + 40
while time.monotonic() < deadline:
    with urllib.request.urlopen(
        urllib.request.Request(base + "/api/jobs/" + job["id"], headers=headers), timeout=5
    ) as response:
        result = json.load(response)
    if result["status"] == "succeeded":
        assert result["attempts"] == 2, result
        assert any(e["kind"] == "retry_scheduled" for e in result["history"])
        print("Docker Compose smoke passed: job completed on its second attempt.")
        break
    if result["status"] in ("failed", "timed_out"):
        raise RuntimeError(result)
    time.sleep(0.25)
else:
    raise TimeoutError("Compose worker did not finish the smoke job")
