import os
import uuid

import fakeredis
import pytest
from redis import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url

from taskforge.broker import Broker
from taskforge.config import Settings
from taskforge.db import database, initialize
from taskforge.service import Service


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        api_key="test-only-key-with-32-characters",
        retry_base_seconds=0.01,
        redis_redelivery_seconds=0.01,
    )
    engine, sessions = database(settings.database_url)
    initialize(engine)
    broker = Broker(fakeredis.FakeRedis(decode_responses=True), "unit:" + uuid.uuid4().hex)
    yield Service(sessions, broker, settings)
    engine.dispose()


@pytest.fixture
def integration_service():
    url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    if not url or not redis_url:
        pytest.skip("Set TEST_DATABASE_URL and TEST_REDIS_URL for real-service integration tests")
    schema = "tf_test_" + uuid.uuid4().hex
    admin, _ = database(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    parsed = make_url(url)
    scoped = parsed.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
        hide_password=False
    )
    settings = Settings(
        database_url=scoped,
        redis_url=redis_url,
        api_key="test-only-key-with-32-characters",
        queue_prefix=schema,
        retry_base_seconds=0.05,
        redis_redelivery_seconds=0.2,
        dispatch_seconds=0.05,
        poll_seconds=0.02,
        lease_grace_seconds=0.5,
    )
    engine, sessions = database(scoped)
    initialize(engine)
    broker = Broker(Redis.from_url(redis_url, decode_responses=True), schema)
    try:
        yield Service(sessions, broker, settings)
    finally:
        keys = list(broker.client.scan_iter(f"{schema}:*"))
        if keys:
            broker.client.delete(*keys)
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
