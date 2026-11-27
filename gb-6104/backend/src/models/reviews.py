from sqlalchemy import BigInteger, Column, ForeignKey, String, Table
from .base import metadata


reviews = Table(
    "reviews", metadata,
    Column("run_id", ForeignKey("runs.id"), primary_key=True),
    Column("actor", String(48), nullable=False),
    Column("note", String(500), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    # 签核必须落在所属批次上：恢复签核回指备份原批次号，串批在恢复核对时即被拦下。
    Column("source_batch_no", String(30), nullable=True, index=True),
)
