import hashlib
import math
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fibonacci(StrictPayload):
    n: int = Field(default=100, ge=0, le=1000)


class Primes(StrictPayload):
    limit: int = Field(default=100000, ge=2, le=1000000)


class Hash(StrictPayload):
    text: str = Field(default="TaskForge", max_length=4096)
    iterations: int = Field(default=10000, ge=1, le=500000)


class RetryDemo(StrictPayload):
    fail_until_attempt: int = Field(default=2, ge=0, le=5)


class Sleep(StrictPayload):
    seconds: float = Field(default=2, ge=0, le=30, allow_inf_nan=False)


TaskName = Literal["fibonacci", "primes", "hash", "retry_demo", "sleep"]
PAYLOADS = {"fibonacci": Fibonacci, "primes": Primes, "hash": Hash, "retry_demo": RetryDemo, "sleep": Sleep}


def validate_payload(task: str, payload: dict) -> dict:
    TypeAdapter(TaskName).validate_python(task)
    return PAYLOADS[task].model_validate(payload).model_dump()


def execute(task: str, payload: dict, attempt: int) -> dict:
    if task == "fibonacci":
        a, b = 0, 1
        for _ in range(payload["n"]):
            a, b = b, a + b
        return {"n": payload["n"], "value": str(a)}
    if task == "primes":
        limit = payload["limit"]
        sieve = bytearray(b"\x01") * (limit + 1)
        sieve[:2] = b"\x00\x00"
        for i in range(2, math.isqrt(limit) + 1):
            if sieve[i]:
                sieve[i * i : limit + 1 : i] = b"\x00" * ((limit - i * i) // i + 1)
        return {"limit": limit, "count": sum(sieve)}
    if task == "hash":
        digest = payload["text"].encode()
        for _ in range(payload["iterations"]):
            digest = hashlib.sha256(digest).digest()
        return {"sha256": digest.hex(), "iterations": payload["iterations"]}
    if task == "retry_demo":
        if attempt <= payload["fail_until_attempt"]:
            raise RuntimeError(f"Deliberate transient failure on attempt {attempt}")
        return {"recovered": True, "attempt": attempt}
    if task == "sleep":
        time.sleep(payload["seconds"])
        return {"slept_seconds": payload["seconds"]}
    raise ValueError("Unknown task")


def child_entry(connection, task, payload, attempt):
    try:
        connection.send(("succeeded", execute(task, payload, attempt), None))
    except Exception as exc:
        connection.send(("failed", None, f"{type(exc).__name__}: {exc}"[:2000]))
    finally:
        connection.close()
