"""Explicit single-worker preview: SQLite + emulated Redis. Not a distributed benchmark."""

import threading
from pathlib import Path

import fakeredis
import uvicorn

from taskforge.api import create_app
from taskforge.broker import Broker
from taskforge.config import Settings
from taskforge.db import database, initialize
from taskforge.service import Service
from taskforge.worker import Worker


def main():
    settings = Settings(
        database_url=f"sqlite:///{Path('preview.db').resolve().as_posix()}",
        api_key="",
        allow_insecure_local=True,
    )
    engine, sessions = database(settings.database_url)
    initialize(engine)
    broker = Broker(fakeredis.FakeRedis(decode_responses=True), "local-preview")
    worker = Worker(Service(sessions, broker, settings), "local-preview-worker")
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    print("LOCAL PREVIEW ONLY: SQLite + emulated Redis; one worker. No API key needed.")
    print("Open http://127.0.0.1:8000 and click Connect with an empty key.")
    try:
        uvicorn.run(create_app(settings, sessions, broker), host="127.0.0.1", port=8000)
    finally:
        worker.stop.set()
        thread.join(timeout=70)
        engine.dispose()


if __name__ == "__main__":
    main()
