import { useMemo, useState } from "react";
import { JOB_STATUS_LABELS, formatDateTime } from "../labels.js";

const VIEWABLE_STATUSES = new Set([
  "completed",
  "completed_with_warnings",
  "awaiting_review",
]);
const COMPLETED_STATUSES = new Set(["completed", "completed_with_warnings"]);
const PROCESSING_STATUSES = new Set(["queued", "running", "resuming"]);

const FILTERS = [
  { key: "all", label: "全部" },
  { key: "processing", label: "处理中" },
  { key: "awaiting_review", label: "待复核" },
  { key: "completed", label: "已完成" },
  { key: "failed", label: "失败" },
];

function matchesFilter(item, filter) {
  if (filter === "all") return true;
  if (filter === "processing") return PROCESSING_STATUSES.has(item.status);
  if (filter === "completed") return COMPLETED_STATUSES.has(item.status);
  if (filter === "failed") {
    return item.status === "failed" || item.status === "cancelled";
  }
  return item.status === filter;
}

export default function BatchPanel({
  batch,
  busy = false,
  onOpen,
  onClear,
  onDecide,
  onExport,
  onRefresh,
}) {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const items = batch?.items || [];

  const stats = useMemo(() => {
    const counts = { completed: 0, awaiting: 0, processing: 0, failed: 0 };
    let risks = 0;
    let high = 0;
    let blocking = 0;
    let exportAllowed = items.length > 0;
    for (const item of items) {
      const summary = item.summary || item.job?.result?.summary;
      risks += summary?.total ?? 0;
      high += summary?.high ?? 0;
      blocking += summary?.blocking_high_pending ?? 0;
      if (
        !COMPLETED_STATUSES.has(item.status) ||
        (summary?.blocking_high_pending ?? 0) > 0
      ) {
        exportAllowed = false;
      }
      if (COMPLETED_STATUSES.has(item.status)) counts.completed += 1;
      else if (item.status === "awaiting_review") counts.awaiting += 1;
      else if (item.status === "failed") counts.failed += 1;
      else counts.processing += 1;
    }
    return { ...counts, risks, high, blocking, exportAllowed };
  }, [items]);

  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return items.filter((item) => {
      if (!matchesFilter(item, filter)) return false;
      if (!keyword) return true;
      return (item.filename || "").toLowerCase().includes(keyword);
    });
  }, [items, filter, query]);

  const canBatchDecide = stats.awaiting > 0 && stats.blocking > 0;

  return (
    <section className="batch-panel">
      <div className="batch-head">
        <div>
          <p className="eyebrow">BATCH REVIEW</p>
          <h1>批量审查</h1>
          <p className="batch-sub">
            批次 {batch?.id ? batch.id.slice(0, 8) : "—"} · 共 {items.length} 份
            · 已完成 {stats.completed} · 待复核 {stats.awaiting} · 处理中{" "}
            {stats.processing} · 失败 {stats.failed}
          </p>
          <p className="batch-sub">
            风险 {stats.risks}（高 {stats.high}）；已核实高风险待复核{" "}
            {stats.blocking}
          </p>
        </div>
        <div className="batch-head-actions">
          <button type="button" className="btn btn-secondary btn-compact" onClick={onRefresh}>
            刷新
          </button>
          <button type="button" className="btn btn-secondary" onClick={onClear}>
            清空列表
          </button>
        </div>
      </div>

      <div className="batch-toolbar">
        <div className="status-tabs" role="tablist" aria-label="批次筛选">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={filter === item.key}
              className={`status-tab ${filter === item.key ? "is-active" : ""}`}
              onClick={() => setFilter(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <input
          className="findings-search"
          type="search"
          value={query}
          placeholder="搜索文件名…"
          aria-label="搜索批次合同"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      <div className="batch-actions">
        <div className="batch-action-group">
          <span className="batch-action-label">批量复核</span>
          <button
            type="button"
            className="btn btn-success btn-compact"
            disabled={busy || !canBatchDecide}
            onClick={() => onDecide?.("accepted")}
          >
            采纳全部已核实高风险
          </button>
          <button
            type="button"
            className="btn btn-outline-danger btn-compact"
            disabled={busy || !canBatchDecide}
            onClick={() => onDecide?.("rejected")}
          >
            忽略全部已核实高风险
          </button>
        </div>
        <div className="batch-action-group">
          <span className="batch-action-label">批次报告</span>
          <button
            type="button"
            className="btn btn-primary btn-compact"
            disabled={busy || !stats.exportAllowed}
            onClick={() => onExport?.("docx")}
          >
            Word
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-compact"
            disabled={busy || !stats.exportAllowed}
            onClick={() => onExport?.("md")}
          >
            Markdown
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-compact"
            disabled={busy || !stats.exportAllowed}
            onClick={() => onExport?.("json")}
          >
            JSON
          </button>
        </div>
      </div>
      {!stats.exportAllowed ? (
        <p className="hint">
          批次内仍有未完成/待复核/失败合同，全部完成且已核实高风险处理完才能导出批次报告。
        </p>
      ) : null}

      <ul className="batch-list">
        {visible.length === 0 ? (
          <li className="batch-empty-state">没有匹配的合同。</li>
        ) : null}
        {visible.map((item) => {
          const summary = item.summary || item.job?.result?.summary;
          const viewable = VIEWABLE_STATUSES.has(item.status);
          const actionLabel =
            item.status === "awaiting_review"
              ? "去复核"
              : item.status === "failed"
                ? "已失败"
                : item.status === "cancelled"
                  ? "已取消"
                  : viewable
                    ? "查看结果"
                    : item.status === "resuming"
                      ? "完成中…"
                      : "审查中…";
          const progress = item.progress ?? item.job?.progress ?? 0;
          return (
            <li key={item.key} className={`batch-item batch-${item.status}`}>
              <div className="batch-item-main">
                <div className="batch-item-title">
                  <strong>{item.filename}</strong>
                  <span className={`job-status job-status-${item.status}`}>
                    {JOB_STATUS_LABELS[item.status] || item.status}
                  </span>
                </div>
                {item.error ? <span className="batch-error">{item.error}</span> : null}
                <div className="batch-meta">
                  <span>进度 {progress}%</span>
                  {summary ? (
                    <>
                      <span>风险 {summary.total}</span>
                      <span className="history-high">高 {summary.high}</span>
                      <span>待复核 {summary.pending_review}</span>
                      <span>已核实高风险待复核 {summary.blocking_high_pending}</span>
                    </>
                  ) : null}
                  {item.job?.updated_at ? (
                    <span>{formatDateTime(item.job.updated_at)}</span>
                  ) : null}
                </div>
                <div className="batch-progress">
                  <span style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
                </div>
              </div>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={!viewable}
                onClick={() => onOpen(item)}
              >
                {actionLabel}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
