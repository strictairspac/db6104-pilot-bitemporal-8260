"""回归校验：v1 老库升级到 v2 的增量迁移，以及原有手工链路不受影响。

迁移测试用独立 SQLite 文件和原始 v1 DDL 造一份老库，再跑 v2 升级，
避免与主测试库的元数据状态互相干扰。
"""
import os
import tempfile

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.pool import StaticPool

from migrations.v002_restore import upgrade as upgrade_v2
from src.config.resources import engine as main_engine
from src.constants.job_state import JobState
from src.main import app
from src.models.restore import restore_items, restore_jobs
from src.models.runs import runs
from fastapi.testclient import TestClient


# 与 v001 原始表结构保持一致的最小 DDL（新增字段出现之前的形态）。
_V1_DDL = [
    """CREATE TABLE users (
        username VARCHAR(48) PRIMARY KEY, role VARCHAR(16) NOT NULL,
        password_hash VARCHAR(180) NOT NULL)""",
    """CREATE TABLE cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code VARCHAR(48) UNIQUE NOT NULL,
        title VARCHAR(120) NOT NULL, description VARCHAR(420) NOT NULL,
        payload JSON NOT NULL, created_by VARCHAR(48) NOT NULL)""",
    """CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES cases(id),
        status VARCHAR(16) NOT NULL, parameter INTEGER NOT NULL, actor VARCHAR(48) NOT NULL,
        created_at BIGINT NOT NULL, finished_at BIGINT, result JSON,
        failure VARCHAR(320), digest VARCHAR(64))""",
    """CREATE TABLE reviews (
        run_id INTEGER PRIMARY KEY REFERENCES runs(id), actor VARCHAR(48) NOT NULL,
        note VARCHAR(500) NOT NULL, created_at BIGINT NOT NULL)""",
    """CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action VARCHAR(32) NOT NULL,
        actor VARCHAR(48) NOT NULL, entity VARCHAR(24) NOT NULL,
        entity_id INTEGER NOT NULL, created_at BIGINT NOT NULL)""",
]


def test_v1_database_upgrades_additively():
    legacy_engine = create_engine(
        f"sqlite:///{tempfile.mkdtemp()}/legacy.db",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with legacy_engine.begin() as db:
        for ddl in _V1_DDL:
            db.execute(text(ddl))
        db.execute(text("INSERT INTO cases (code, title, description, payload, created_by) "
                        "VALUES ('CASE-OLD', '老库资料', '换库前数据', '{}', 'seed')"))
        before = {c["name"] for c in inspect(db).get_columns("runs")}
        assert "source_batch_no" not in before
        assert "restore_jobs" not in inspect(db).get_table_names()

    with legacy_engine.begin() as db:
        upgrade_v2(db)
    inspector = inspect(legacy_engine)
    runs_cols = {c["name"] for c in inspector.get_columns("runs")}
    assert {"source", "restore_job_id", "source_batch_no", "source_amount_cents"} <= runs_cols
    cases_cols = {c["name"] for c in inspector.get_columns("cases")}
    assert {"source", "source_batch_no"} <= cases_cols
    assert {"restore_jobs", "restore_items"} <= set(inspector.get_table_names())
    index_names = {idx["name"] for tbl in ("cases", "runs", "reviews")
                   for idx in inspector.get_indexes(tbl)}
    assert {"ix_cases_source_batch_no", "ix_runs_restore_job_id",
            "ix_runs_source_batch_no", "ix_reviews_source_batch_no"} <= index_names

    # 老数据自动补齐默认来源，仍可查。
    with legacy_engine.connect() as db:
        row = db.execute(text("SELECT code, source FROM cases")).one()
        assert row == ("CASE-OLD", "manual")

    # 迁移幂等：再执行一次不报错。
    with legacy_engine.begin() as db:
        upgrade_v2(db)
    legacy_engine.dispose()


def test_legacy_manual_flow_still_works():
    from src.domain.scenario import FIXTURES
    from src.models.cases import cases as cases_table
    with main_engine.begin() as db:
        # 演示数据在 MySQL 上由 seed() 载入；SQLite 测试库直接插入等价资料。
        for fixture in FIXTURES:
            exists = db.execute(select(cases_table.c.id).where(
                cases_table.c.code == fixture["code"])).first()
            if exists is None:
                db.execute(cases_table.insert().values(**fixture, created_by="seed"))

    client = TestClient(app)
    token = client.post("/auth/login", json={
        "username": "operator", "password": "Demo-DbWorkbench-2026"}).json()["token"]
    op = {"Authorization": f"Bearer {token}"}

    case_list = client.get("/cases", headers=op).json()
    assert case_list and case_list[0]["source"] == "manual"
    case_id = case_list[0]["id"]
    queued = client.post(f"/cases/{case_id}/runs", headers=op, json={"parameter": 23})
    assert queued.status_code == 202
    run_id = queued.json()["id"]
    assert queued.json()["source"] == "manual"

    # 业务复算用的是 MySQL 临时表 DDL，SQLite 下作业进入失败态（生产为 MySQL）；
    # 这里按真实 worker 的方式捕获 JobFailure 并落库，验证队列与失败链路完好。
    from src.services.execution import JobFailure, fail, service_one
    while True:
        try:
            if not service_one():
                break
        except JobFailure as exc:
            fail(exc)
    detail = client.get(f"/runs/{run_id}", headers=op).json()
    assert detail["status"] in (JobState.DONE, JobState.FAILED)
    assert detail["source_amount_cents"] is None

    import time
    with main_engine.begin() as db:
        result = db.execute(runs.insert().values(
            case_id=case_id, status=JobState.DONE, parameter=23, actor="operator",
            created_at=int(time.time()), finished_at=int(time.time()),
            result={"summary": "回归用成功批次"}, digest="0" * 64, source="manual"))
        signed_run_id = result.inserted_primary_key[0]

    rtoken = client.post("/auth/login", json={
        "username": "reviewer", "password": "Demo-DbWorkbench-2026"}).json()["token"]
    signed = client.post(f"/runs/{signed_run_id}/review",
                         headers={"Authorization": f"Bearer {rtoken}"},
                         json={"note": "复核无误"})
    assert signed.status_code == 201 and signed.json()["source_batch_no"] is None

    exported = client.get(f"/runs/{signed_run_id}/export", headers=op).json()
    assert "download_credential" not in exported
    assert exported["review"]["note"] == "复核无误"

    assert client.get("/restores", headers=op).json() == []
    with main_engine.connect() as db:
        assert db.execute(select(restore_jobs)).all() == []
        assert db.execute(select(restore_items)).all() == []
        assert db.execute(select(runs.c.source).where(
            runs.c.id == signed_run_id)).scalar() == "manual"
