"""换库备份恢复的端到端校验，使用 SQLite 跑通服务与接口。

运行：PYTHONPATH=/workspace/.pylibs:<backend> python -m pytest tests -q
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.config.resources import engine
from src.constants.job_state import RestoreItemState, RestoreState
from src.main import app
from src.models.cases import cases
from src.models.restore import restore_items, restore_jobs
from src.models.reviews import reviews
from src.models.runs import runs
from src.services import restore
from src.services.runs import list_entries as list_run_entries
from src.constants.job_state import JobState


def _parts(code, amount=10000, *, review_batch=None, sign_batch=None,
           review_amount=None, sign_actor="reviewer", note="核对一致"):
    return {
        "batch_no": code,
        "billing": {"batch_no": code, "amount_cents": amount, "vessel": "鹭汊轮"},
        "review": {"batch_no": review_batch or code,
                   "amount_cents": review_amount if review_amount is not None else amount,
                   "parameter": 23, "decision": "通过"},
        "signoff": {"batch_no": sign_batch or code, "actor": sign_actor,
                    "note": note, "amount_cents": amount, "decision": "通过"},
    }


def _bundle():
    missing = _parts("LC-B-1002")
    missing.pop("signoff")
    return {"batches": [
        _parts("LC-B-1001", 10000),
        missing,
        _parts("LC-B-1003", 20000, review_amount=20999),
        _parts("LC-B-1004", 30000, sign_batch="LC-B-9999"),  # 签核串批
    ]}


def test_full_restore_flow_and_states():
    with engine.begin() as db:
        job_id = restore.create_job(db, _bundle(), "operator", "第一次补备份")
    restore.process_job(job_id)

    with engine.connect() as db:
        job = restore.get_job(db, job_id)
        assert job["status"] == RestoreState.DONE
        assert (job["restored_count"], job["suspended_count"], job["skipped_count"]) == (1, 3, 0)
        items = {row["batch_no"]: dict(row) for row in restore.list_items(db, job_id)}

    assert items["LC-B-1001"]["status"] == RestoreItemState.RESTORED
    assert items["LC-B-1002"]["status"] == RestoreItemState.SUSPENDED
    assert "签核" in items["LC-B-1002"]["reason"]
    assert items["LC-B-1003"]["status"] == RestoreItemState.SUSPENDED
    assert "对不上" in items["LC-B-1003"]["reason"]
    assert items["LC-B-1004"]["status"] == RestoreItemState.SUSPENDED
    assert "串批" in items["LC-B-1004"]["reason"]

    with engine.connect() as db:
        run = db.execute(select(runs).where(
            runs.c.id == items["LC-B-1001"]["restored_run_id"])).mappings().one()
        assert run["source"] == "restore"
        assert run["source_batch_no"] == "LC-B-1001"
        assert run["source_amount_cents"] == 10000  # 下载凭据沿用备份原金额
        case = db.execute(select(cases).where(cases.c.id == run["case_id"])).mappings().one()
        assert case["code"] == f"RST-{job_id}-LC-B-1001"  # 撞号也能并存
        assert case["source_batch_no"] == "LC-B-1001"
        review = db.execute(select(reviews).where(reviews.c.run_id == run["id"])).mappings().one()
        assert review["source_batch_no"] == "LC-B-1001"  # 签核不串批

        # 恢复批次进入常规查账列表，且与手工批次并存可查。
        entries = list_run_entries(db)
        restored_entry = next(e for e in entries if e["source_batch_no"] == "LC-B-1001")
        assert restored_entry["status"] == JobState.DONE


def test_batch_number_collision_keeps_both_ledgers():
    collision_code = "LC-COLLIDE-1"
    # 现账里已有一个使用该业务编号的手工资料。
    with engine.begin() as db:
        db.execute(cases.insert().values(
            code=collision_code, title="现账开账批", description="换库后的现账",
            payload={"manual": True}, created_by="operator",
            source="manual", source_batch_no=None))
        manual_before = len(db.execute(select(cases).where(cases.c.code == collision_code)).all())

    with engine.begin() as db:
        job_id = restore.create_job(db, {"batches": [_parts(collision_code, 55000)]}, "operator")
    restore.process_job(job_id)
    with engine.connect() as db:
        # 同一原批次号：手工现账与恢复账两份都查得到，互不覆盖。
        manual = db.execute(select(cases).where(cases.c.code == collision_code)).mappings().all()
        restored = db.execute(select(cases).where(
            cases.c.code == f"RST-{job_id}-{collision_code}")).mappings().all()
        assert len(manual) == manual_before and len(restored) == 1
        same_batch = db.execute(select(cases).where(
            cases.c.source_batch_no == collision_code)).mappings().all()
        assert len(same_batch) == 1  # 恢复账保留原批次号
        # 两份账的执行记录都能查
        run_rows = db.execute(select(runs).join(cases, cases.c.id == runs.c.case_id).where(
            (cases.c.code == collision_code) |
            (cases.c.code == f"RST-{job_id}-{collision_code}"))).mappings().all()
        assert {row["source"] for row in run_rows} <= {"manual", "restore"}


def test_retry_is_idempotent_and_skips_restored():
    with engine.begin() as db:
        before = len(db.execute(select(runs)).all())
        job2 = restore.create_job(db, {"batches": [_parts("LC-B-1001", 10000),
                                                    _parts("LC-B-2001", 5000)]}, "operator")
    restore.process_job(job2)
    with engine.connect() as db:
        job = restore.get_job(db, job2)
        assert job["restored_count"] == 1
        assert job["skipped_count"] == 1
        assert len(db.execute(select(runs)).all()) == before + 1  # 已恢复批次不重复出现
        items = {row["batch_no"]: dict(row) for row in restore.list_items(db, job2)}
        assert items["LC-B-1001"]["status"] == RestoreItemState.SKIPPED
        run_count = len(db.execute(select(runs)).all())

    # 中断重试：把明细退回 pending 并把任务置回运行态。
    with engine.begin() as db:
        target = db.execute(select(restore_items.c.id).where(
            restore_items.c.batch_no == "LC-B-2001")).scalar()
        db.execute(restore_items.update().where(restore_items.c.id == target).values(
            status=RestoreItemState.PENDING))
        db.execute(restore_jobs.update().where(restore_jobs.c.id == job2).values(
            status=RestoreState.RUNNING, finished_at=None))
    restore.process_job(job2)
    with engine.connect() as db:
        job = restore.get_job(db, job2)
        assert job["status"] == RestoreState.DONE
        assert job["attempt"] >= 2
        assert len(db.execute(select(runs)).all()) == run_count  # 重试不重复建账


def test_reconcile_rules():
    from src.domain import restore_bundle

    ok, reasons = restore_bundle.reconcile(
        "X", restore_bundle.validate_bundle({"batches": [_parts("X", 999)]})["X"])
    assert ok and reasons == []
    ok, reasons = restore_bundle.reconcile(
        "X", restore_bundle.validate_bundle(
            {"batches": [_parts("X", 999, sign_actor="  ")]})["X"])
    assert not ok and any("签署人" in r for r in reasons)
    with pytest.raises(ValueError):
        restore_bundle.validate_bundle({"batches": []})
    with pytest.raises(ValueError):
        restore_bundle.validate_bundle({"batches": [{"batch_no": "lowercase"}]})


def test_api_permissions_and_export_credential():
    client = TestClient(app)

    def login(name):
        token = client.post("/auth/login", json={
            "username": name, "password": "Demo-DbWorkbench-2026"}).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    op, viewer = login("operator"), login("viewer")
    assert client.post("/restores", headers=viewer,
                       json={"bundle": _bundle()}).status_code == 403
    assert client.post("/restores", headers=op,
                       json={"bundle": {"batches": [{}]}}).status_code == 422

    resp = client.post("/restores", headers=op, json={"note": "接口提交", "bundle": {
        "batches": [_parts("LC-WEB-1", 42000)]}})
    assert resp.status_code == 202
    job_id = resp.json()["id"]
    assert resp.json()["status"] == RestoreState.QUEUED  # 接口只入队，处理在 worker
    restore.process_job(job_id)  # 模拟 worker 取走任务

    assert any(item["id"] == job_id for item in client.get("/restores", headers=op).json())
    items = client.get(f"/restores/{job_id}/items", headers=op).json()
    assert items[0]["status"] == RestoreItemState.RESTORED
    run_id = items[0]["restored_run_id"]

    again = client.post(f"/restores/{job_id}/retry", headers=op)
    assert again.status_code == 200 and again.json()["status"] == RestoreState.DONE

    exported = client.get(f"/runs/{run_id}/export", headers=op)
    assert exported.status_code == 200
    import json
    payload = json.loads(exported.content)
    assert payload["download_credential"] == {"batch_no": "LC-WEB-1", "amount_cents": 42000}
    assert "filename=evidence-LC-WEB-1.json" in exported.headers["content-disposition"]
    assert client.get("/restores").status_code == 401


def test_worker_picks_up_queued_and_interrupted_jobs(monkeypatch):
    from src.constants.job_state import RestoreState
    from src.worker import _service_restore

    # 任务 1：正常入队，worker 取走并处理完成。
    with engine.begin() as db:
        job_id = restore.create_job(db, {"batches": [_parts("LC-WK-1", 7000)]}, "operator")
    assert _service_restore() is True
    with engine.connect() as db:
        assert restore.get_job(db, job_id)["status"] == RestoreState.DONE
    # 队列已空时返回 False。
    assert _service_restore() is False

    # 任务 2：处理到一半被打断（任务停在 RUNNING，仍有 pending 明细），worker 重新接手。
    with engine.begin() as db:
        interrupted = restore.create_job(
            db, {"batches": [_parts("LC-WK-2", 8000), _parts("LC-WK-3", 9000)]}, "operator")
        db.execute(restore_jobs.update().where(restore_jobs.c.id == interrupted).values(
            status=RestoreState.RUNNING))

    calls = {"n": 0}
    real_rebuild = restore._rebuild_records

    def flaky_rebuild(db, job_id, code, parts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("模拟处理中断")
        return real_rebuild(db, job_id, code, parts)

    monkeypatch.setattr(restore, "_rebuild_records", flaky_rebuild)
    assert _service_restore() is True  # 第一批中断保持 pending，第二批照常恢复
    monkeypatch.undo()
    with engine.connect() as db:
        job = restore.get_job(db, interrupted)
        assert job["status"] == RestoreState.FAILED  # 有未决批次，判失败待重试
        states = {i["batch_no"]: i["status"] for i in restore.list_items(db, interrupted)}
    assert states["LC-WK-2"] == RestoreItemState.PENDING
    assert states["LC-WK-3"] == RestoreItemState.RESTORED

    # 重试：只处理仍 pending 的批次，已恢复的不重复。
    restore.process_job(interrupted)
    with engine.connect() as db:
        job = restore.get_job(db, interrupted)
        assert job["status"] == RestoreState.DONE
        states = {i["batch_no"]: i["status"] for i in restore.list_items(db, interrupted)}
    assert states["LC-WK-2"] == RestoreItemState.RESTORED
    assert states["LC-WK-3"] == RestoreItemState.RESTORED
    assert job["restored_count"] == 2 and job["suspended_count"] == 0

    # 挂起批次（缺件/对不上）补齐材料后可在新任务里恢复，旧任务记录保持不变。
    with engine.begin() as db:
        retry_job = restore.create_job(db, {"batches": [_parts("LC-HOLD-1")]}, "operator")
    with engine.begin() as db:
        for code in ("LC-HOLD-1",):
            db.execute(restore_items.update().where(
                restore_items.c.job_id == retry_job, restore_items.c.batch_no == code
            ).values(status=RestoreItemState.SUSPENDED, reason="演练挂起"))
        db.execute(restore_jobs.update().where(restore_jobs.c.id == retry_job).values(
            status=RestoreState.DONE, suspended_count=1))
    with engine.begin() as db:
        followup = restore.create_job(db, {"batches": [_parts("LC-HOLD-1")]}, "operator")
    restore.process_job(followup)
    with engine.connect() as db:
        assert restore.get_job(db, followup)["restored_count"] == 1
        old = {i["batch_no"]: i["status"] for i in restore.list_items(db, retry_job)}
        assert old["LC-HOLD-1"] == RestoreItemState.SUSPENDED  # 历史挂起记录不被改写
