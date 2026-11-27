from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from src.constants.job_state import JobState
from src.models.reviews import reviews
from . import audit, runs as run_service


def get(db, run_id):
    row = db.execute(select(reviews).where(reviews.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


def sign(db, run_id, data, actor):
    run = run_service.get(db, run_id)
    if run["status"] != JobState.DONE:
        raise HTTPException(409, "批次执行成功后才能签核")
    values = {"run_id": run_id, "note": data.note, "actor": actor, "created_at": int(time())}
    # 手工签核跟随所属批次；恢复批次把原批次号一并落库，签核不跨批。
    if run.get("source") == "restore":
        values["source_batch_no"] = run["source_batch_no"]
    try:
        db.execute(insert(reviews).values(**values))
    except IntegrityError as exc:
        raise HTTPException(409, "该批次已经签核") from exc
    audit.record(db, "review", actor, "run", run_id)
    return get(db, run_id)
