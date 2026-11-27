export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
});

export const RestoreStates = Object.freeze({
  queued: "等待恢复", running: "恢复中", done: "恢复完成", failed: "恢复失败",
});

export const RestoreItemStates = Object.freeze({
  pending: "待处理", restored: "已恢复", suspended: "已挂起", skipped: "已跳过",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  restore_create: "提交备份", restore_finish: "恢复完成", restore_restore: "批次恢复",
  restore_suspend: "批次挂起", restore_skip: "重复跳过", restore_run: "恢复建账",
});
