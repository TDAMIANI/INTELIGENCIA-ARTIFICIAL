import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Los tests usan SQLite en memoria salvo que se indique una base explícita.
os.environ.setdefault("MVT_DATABASE_URL", "sqlite+pysqlite:///:memory:")

from mvtwin import models  # noqa: E402,F401
from mvtwin.api.main import app  # noqa: E402
from mvtwin.db import Base, get_session  # noqa: E402
from mvtwin.seed import load_plant_config, seed  # noqa: E402
from mvtwin.settings import settings  # noqa: E402

# Para correr también los tests contra PostgreSQL/TimescaleDB real:
#   MVT_TEST_PG_URL=postgresql+psycopg://mvt:mvt@localhost:5432/mvt_test pytest
PG_URL = os.environ.get("MVT_TEST_PG_URL")


@pytest.fixture(params=["sqlite"] + (["postgresql"] if PG_URL else []))
def session_factory(request) -> Iterator[sessionmaker[Session]]:
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(PG_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        seed(s, load_plant_config(settings.plant_config))
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
