import json
import logging
import time


def configure():
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def log(kind: str, **fields):
    logging.getLogger("taskforge").info(json.dumps({"at": time.time(), "event": kind, **fields}))
