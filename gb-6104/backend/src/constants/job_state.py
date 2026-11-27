from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"


class RestoreState(StrEnum):
    QUEUED = "queued"          # 恢复任务已登记，等待处理
    RUNNING = "running"        # 正在逐批核对恢复
    DONE = "done"              # 任务处理完毕（可能含挂起批次）
    FAILED = "failed"          # 任务整体失败，可重试


class RestoreItemState(StrEnum):
    PENDING = "pending"        # 等待本次处理
    RESTORED = "restored"      # 三样齐备且核对一致，已恢复查账
    SUSPENDED = "suspended"    # 缺件或对不上，挂起
    SKIPPED = "skipped"        # 本任务此前已恢复，重复提交时跳过
