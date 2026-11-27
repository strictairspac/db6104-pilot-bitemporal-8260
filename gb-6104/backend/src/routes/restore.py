from fastapi import APIRouter, Depends

from src.config.resources import engine
from src.controllers.access import identity, operator
from src.controllers.restore import retry_restore, submit_restore
from src.schemas.restore import RestoreInput, RestoreItemEntry, RestoreJobEntry
from src.services import restore

router = APIRouter(prefix="/restores", tags=["Restore"])


@router.get("", response_model=list[RestoreJobEntry], dependencies=[Depends(identity)])
def list_restore_jobs():
    with engine.connect() as db:
        return restore.list_jobs(db)


@router.post("", response_model=RestoreJobEntry, status_code=202)
def submit(form: RestoreInput, user=Depends(operator)):
    return submit_restore(form, user)


@router.get("/{job_id}", response_model=RestoreJobEntry, dependencies=[Depends(identity)])
def restore_detail(job_id: int):
    with engine.connect() as db:
        return restore.get_job(db, job_id)


@router.get("/{job_id}/items", response_model=list[RestoreItemEntry],
            dependencies=[Depends(identity)])
def restore_items(job_id: int):
    with engine.connect() as db:
        return restore.list_items(db, job_id)


@router.post("/{job_id}/retry", response_model=RestoreJobEntry)
def retry(job_id: int, user=Depends(operator)):
    return retry_restore(job_id, user)
