from sqlalchemy import JSON, Column, Integer, String, Table
from .base import metadata


cases = Table(
    "cases", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(48), unique=True, nullable=False),
    Column("title", String(120), nullable=False),
    Column("description", String(420), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_by", String(48), nullable=False),
    # 换库恢复来源：手工登记的资料为空；恢复批次记录原批次号，便于撞号时两份账并存可查。
    Column("source", String(16), nullable=False, server_default="manual"),
    Column("source_batch_no", String(30), nullable=True, index=True),
)
