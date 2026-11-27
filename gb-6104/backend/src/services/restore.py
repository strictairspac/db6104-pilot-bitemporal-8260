"""备份恢复服务：接收会计分次补回的备份，逐批核对后恢复查账。

设计要点：
- 恢复以任务（restore_jobs）+ 批次明细（restore_items）持久化，处理过程可中断、可重试。
- 每个批次独立事务：单个批次挂起不影响其他批次照常恢复。
- 已恢复批次有终态保护，重试时直接跳过，不会重复建账、重复出现。
- 旧批次号与现账撞号时，恢复资料用新内部编号入库但保留原批次号，两份账都查得到。
- 恢复出来的执行批次把备份原批次号和原金额写在凭据字段里，下载证据沿用这两项。
"""
import logging
from time import time

from fastapi import HTTPException
from sqlalchemy import and_, select

from src.config.resources import engine
from src.constants.job_state import JobState, RestoreItemState, RestoreState
from src.domain import restore_bundle
from src.models.cases import cases
from src.models.restore import restore_items, restore_jobs
from src.models.reviews import reviews
from src.models.runs import runs
from src.services import audit
from src.utils.serial import content_digest

logger = logging.getLogger(__name__)


# 任务登记 ----------------------------------------------------------------------

def create_job(db, bundle, actor, note=""):
    """登记一个恢复任务并落库全部批次明细，初始为挂起态待处理。"""
    normalized = restore_bundle.validate_bundle(bundle)
    now = int(time())
    result = db.execute(restore_jobs.insert().values(
        status=RestoreState.QUEUED, total_batches=len(normalized),
        restored_count=0, suspended_count=0, skipped_count=0,
        actor=actor, note=note[:500], created_at=now, attempt=0,
    ))
    job_id = result.inserted_primary_key[0]
    for code, parts in normalized.items():
        db.execute(restore_items.insert().values(
            job_id=job_id, batch_no=code, status=RestoreItemState.PENDING,
            billing=parts["billing"], review=parts["review"], signoff=parts["signoff"],
            missing=parts["missing"] or None, reason=None,
            restored_case_id=None, restored_run_id=None,
            created_at=now, updated_at=None,
        ))
    audit.record(db, "restore_create", actor, "restore_job", job_id)
    return job_id


# 任务处理 ----------------------------------------------------------------------

def process_job(job_id):
    """处理一个恢复任务；可被 worker 或接口重试调用，全程幂等。"""
    with engine.begin() as db:
        job = db.execute(
            select(restore_jobs).where(restore_jobs.c.id == job_id).with_for_update()
        ).mappings().first()
        if job is None:
            raise HTTPException(404, "恢复任务不存在")
        if job["status"] == RestoreState.DONE:
            return job_id
        db.execute(restore_jobs.update().where(restore_jobs.c.id == job_id).values(
            status=RestoreState.RUNNING, started_at=job["started_at"] or int(time()),
            attempt=job["attempt"] + 1, failure=None,
        ))
        pending = db.execute(
            select(restore_items)
            .where(and_(restore_items.c.job_id == job_id,
                        restore_items.c.status == RestoreItemState.PENDING))
            .order_by(restore_items.c.id)
            .with_for_update(skip_locked=True)
        ).mappings().all()

    restored = suspended = skipped = 0
    interrupted = False
    for item in pending:
        try:
            outcome = _restore_one(job_id, item)
        except Exception:
            # 单批次意外出错：独立事务记下原因但保持 pending，任务可重试；
            # 已回滚的本批不影响后续批次照常恢复。
            logger.exception("恢复批次 %s 中断", item["batch_no"])
            _mark_interrupted(item["id"])
            interrupted = True
            continue
        if outcome[0] == RestoreItemState.RESTORED:
            restored += 1
        elif outcome[0] == RestoreItemState.SKIPPED:
            skipped += 1
        else:
            suspended += 1

    with engine.begin() as db:
        job = db.execute(
            select(restore_jobs).where(restore_jobs.c.id == job_id).with_for_update()
        ).mappings().first()
        items = db.execute(
            select(restore_items.c.status).where(restore_items.c.job_id == job_id)
        ).scalars().all()
        still_pending = any(state == RestoreItemState.PENDING for state in items)
        counts = {
            RestoreItemState.RESTORED: items.count(RestoreItemState.RESTORED),
            RestoreItemState.SUSPENDED: items.count(RestoreItemState.SUSPENDED),
            RestoreItemState.SKIPPED: items.count(RestoreItemState.SKIPPED),
        }
        if still_pending:
            # 有批次处理中断：任务判失败并保留 pending 明细，等待显式重试；
            # 进程崩溃则会停在 running，由 worker 重新接手。
            new_status = RestoreState.FAILED if interrupted else RestoreState.RUNNING
            failure = "部分批次处理中断，可重试恢复" if interrupted else "恢复中断，等待重试"
        else:
            new_status, failure = RestoreState.DONE, None
        db.execute(restore_jobs.update().where(restore_jobs.c.id == job_id).values(
            status=new_status, failure=failure, finished_at=int(time()),
            restored_count=counts[RestoreItemState.RESTORED],
            suspended_count=counts[RestoreItemState.SUSPENDED],
            skipped_count=counts[RestoreItemState.SKIPPED],
        ))
        audit.record(db, "restore_finish", job["actor"], "restore_job", job_id)
    return job_id


def _mark_interrupted(item_id):
    """在独立事务里登记中断原因，明细保持 pending 以便重试。"""
    with engine.begin() as db:
        db.execute(restore_items.update().where(
            restore_items.c.id == item_id,
            restore_items.c.status == RestoreItemState.PENDING,
        ).values(reason="系统处理中断，重试任务后继续", updated_at=int(time())))


