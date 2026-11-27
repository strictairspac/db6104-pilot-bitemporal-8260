"""版本 2：换库备份恢复。

在既有表上增量增加来源字段，并新建恢复任务/明细表。全部用 ADDITIVE 的
ALTER TABLE，库内已有演示数据不受影响；字段带默认值，旧行自动补齐。
"""
from sqlalchemy import inspect, text

from src.models import audit, cases, restore, reviews, runs, users  # noqa: F401 注册表
from src.models.base import metadata

_COLUMN_DEFAULTS = {
    "cases": [
        ("source", "VARCHAR(16) NOT NULL DEFAULT 'manual'"),
        ("source_batch_no", "VARCHAR(30) NULL"),
    ],
    "runs": [
        ("source", "VARCHAR(16) NOT NULL DEFAULT 'manual'"),
        ("restore_job_id", "INT NULL"),
        ("source_batch_no", "VARCHAR(30) NULL"),
        ("source_amount_cents", "BIGINT NULL"),
    ],
    "reviews": [
        ("source_batch_no", "VARCHAR(30) NULL"),
    ],
}

_INDEXES = [
    ("ix_cases_source_batch_no", "cases", "source_batch_no"),
    ("ix_runs_restore_job_id", "runs", "restore_job_id"),
    ("ix_runs_source_batch_no", "runs", "source_batch_no"),
    ("ix_reviews_source_batch_no", "reviews", "source_batch_no"),
]


def upgrade(connection):
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table_name, columns in _COLUMN_DEFAULTS.items():
        present = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, ddl in columns:
            if column_name not in present:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))

    metadata.create_all(connection, tables=[
        metadata.tables["restore_jobs"], metadata.tables["restore_items"],
    ])

    present_indexes = {name for table in existing_tables for name in
                       {index["name"] for index in inspector.get_indexes(table)}}
    for index_name, table_name, column_name in _INDEXES:
        if index_name not in present_indexes:
            connection.execute(text(
                f"CREATE INDEX {index_name} ON {table_name} ({column_name})"
            ))
