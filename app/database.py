from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def build_engine(url: str) -> Engine:
    """Short lock waits preserve Slack's ACK budget. PostgreSQL uses psycopg."""
    sqlite = url.startswith("sqlite")
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 1}} if sqlite else {
        "connect_args": {"connect_timeout": 1, "options": "-c statement_timeout=1000 -c lock_timeout=1000"},
        "pool_timeout": 1,
    }
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if sqlite:
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=1000")
            cursor.close()
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
