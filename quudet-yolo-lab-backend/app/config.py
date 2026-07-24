from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ExecutionBackend = Literal["celery", "remote-agent"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    PROJECT_NAME: str = "QuuDet YOLO Lab API"
    API_V1_PREFIX: str = "/api/v1"

    # Database — sqlite for local dev without PostgreSQL.
    DATABASE_URL: str = "sqlite:///./data/quudet.db"

    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    FIRST_SUPERUSER_EMAIL: str = "admin@quudet.local"
    FIRST_SUPERUSER_PASSWORD: str = "admin123"

    CORS_ORIGINS: str = "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8080,http://localhost:8080"

    REDIS_URL: str = "redis://localhost:6379/0"

    # Data directories (inside container: /data)
    # Use absolute path so local dev works regardless of current working directory.
    DATA_DIR: Path = Path(__file__).resolve().parents[1] / "data"
    UPLOADS_SUBDIR: str = "uploads"
    ARTIFACTS_SUBDIR: str = "artifacts"

    # YOLO: working directory (repo root with ultralytics / datasets)
    YOLO_WORK_DIR: Path | None = None  # default: repo root (parent of this backend folder)

    # Artifact store — pluggable storage backend for experiment outputs.
    #   local — local filesystem (default; works without external services)
    #   s3    — MinIO / S3-compatible (future)
    ARTIFACT_STORE_BACKEND: str = "local"

    # --- Execution backend (maintenance tasks only) ---
    # Training/val/detect jobs always go through unified node scheduling
    # (PENDING_ASSIGN → claim-next → agent execution).
    #
    # This setting only affects non-training maintenance tasks:
    #   celery        — reconciliation via Celery Beat
    #   remote-agent  — legacy dispatch mode (keep for backward compat)
    EXECUTION_BACKEND: ExecutionBackend = "celery"

    # If True, API accepts requests without JWT and attributes actions to a built-in guest user.
    # Set False when exposing the service to the internet (use login + strong secrets).
    DISABLE_AUTH: bool = True

    GUEST_USER_EMAIL: str = "guest@quudet.local"

    # --- Legacy cluster settings (only used when EXECUTION_BACKEND=remote-agent) ---
    CLUSTER_ENABLED: bool | None = None
    NODE_SHARED_TOKEN: str = "change-me-node-token"
    NODE_HEARTBEAT_TIMEOUT_SECONDS: int = 20

    @property
    def resolved_yolo_work_dir(self) -> Path:
        if self.YOLO_WORK_DIR is not None:
            return Path(self.YOLO_WORK_DIR).resolve()
        # quudet-yolo-lab-backend/app/config.py -> parents[2] == workspace root (e.g. d:\\yolo26)
        return Path(__file__).resolve().parents[2]

    @property
    def uploads_dir(self) -> Path:
        p = self.DATA_DIR / self.UPLOADS_SUBDIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def artifacts_dir(self) -> Path:
        p = self.DATA_DIR / self.ARTIFACTS_SUBDIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def effective_cluster_enabled(self) -> bool:
        """Resolve whether cluster/dispatch mode is active."""
        if self.EXECUTION_BACKEND == "remote-agent":
            return True
        if self.CLUSTER_ENABLED is not None:
            return self.CLUSTER_ENABLED
        return False

    def cors_list(self) -> list[str]:
        return [x.strip() for x in self.CORS_ORIGINS.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
