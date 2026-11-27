from sqlalchemy import JSON, BigInteger, Column, Integer, String, Table, Text
from .base import metadata


restore_jobs = Table(
    "restore_jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("status", String(16), nullable=False, index=True),
    Column("total_batches", Integer, nullable=False),
    Column("restored_count", Integer, nullable=False, server_default="0"),
    Column("suspended_count", Integer, nullable=False, server_default="0"),
    Column("skipped_count", Integer, nullable=False, server_default="0"),
    Column("actor", String(48), nullable=False),
    Column("note", String(500), nullable=False, server_default=""),
    Column("failure", String(320), nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("started_at", BigInteger, nullable=True),
    Column("finished_at", BigInteger, nullable=True),
    Column("attempt", Integer, nullable=False, server_default="0"),
)


restore_items = Table(
    "restore_items", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Integer, nullable=False, index=True),
    Column("batch_no", String(30), nullable=False),
    Column("status", String(16), nullable=False, index=True),
    Column("billing", JSON(none_as_null=True), nullable=True),
    Column("review", JSON(none_as_null=True), nullable=True),
    Column("signoff", JSON(none_as_null=True), nullable=True),
    Column("missing", JSON(none_as_null=True), nullable=True),
    Column("reason", Text, nullable=True),
    Column("restored_case_id", Integer, nullable=True),
    Column("restored_run_id", Integer, nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=True),
)
