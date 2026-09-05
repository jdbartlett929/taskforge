import argparse
import multiprocessing
import os
import signal
import socket
import threading
import time
import uuid

from redis import Redis

from taskforge.broker import Broker
from taskforge.config import Settings
from taskforge.db import database
from taskforge.logging import configure, log
from taskforge.service import Service
from taskforge.tasks import child_entry


def execute_isolated(task, payload, attempt, timeout):
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=child_entry, args=(sender, task, payload, attempt))
    started = time.monotonic()
    try:
        process.start()
        sender.close()
        # poll before join avoids a pipe buffer deadlock on larger results.
        if receiver.poll(max(0, timeout - (time.monotonic() - started))):
            try:
                outcome = receiver.recv()
            except EOFError:
                outcome = ("failed", None, "Task process exited without a result")
            process.join(timeout=0.5)
            return outcome
        return "timed_out", None, f"Exceeded {timeout:g}s execution timeout"
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
        receiver.close()
        sender.close()


class Worker:
    def __init__(self, service, worker_id=None):
        self.service = service
        self.settings = service.settings
        self.id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.stop = threading.Event()
        self.current = None

    def heartbeat_loop(self):
        while not self.stop.is_set():
            try:
                self.service.broker.heartbeat(
                    self.id, self.current, max(1, int(self.settings.worker_stale_seconds))
                )
            except Exception as exc:
                log("heartbeat_error", worker=self.id, error=type(exc).__name__)
            self.stop.wait(self.settings.heartbeat_seconds)

    def run_once(self):
        job_id = self.service.broker.pop()
        if not job_id:
            return False
        job = self.service.claim(job_id, self.id)
        if not job:
            return False
        self.current = job["id"]
        log("job_started", worker=self.id, job=job["id"], attempt=job["attempt"])
        try:
            status, result, error = execute_isolated(
                job["task"], job["payload"], job["attempt"], job["timeout"]
            )
            accepted = self.service.finish(job["id"], job["token"], status, result, error)
            log("job_finished", worker=self.id, job=job["id"], status=status, accepted=accepted)
        finally:
            self.current = None
        return True

    def run(self):
        heartbeat = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat.start()
        last_dispatch = 0
        log("worker_online", worker=self.id)
        try:
            while not self.stop.is_set():
                try:
                    now = time.monotonic()
                    if now - last_dispatch >= self.settings.dispatch_seconds:
                        self.service.recover()
                        self.service.dispatch()
                        last_dispatch = now
                    if not self.run_once():
                        self.stop.wait(self.settings.poll_seconds)
                except Exception as exc:
                    # A failed result commit is recovered through the durable lease.
                    log("worker_error", worker=self.id, error=type(exc).__name__)
                    self.stop.wait(1)
        finally:
            self.stop.set()
            heartbeat.join(timeout=3)
            log("worker_stopped", worker=self.id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id")
    args = parser.parse_args()
    configure()
    settings = Settings()
    _, sessions = database(settings.database_url)
    broker = Broker(
        Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2),
        settings.queue_prefix,
    )
    worker = Worker(Service(sessions, broker, settings), args.id)
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    worker.run()


if __name__ == "__main__":
    main()
