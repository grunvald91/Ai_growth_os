import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


@event.listens_for(Engine, "connect")
def _sqlite_unicode_search(dbapi_connection, _record):
    """Register a read-only, unicode-aware search helper on SQLite only.

    SQLite's built-in lower()/LIKE cannot casefold Cyrillic. Namespaced
    function avoids changing existing database collation or other queries.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.create_function(
            "sg_casefold", 1,
            lambda value: value.casefold() if isinstance(value, str) else None,
            deterministic=True,
        )


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
