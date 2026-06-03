import logging
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app import models  # noqa: F401
from app.config import settings

# Import your models and settings
from app.database import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
# Only configure logging via fileConfig if the main Alembic script is running.
# When run programmatically (e.g., during app startup), the application's
# logging configuration should take precedence.
if config.config_file_name is not None:
    # Check if we are running programmatically (e.g. from the app)
    # If the root logger already has handlers, we skip reconfiguration
    if not logging.getLogger().hasHandlers():
        fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def include_object(object, name, type_, reflected, compare_to):
    # Ignore specific indexes that cause spurious differences on SQLite due to unique constraints
    if type_ == "index" and name in {
        "ix_p2p_network_config_psk_hash",
        "ix_p2p_peers_peer_id",
        "ix_remote_shared_file_cache_last_announced_at",
    }:
        return False
    # Ignore type/nullability comparisons on SQLite columns that do not support ALTER TABLE modifications natively
    if type_ == "column" and name in {"backend_type", "operation_mode"} and getattr(object, "table", None) is not None and object.table.name == "cold_storage_locations":
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=False,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Check if a connection was passed programmatically (e.g. from tests)
    connection = config.attributes.get("connection", None)
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=False,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()
        return

    # Overwrite the sqlalchemy.url from settings
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=False,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
