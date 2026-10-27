"""
copy_sqlite_to_postgres.py — Move your existing data from the SQLite file to Supabase
------------------------------------------------------------------------------------
Run it once, after DATABASE_URL in Backend/.env points to the Supabase database:

    cd Backend
    python -m scripts.copy_sqlite_to_postgres --source data/leads.db

It first brings the Supabase database up to date (the migrations), then copies every table in
dependency order and fixes the id counters so new rows get new ids. By default it refuses to run
if the target already holds leads, so nothing is overwritten by accident; --merge copies anyway,
updating rows that have the same id.

The SQLite file must be up to date: start the backend once on SQLite first if it is older.
"""

import argparse
import os
import sys
from typing import Callable

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, IndustryProfile, Lead  # noqa: E402


def _classes_by_table() -> dict:
    return {mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers}


def _reset_sequences(dst: Session) -> None:
    """After copying explicit ids, PostgreSQL's counters must start after the highest one."""
    for table in Base.metadata.sorted_tables:
        pk = list(table.primary_key.columns)
        if len(pk) != 1 or not pk[0].autoincrement or pk[0].type.python_type is not int:
            continue
        name, col = table.name, pk[0].name
        dst.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{name}', '{col}'), "
            f"COALESCE((SELECT MAX({col}) FROM {name}), 1), (SELECT MAX({col}) FROM {name}) IS NOT NULL)"
        ))


def copy_database(source_url: str, target: Engine, merge: bool = False, batch: int = 500,
                  log: Callable[[str], None] = print) -> dict[str, int]:
    """Copies every table from the source database into `target`. Returns {table: rows copied}."""
    source = create_engine(source_url)
    if "leads" not in inspect(source).get_table_names():
        raise ValueError("The source database has no leads table. Is the path right?")

    classes = _classes_by_table()
    copied: dict[str, int] = {}

    with Session(source) as src, Session(target) as dst:
        if not merge:
            if dst.query(Lead).first() is not None:
                raise ValueError("The target database already contains leads. Use --merge to copy anyway.")
            dst.query(IndustryProfile).delete()       # only the default profile seeded at start-up
            dst.commit()

        for table in Base.metadata.sorted_tables:
            cls = classes.get(table.name)
            if cls is None:
                continue
            columns = [attr.key for attr in cls.__mapper__.column_attrs]
            count = 0
            for row in src.query(cls).yield_per(batch):
                dst.merge(cls(**{key: getattr(row, key) for key in columns}))
                count += 1
                if count % batch == 0:
                    dst.commit()
            dst.commit()
            copied[table.name] = count
            log(f"  {table.name}: {count} row(s)")

        if target.dialect.name == "postgresql":
            _reset_sequences(dst)
            dst.commit()

    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy the SQLite database into the DATABASE_URL database")
    parser.add_argument("--source", default="data/leads.db", help="SQLite file to copy from")
    parser.add_argument("--merge", action="store_true", help="copy even if the target already holds leads")
    args = parser.parse_args()

    import database
    if database.engine.dialect.name != "postgresql":
        sys.exit("DATABASE_URL must point to PostgreSQL (Supabase) for this copy. See the README.")
    if not os.path.isfile(args.source):
        sys.exit(f"Source file not found: {args.source}")

    print("Preparing the target database (migrations)...")
    database.init_db()
    print(f"Copying {args.source} -> {database.engine.url.render_as_string(hide_password=True)}")
    try:
        copy_database(f"sqlite:///{os.path.abspath(args.source)}", database.engine, merge=args.merge)
    except ValueError as e:
        sys.exit(str(e))
    print("Done.")


if __name__ == "__main__":
    main()
