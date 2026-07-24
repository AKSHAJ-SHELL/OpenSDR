"""Shared test fixtures.

Integration fixtures use a real Postgres + pgvector (mock LLM, no real SMTP).
Set TEST_DATABASE_URL to point elsewhere; tests skip if the DB is unreachable.
"""

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://craftsman:craftsman@localhost:5432/craftsman_test",
)


def _ensure_test_db():
    admin_url = TEST_DB_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname='craftsman_test'")
            ).scalar()
            if not exists:
                conn.execute(text("CREATE DATABASE craftsman_test"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def engine():
    if not _ensure_test_db():
        pytest.skip("Postgres not reachable — skipping integration tests")
    eng = create_engine(TEST_DB_URL)
    from craftsman.core.models import Base, Org
    from craftsman.core.tenancy import DEFAULT_ORG_ID, unscoped_context

    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    # M5.1: the default org — the db fixture enters its context, so every
    # pre-tenancy test keeps working unchanged (exactly the migration-backfill
    # world a single-tenant self-hoster lives in)
    with unscoped_context():
        s = sessionmaker(bind=eng)()
        s.add(Org(id=DEFAULT_ORG_ID, name="Default", slug="default"))
        s.commit()
        s.close()
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    from craftsman.core.tenancy import DEFAULT_ORG_ID, org_context

    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()
    with org_context(DEFAULT_ORG_ID):
        yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(autouse=True)
def _default_org_context():
    """Every test runs inside the default org (M5.1) — the exact world the 0016
    backfill leaves a single-tenant self-hoster in. Cross-tenant tests enter
    other orgs (or tenancy.no_org_context) explicitly; nesting overrides this.
    Threads and the TestClient portal do NOT inherit it — worker threads enter
    their own context like real Celery tasks, and API requests get theirs from
    the auth dependency, which is the production path under test."""
    from craftsman.core.tenancy import DEFAULT_ORG_ID, org_context

    with org_context(DEFAULT_ORG_ID):
        yield


@pytest.fixture()
def default_org_ctx(_default_org_context):
    """Alias kept for tests that name the dependency explicitly."""
    yield


@pytest.fixture()
def client(db):
    """FastAPI TestClient wired to the test session.

    Instantiated without the `with` block so the app lifespan (which would call
    init_db against the real database) never runs — the `engine` fixture already
    built the schema on the test DB.
    """
    from fastapi.testclient import TestClient

    from craftsman.api.app import app
    from craftsman.api.deps import get_db

    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def make_key(db):
    """Factory: mint an API key with the given scopes and return its plaintext token."""
    from craftsman.api.auth import generate_token, hash_token, key_prefix
    from craftsman.core.models import ApiKey

    def _make(*scopes: str, name: str = "test", revoked: bool = False) -> str:
        token = generate_token()
        db.add(
            ApiKey(
                name=name,
                key_prefix=key_prefix(token),
                key_hash=hash_token(token),
                scopes=list(scopes),
                revoked_at=datetime.now(timezone.utc) if revoked else None,
            )
        )
        db.flush()
        return token

    return _make
