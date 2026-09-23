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


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        seed(s, load_plant_config(settings.plant_config))
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
