from pydantic import BaseModel, Field, field_validator

from src.constants.job_state import RestoreItemState, RestoreState
from src.utils.serial import canonical_json


class RestoreInput(BaseModel):
    note: str = Field(default="", max_length=500)
    bundle: dict

    @field_validator("note")
    @classmethod
    def clean_note(cls, note):
        return note.strip()

    @field_validator("bundle")
    @classmethod
    def check_size(cls, bundle):
        if len(canonical_json(bundle)) > 1_500_000:
            raise ValueError("备份包不能超过 1.5 MB")
        return bundle


class RestoreJobEntry(BaseModel):
    id: int
    status: RestoreState
    total_batches: int
    restored_count: int
    suspended_count: int
    skipped_count: int
    actor: str
    note: str
    failure: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    attempt: int


class RestoreItemEntry(BaseModel):
    id: int
    job_id: int
    batch_no: str
    status: RestoreItemState
    billing: dict | None
    review: dict | None
    signoff: dict | None
    missing: list[str] | None
    reason: str | None
    restored_case_id: int | None
    restored_run_id: int | None
    created_at: int
    updated_at: int | None
