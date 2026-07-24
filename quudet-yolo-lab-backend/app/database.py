from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _create_engine():
    """Create a SQLAlchemy engine for PostgreSQL or SQLite local dev."""
    settings = get_settings()
    database_url = settings.DATABASE_URL

    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
        )

    return create_engine(
        database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )


_engine = _create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables from SQLAlchemy model metadata.

    Alembic is the formal migration mechanism; this function provides a
    convenience shortcut for local development (table creation only, no
    column patching).  In production / Docker, always run ``alembic upgrade head``.
    """
    settings = get_settings()
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Import all models so they register on Base.metadata
    from app.models import compute_node  # noqa: F401
    from app.models import experiment_group  # noqa: F401
    from app.models import job_record  # noqa: F401
    from app.models import provision_plan  # noqa: F401
    from app.models import resource_manifest  # noqa: F401
    from app.models import uploaded_dataset  # noqa: F401
    from app.models import user  # noqa: F401

    Base.metadata.create_all(bind=_engine)
    # Column-level migrations (experiment_group_id, snapshot paths, etc.)
    # are handled by Alembic — run ``alembic upgrade head`` before starting.


def check_db_connection() -> bool:
    """Return True if the database is reachable."""
    try:
        from sqlalchemy import text as _sql_text

        with _engine.connect() as conn:
            conn.execute(_sql_text("SELECT 1"))
        return True
    except Exception:
        return False
