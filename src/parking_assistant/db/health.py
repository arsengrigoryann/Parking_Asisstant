"""PostgreSQL connectivity verification."""

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from parking_assistant.db.session import create_database_engine


def database_is_ready(engine: Engine) -> bool:
    """Return whether the database accepts a trivial query."""
    try:
        with engine.connect() as connection:
            result: object = connection.execute(text("SELECT 1")).scalar_one()
            return result == 1
    except SQLAlchemyError:
        return False


def main() -> None:
    """Verify the configured PostgreSQL connection for developer use."""
    engine = create_database_engine()
    try:
        if not database_is_ready(engine):
            raise SystemExit("PostgreSQL connectivity check failed")
        print("PostgreSQL is ready")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
