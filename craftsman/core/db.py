from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from craftsman.core.config import get_settings

_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> sessionmaker:
    get_engine()
    return _SessionLocal


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create the pgvector extension and all tables directly from the models.

    This is the fast dev/test/demo path (used by tests, seed_demo, e2e_demo). The
    production schema path is Alembic migrations via run_migrations() — the API applies
    those on startup. Do not use init_db() to build a schema you intend to migrate later.
    """
    from craftsman.core import models

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    models.Base.metadata.create_all(engine)


def run_migrations() -> None:
    """Bring the database up to the latest schema by running Alembic migrations.

    This is the production schema path: idempotent, records applied revisions, and is
    safe to call on every startup. Single-node deployments can run it inline (the API
    does, in its lifespan); at multi-replica scale you would move this to a one-shot
    migrate job so replicas don't race.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    root = Path(__file__).resolve().parents[2]  # repo root (contains alembic.ini)
    cfg = Config(str(root / "alembic.ini"))

    # Guard the "existing schema, no Alembic stamp" case (a DB built by create_all before
    # migrations existed). Blindly upgrading re-creates existing tables → a cryptic
    # DuplicateTable. Fail with an actionable message instead.
    engine = get_engine()
    with engine.connect() as conn:
        insp = inspect(conn)
        if not insp.has_table("alembic_version") and insp.has_table("campaigns"):
            raise RuntimeError(
                "Database has tables but no Alembic version stamp — it was built by "
                "create_all (dev/demo), not migrations, so migrations can't run against "
                "it. For a dev/demo DB, drop and recreate it (the data is regenerable) and "
                "retry. If you are certain its schema already matches the latest models, "
                "adopt it with:  alembic stamp head"
            )

    # env.py reads the URL from Settings, so we don't set it here.
    command.upgrade(cfg, "head")
