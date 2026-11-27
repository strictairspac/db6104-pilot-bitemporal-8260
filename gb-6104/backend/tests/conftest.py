import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="workbench-tests-")
_DB_PATH = os.path.join(_TMP, "workbench.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DEMO_PASSWORD", "Demo-DbWorkbench-2026")
os.environ.setdefault("SEED_DEMO", "false")

from scripts.seed_demo import setup_schema  # noqa: E402
from src.config.resources import engine  # noqa: E402
from src.models.users import users  # noqa: E402
from src.utils.passwords import hash_password  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema_and_users():
    setup_schema()
    with engine.begin() as db:
        for role in ("operator", "reviewer", "viewer"):
            db.execute(users.insert().values(
                username=role, role=role,
                password_hash=hash_password("Demo-DbWorkbench-2026")))
    yield
    engine.dispose()
