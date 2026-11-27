import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { table } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { poll } from "../hooks/poll.js";
import { el } from "../utils/dom.js";
import { time } from "../utils/format.js";
import { RestoreItemStates, RestoreStates } from "../constants/states.js";

const SAMPLE_BUNDLE = JSON.stringify({
  batches: [
    {
      batch_no: "LC-PILOT-1201",
      billing: {batch_no: "LC-PILOT-1201", amount_cents: 96000},
      review: {batch_no: "LC-PILOT-1201", amount_cents: 96000, parameter: 23, decision: "通过"},
      signoff: {batch_no: "LC-PILOT-1201", actor: "reviewer", note: "三样核对一致", amount_cents: 96000, decision: "通过"},
    },
  ],
}, null, 2);

function restoreStatus(value) {
  return el("span", {class: `status state-restore-${value}`, "data-state": value},
    RestoreStates[value] || value);
}

function itemStatus(value) {
  return el("span", {class: `status state-item-${value}`, "data-state": value},
    RestoreItemStates[value] || value);
}

function uploadForm(config, onCreated) {
  const form = el("form", {id: "restore-form"},
    field("交接说明", el("input", {name: "note", maxlength: 500, placeholder: "例如：会计第一次补回的备份"})),
    field("备份包 JSON（批次含 billing / review / signoff 三样）",
      el("textarea", {name: "bundle", spellcheck: "false", required: true, rows: 14}, SAMPLE_BUNDLE),
      "缺件、对不上或签核串批的批次会自动挂起，其余批次照常恢复。"),
    errorText(), el("button", {type: "submit"}, "提交备份恢复"));
  submit(form, async values => {
    let bundle;
    try {
      bundle = JSON.parse(values.get("bundle"));
    } catch {
      throw new Error("备份包 JSON 无法解析，请检查括号、引号和逗号");
    }
    const job = await api.submitRestore({note: values.get("note") || "", bundle});
    onCreated(job.id);
  });
  return form;
}

function jobSummary(job) {
  return el("div", {class: "stack"},
    restoreStatus(job.status),
    el("p", {}, `共 ${job.total_batches} 批 · 已恢复 ${job.restored_count} · 挂起 ${job.suspended_count} · 跳过 ${job.skipped_count}`),
    job.failure ? el("p", {class: "feedback", role: "alert"}, job.failure) : null,
    el("p", {class: "muted"}, `第 ${job.attempt} 次处理 · ${time(job.created_at)}`),
    ["queued", "running", "failed"].includes(job.status)
      ? el("button", {type: "button", onclick: async event => {
          event.target.disabled = true;
          await api.retryRestore(job.id);
          location.hash = `restores/${job.id}`;
        }}, "重试恢复") : null);
}

function itemsTable(items) {
  return table(["原批次号", "状态", "缺件 / 挂起原因", "恢复批次"], items.map(item => [
    item.batch_no,
    itemStatus(item.status),
    item.reason || (item.missing?.length ? `缺少${item.missing.join("、")}` : "—"),
    item.restored_run_id
      ? el("a", {href: `#evidence/${item.restored_run_id}`}, `#${item.restored_run_id}`) : "—",
  ]), "恢复批次明细");
}

export async function restoresPage(config, user, route, signal) {
  const jobs = await api.restores();
  const selectedId = route.id ? Number(route.id) : jobs[0]?.id;
  const selected = selectedId ? await api.restore(selectedId) : null;
  const items = selectedId ? await api.restoreItems(selectedId) : [];
  const isOperator = user.role === "operator";

  function refreshTo(id) {
    location.hash = `restores/${id}`;
  }

  async function refresh() {
    if (!selectedId || signal.aborted) return false;
    const next = await api.restore(selectedId);
    if (!signal.aborted) {
      const badge = document.querySelector("#restore-job-state");
      if (badge) badge.replaceWith(el("span", {id: "restore-job-state"}, restoreStatus(next.status)));
    }
    return ["queued", "running"].includes(next.status);
  }
  if (selected && ["queued", "running"].includes(selected.status)) poll(refresh, signal);

  const jobList = jobs.length ? table(
    ["任务", "状态", "批次", "恢复/挂起/跳过", "提交时间"],
    jobs.map(job => [
      el("a", {href: `#restores/${job.id}`, "aria-current": job.id === selectedId ? "true" : null}, `#${job.id}`),
      el("span", {id: job.id === selectedId ? "restore-job-state" : null}, restoreStatus(job.status)),
      job.total_batches, `${job.restored_count}/${job.suspended_count}/${job.skipped_count}`, time(job.created_at),
    ]), "恢复任务") : empty("还没有恢复任务，会计补回备份后在这里提交。");

  return el("div", {class: "page"},
    pageHeading("备份恢复查账", "三样材料（开账资料、复核结果、签核）对得上才恢复，缺件或对不上先挂起。"),
    el("div", {class: "split"},
      section("恢复任务", jobList),
      isOperator ? section("补回备份", uploadForm(config, refreshTo)) :
        section("补回备份", empty("仅资料操作员可提交备份。"))),
    selected ? section(`任务 #${selected.id} 批次明细`, jobSummary(selected), itemsTable(items)) :
      section("批次明细", empty("选择一个恢复任务查看每一批的核对结果。")));
}