def _restore_one(job_id, item):
    """恢复单个批次，独立事务，返回 (终态, 原因或 None)。"""
    item_id = item["id"]
    code = item["batch_no"]
    with engine.begin() as db:
        locked = db.execute(
            select(restore_items).where(restore_items.c.id == item_id).with_for_update()
        ).mappings().first()
        if locked is None:
            return RestoreItemState.SKIPPED, None
        if locked["status"] != RestoreItemState.PENDING:
            # 终态保护：已恢复/已挂起的明细不在重试中重复处理。
            return RestoreItemState.SKIPPED, None

        # 同一批次号此前已在任意任务里恢复过：不重复建账，标记跳过。
        existing = db.execute(
            select(restore_items.c.id).where(and_(
                restore_items.c.batch_no == code,
                restore_items.c.status == RestoreItemState.RESTORED,
                restore_items.c.id != locked["id"],
            )).limit(1)
        ).first()
        if existing is not None:
            db.execute(_item_update(item_id, RestoreItemState.SKIPPED,
                                    f"批次 {code} 已在先前备份中恢复，本次跳过"))
            audit.record(db, "restore_skip", "worker", "restore_item", item_id)
            return RestoreItemState.SKIPPED, None

        # 中断保护：明细此前已经建过账（任务被重复处理），直接认定已恢复，不重复建账。
        if locked["restored_run_id"] is not None:
            db.execute(_item_update(item_id, RestoreItemState.RESTORED, None))
            return RestoreItemState.RESTORED, None

        parts = {"billing": locked["billing"], "review": locked["review"],
                 "signoff": locked["signoff"], "missing": locked["missing"] or []}
        ok, reasons = restore_bundle.reconcile(code, parts)
        if not ok:
            db.execute(_item_update(item_id, RestoreItemState.SUSPENDED, "；".join(reasons)))
            audit.record(db, "restore_suspend", "worker", "restore_item", item_id)
            return RestoreItemState.SUSPENDED, reasons

        case_id, run_id = _rebuild_records(db, job_id, code, parts)
        db.execute(restore_items.update().where(restore_items.c.id == item_id).values(
            status=RestoreItemState.RESTORED, reason=None,
            restored_case_id=case_id, restored_run_id=run_id, updated_at=int(time()),
        ))
        audit.record(db, "restore_restore", "worker", "restore_item", item_id)
        return RestoreItemState.RESTORED, None


def _rebuild_records(db, job_id, code, parts):
    """三样核对通过后，把备份重建为可查账的资料、执行批次和签核。"""
    billing, review, signoff = parts["billing"], parts["review"], parts["signoff"]
    amount = billing["amount_cents"]
    now = int(time())

    case_payload = {
        "restored": True,
        "batch_no": code,
        "billing": billing,
        "review": review,
        "download_credential": {"batch_no": code, "amount_cents": amount},
    }
    # 撞号保护：业务唯一编号加任务后缀，原批次号保存在 source_batch_no，两份账并存。
    internal_code = f"RST-{job_id}-{code}"
    case_result = db.execute(cases.insert().values(
        code=internal_code, title=f"{code} 恢复开账批",
        description=f"换库恢复自备份，原批次号 {code}，原金额 {amount} 分。",
        payload=case_payload, created_by="restore",
        source="restore", source_batch_no=code,
    ))
    case_id = case_result.inserted_primary_key[0]

    result = {
        "status": JobState.DONE,
        "summary": f"备份批次 {code} 三样核对一致，已恢复查账。",
        "metrics": review.get("metrics") or {"原金额（分）": amount},
        "rows": review.get("rows") or [],
        "source_batch_no": code,
        "source_amount_cents": amount,
    }
    run_result = db.execute(runs.insert().values(
        case_id=case_id, status=JobState.DONE,
        parameter=_safe_parameter(review),
        actor="restore", created_at=now, finished_at=now,
        result=result, failure=None, digest=content_digest(result),
        source="restore", restore_job_id=job_id,
        source_batch_no=code, source_amount_cents=amount,
    ))
    run_id = run_result.inserted_primary_key[0]

    # 签核只挂在本次恢复出的批次上，source_batch_no 与批次一致，杜绝串批。
    db.execute(reviews.insert().values(
        run_id=run_id, actor=str(signoff["actor"]).strip(),
        note=str(signoff["note"]).strip(), created_at=now,
        source_batch_no=code,
    ))
    audit.record(db, "restore_run", "restore", "run", run_id)
    return case_id, run_id


def _item_update(item_id, status, reason):
    return restore_items.update().where(restore_items.c.id == item_id).values(
        status=status, reason=reason, updated_at=int(time()),
    )


def _safe_parameter(review):
    parameter = review.get("parameter", 1)
    return parameter if type(parameter) is int and 1 <= parameter <= 180 else 1


# 查询 -------------------------------------------------------------------------

def list_jobs(db):
    rows = db.execute(select(restore_jobs).order_by(restore_jobs.c.id.desc()).limit(100)).mappings()
    return [dict(row) for row in rows]


def get_job(db, job_id):
    job = db.execute(select(restore_jobs).where(restore_jobs.c.id == job_id)).mappings().first()
    if job is None:
        raise HTTPException(404, "恢复任务不存在")
    return dict(job)


def list_items(db, job_id):
    get_job(db, job_id)
    rows = db.execute(
        select(restore_items).where(restore_items.c.job_id == job_id)
        .order_by(restore_items.c.id)
    ).mappings()
    return [dict(row) for row in rows]
