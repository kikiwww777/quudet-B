from celery import Celery

from app.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "quudet_yolo_lab",
    broker=_settings.REDIS_URL,
    backend=_settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Periodic reconciliation — runs every 60 seconds via ``celery beat``
    beat_schedule={
        "reconcile-every-60s": {
            "task": "quudet.reconcile",
            "schedule": 60.0,
        },
    },
)

# Import task modules so Celery registers them
from app.tasks import executor  # noqa: E402, F401

# Import reconciliation task so Celery Beat can register it
from app.services import reconciliation  # noqa: E402, F401
