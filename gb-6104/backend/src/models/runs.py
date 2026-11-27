from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    Column("parameter", Integer, nullable=False),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("finished_at", BigInteger, nullable=True),
    Column("result", JSON(none_as_null=True), nullable=True),
    Column("failure", String(320), nullable=True),
    Column("digest", String(64), nullable=True),
    # 恢复批次回指恢复任务与明细，并保留备份里的原批次号和原金额作为下载凭据。
    Column("source", String(16), nullable=False, server_default="manual"),
    Column("restore_job_id", Integer, nullable=True, index=True),
    Column("source_batch_no", String(30), nullable=True, index=True),
    Column("source_amount_cents", BigInteger, nullable=True),
)
