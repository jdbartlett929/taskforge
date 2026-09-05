from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from taskforge.models import Base


def database(url: str):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    return engine, sessionmaker(engine, expire_on_commit=False)


def initialize(engine):
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    from taskforge.config import Settings

    engine, _ = database(Settings().database_url)
    initialize(engine)
