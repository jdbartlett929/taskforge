from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TASKFORGE_")
    database_url: str = "postgresql+psycopg://taskforge:taskforge@localhost:5432/taskforge"
    redis_url: str = "redis://localhost:6379/0"
    api_key: str = ""
    allow_insecure_local: bool = False
    queue_prefix: str = "taskforge"
    heartbeat_seconds: float = 2
    worker_stale_seconds: float = 10
    poll_seconds: float = 0.15
    dispatch_seconds: float = 1
    redis_redelivery_seconds: float = 5
    lease_grace_seconds: float = 5
    retry_base_seconds: float = 1
    max_queue_depth: int = 50000
