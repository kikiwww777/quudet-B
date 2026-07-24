from fastapi import APIRouter

from app.database import check_db_connection

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    """Liveness probe — always returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz():
    """Readiness probe — checks PostgreSQL, Redis, and Celery worker availability.

    Returns 200 when PostgreSQL and Redis are reachable.
    """
    from app.config import get_settings

    settings = get_settings()
    checks: dict[str, bool | str] = {}

    # 1. Database
    checks["database"] = check_db_connection()

    # 2. Redis (required — all job dispatch goes through Celery)
    try:
        import redis as _redis_lib

        r = _redis_lib.from_url(settings.REDIS_URL, socket_timeout=3)
        checks["redis"] = r.ping()
        r.close()
    except Exception:
        checks["redis"] = False

    # 3. Celery worker ping (informational — does not degrade overall status)
    try:
        from app.celery_app import celery_app

        # ``ping()`` returns a list of dicts like [{"worker1": {"ok": "pong"}}, ...]
        pongs = celery_app.control.ping(timeout=2)
        workers = [list(w.keys())[0] for w in pongs if isinstance(w, dict)]
        checks["celery_workers"] = workers if workers else "no_workers_responded"
    except Exception as exc:
        checks["celery_workers"] = f"ping_failed: {exc}"

    # Overall: PG + Redis required; Celery workers are advisory
    required_ok = all(
        v is True for k, v in checks.items() if k in ("database", "redis")
    )

    return {
        "status": "ready" if required_ok else "degraded",
        "checks": checks,
    }
