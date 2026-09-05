import pytest
from pydantic import ValidationError

from taskforge.tasks import execute, validate_payload
from taskforge.worker import execute_isolated


@pytest.mark.parametrize("n,value", [(0, "0"), (1, "1"), (10, "55"), (100, "354224848179261915075")])
def test_fibonacci(n, value):
    assert execute("fibonacci", {"n": n}, 1)["value"] == value


def test_primes():
    assert execute("primes", {"limit": 100}, 1)["count"] == 25


def test_hash_is_deterministic():
    payload = {"text": "hello", "iterations": 12}
    assert execute("hash", payload, 1) == execute("hash", payload, 1)
    assert len(execute("hash", payload, 1)["sha256"]) == 64


def test_transient_failure():
    with pytest.raises(RuntimeError):
        execute("retry_demo", {"fail_until_attempt": 2}, 2)
    assert execute("retry_demo", {"fail_until_attempt": 2}, 3)["recovered"]


@pytest.mark.parametrize(
    "task,payload",
    [
        ("shell", {}),
        ("primes", {"limit": 999999999}),
        ("sleep", {"seconds": -1}),
        ("hash", {"text": "a" * 5000}),
        ("fibonacci", {"n": 4, "command": "ignored"}),
        ("sleep", {"seconds": float("nan")}),
    ],
)
def test_payload_limits(task, payload):
    with pytest.raises(ValidationError):
        validate_payload(task, payload)


def test_timeout_kills_process():
    status, result, error = execute_isolated("sleep", {"seconds": 4}, 1, 0.15)
    assert status == "timed_out"
    assert result is None
    assert "timeout" in error


def test_isolated_execution():
    status, result, error = execute_isolated("fibonacci", {"n": 10}, 1, 5)
    assert (status, result, error) == ("succeeded", {"n": 10, "value": "55"}, None)
