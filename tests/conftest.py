"""Shared fixtures. Tests run against a SEPARATE database (TEST_DATABASE_URL) and
temporary data/model directories, so they never touch the working dataset."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="insightpulse-tests-"))
_TEST_DB = os.environ.get("TEST_DATABASE_URL",
                          "postgresql+psycopg://insightpulse:insightpulse@localhost:5432/insightpulse_test")
os.environ.update({
    "DATABASE_URL": _TEST_DB, "DATA_DIR": str(_TMP / "data"), "MODEL_DIR": str(_TMP / "models"),
    "EVALUATION_ACCESS_TOKEN": "test-eval-token", "DATA_SCALE": "starter", "LOG_LEVEL": "WARNING",
})

import pytest  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()
EVAL_TOKEN = "test-eval-token"


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _TMP


@pytest.fixture(scope="session")
def starter_data(tmp_root: Path) -> dict:
    from generator.generate_data import generate

    return generate("starter", 20261022, tmp_root / "data")


@pytest.fixture(scope="session")
def engine(starter_data):
    """Fresh schema + full pipeline run on the starter dataset (session-scoped)."""
    from app.core.database import get_engine
    from app.services.pipeline_service import run_pipeline
    from scripts.setup_db import setup_database

    setup_database(_TEST_DB, reset=True)
    eng = get_engine()
    summary = run_pipeline(eng, scale="starter", triggered_by="pytest")
    assert summary["status"] == "SUCCEEDED"
    eng.pipeline_summary = summary  # type: ignore[attr-defined]
    return eng


@pytest.fixture(scope="session")
def client(engine):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
