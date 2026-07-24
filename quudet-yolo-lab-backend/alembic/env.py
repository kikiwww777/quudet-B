"""Alembic environment configuration — loads settings from app.config."""

from logging.config import fileConfig

from alembic import context

from app.config import get_settings
from app.database import Base

# Alembic Config object
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all models so they register on Base.metadata
from app.models import compute_node  # noqa: E402, F401
from app.models import experiment_group  # noqa: E402, F401
from app.models import job_record  # noqa: E402, F401
from app.models import provision_plan  # noqa: E402, F401
from app.models import resource_manifest  # noqa: E402, F401
from app.models import uploaded_dataset  # noqa: E402, F401
from app.models import user  # noqa: E402, F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine,
    without an actual database connection.
    """
    settings = get_settings()
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connect to the live DB."""
    from app.database import _engine

    connectable = _engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
