"""SQLAlchemy engine and transactional session lifecycle."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from parking_assistant.config import Settings, get_settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Build an engine without opening a connection prematurely."""
    resolved_settings = settings or get_settings()
    return create_engine(
        resolved_settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the application's explicit session factory."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit successful work and roll back failed work."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
