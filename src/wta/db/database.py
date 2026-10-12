"""SQLite engine and session factory."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateColumn

from wta.db.models import Base


def _enable_foreign_keys(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _add_missing_columns(engine: Engine) -> list[str]:
    """Bring an existing database up to date with new *nullable* columns.

    `create_all` creates new tables but never changes existing ones. SQLite can add a
    nullable column (with its foreign key) in place, which covers additions like
    run_trace.run_id. Anything bigger needs a real migration.
    """
    added = []
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in inspect(conn).get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable:
                    raise RuntimeError(f"Database needs a migration: {table.name}.{column.name} is not nullable")
                ddl = str(CreateColumn(column).compile(dialect=engine.dialect))
                for fk in column.foreign_keys:
                    ondelete = f" ON DELETE {fk.ondelete}" if fk.ondelete else ""
                    ddl += f" REFERENCES {fk.column.table.name} ({fk.column.name}){ondelete}"
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                added.append(f"{table.name}.{column.name}")
    return added


class Database:
    """Owns the engine for one SQLite file and hands out ORM sessions."""

    def __init__(self, path: Path | str) -> None:
        url = "sqlite://" if str(path) == ":memory:" else f"sqlite:///{Path(path).as_posix()}"
        self.engine: Engine = create_engine(url)
        event.listen(self.engine, "connect", _enable_foreign_keys)
        Base.metadata.create_all(self.engine)
        self.upgraded_columns = _add_missing_columns(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def close(self) -> None:
        self.engine.dispose()
