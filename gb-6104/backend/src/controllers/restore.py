from fastapi import HTTPException
from redis.exceptions import RedisError

from src.config.resources import cache, engine
from src.constants.job_state import RestoreState
from src.services import restore


def submit_restore(data, user):
    with engine.begin() as db:
        try:
            job_id = restore.create_job(db, data.bundle, user["username"], data.note)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    try:
        cache.lpush("workbench:restore-wake", str(job_id))
        cache.ltrim("workbench:restore-wake", 0, 99)
    except RedisError:
        # 数据库是持久队列，worker 也会轮询；通知失败不影响恢复。
        pass
    with engine.connect() as db:
        return restore.get_job(db, job_id)


def retry_restore(job_id, user):
    with engine.begin() as db:
        job = restore.get_job(db, job_id)
        if job["status"] == RestoreState.DONE:
            return job
    # 处理在独立连接里跑，避免长事务持锁；中断后重试只处理仍是 pending 的明细。
    restore.process_job(job_id)
    try:
        cache.lpush("workbench:restore-wake", str(job_id))
    except RedisError:
        pass
    with engine.connect() as db:
        return restore.get_job(db, job_id)
