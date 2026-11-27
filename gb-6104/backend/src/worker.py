import logging
import time

from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from src.config.resources import cache, engine
from src.constants.job_state import RestoreState
from src.models.restore import restore_jobs
from src.services.execution import JobFailure, fail, service_one
from src.services.restore import process_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")


def check():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    heartbeat = cache.get("workbench:heartbeat")
    return heartbeat is not None and time.time() - float(heartbeat) < 90


def _service_restore():
    """取一个未完成的恢复任务继续处理；完成的任务不再出现，天然支持重试去重。"""
    with engine.begin() as db:
        job = db.execute(
            select(restore_jobs.c.id)
            .where(restore_jobs.c.status.in_((RestoreState.QUEUED, RestoreState.RUNNING)))
            .order_by(restore_jobs.c.id).limit(1).with_for_update(skip_locked=True)
        ).first()
    if job is None:
        return False
    try:
        process_job(job[0])
    except Exception:
        logger.exception("Restore job %s failed", job[0])
        with engine.begin() as db:
            db.execute(restore_jobs.update().where(restore_jobs.c.id == job[0]).values(
                status=RestoreState.FAILED,
                failure="恢复任务处理失败，可稍后重试",
            ))
    return True


def work():
    while True:
        try:
            cache.set("workbench:heartbeat", time.time(), ex=90)
            try:
                if service_one():
                    continue
                if _service_restore():
                    continue
            except JobFailure as exc:
                logger.exception("Execution %s failed", exc.run_id)
                fail(exc)
                continue
            cache.blpop(["workbench:wake", "workbench:restore-wake"], timeout=1)
        except (RedisError, SQLAlchemyError):
            logger.exception("Worker connection unavailable")
            time.sleep(2)


if __name__ == "__main__":
    work()
