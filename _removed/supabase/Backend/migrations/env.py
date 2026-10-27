"""
Alembic environment. Uses the same engine as the app (database.py), so the address, SSL and
pool settings are identical. A caller can pass its own connection with
config.attributes["connection"] (the tests do).
"""

from alembic import context

from database import Base, engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Writes the SQL instead of running it: alembic upgrade head --sql"""
    context.configure(url=str(engine.url), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
    else:
        with engine.connect() as connection:
            _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
