import { useCallback, useEffect, useRef, useState } from "react";
import { getReview, listReviews } from "../api.js";
import { JOB_STATUS_LABELS, formatDateTime } from "../labels.js";
import { useFocusTrap } from "../useFocusTrap.js";
import { HistoryIcon, RefreshIcon, XIcon } from "./Icons.jsx";

export default function ServerHistoryPanel({ onClose, onOpen }) {
  const dialogRef = useRef(null);
  useFocusTrap(dialogRef);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await listReviews({ limit: 50 });
      setEntries(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || "无法读取任务列表。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const handler = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const openEntry = async (jobId) => {
    if (busyId) return;
    setBusyId(jobId);
    setError("");
    try {
      const job = await getReview(jobId);
      onOpen(job);
    } catch (err) {
      setError(err.message || "无法打开该任务。");
      setBusyId("");
    }
  };

  return (
    <div className="drawer-overlay" onMouseDown={onClose}>
      <aside
        ref={dialogRef}
        tabIndex={-1}
        className="drawer history-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="服务端审查历史"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="drawer-head">
          <div>
            <p className="eyebrow">SERVER HISTORY</p>
            <h2>审查历史</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="关闭"
          >
            <XIcon size={20} />
          </button>
        </header>

        <div className="history-toolbar">
          <span className="hint">
            任务保存在后端 SQLite，默认保留 7 天；等待人工复核的任务会保留，进行中的任务在服务重启后会标记为中断。
          </span>
          <div className="history-toolbar-actions">
            <button
              type="button"
              className="btn btn-secondary btn-compact"
              onClick={refresh}
            >
              <RefreshIcon size={15} />
              刷新
            </button>
          </div>
        </div>

        <div className="drawer-scroll history-scroll">
          {loading ? (
            <div className="history-state">
              <RefreshIcon size={22} />
              <p>正在读取任务列表…</p>
            </div>
          ) : null}

          {!loading && error ? (
            <div className="history-state is-error">
              <p>{error}</p>
              <button
                type="button"
                className="btn btn-secondary btn-compact"
                onClick={refresh}
              >
                重试
              </button>
            </div>
          ) : null}

          {!loading && !error && entries.length === 0 ? (
            <div className="history-state">
              <HistoryIcon size={26} />
              <p>还没有审查任务。</p>
              <span className="hint">完成一次合同审查后会出现在这里。</span>
            </div>
          ) : null}

          {!loading && !error && entries.length > 0 ? (
            <ul className="history-list">
              {entries.map((entry) => {
                const summary = entry.summary || {};
                const busy = busyId === entry.job_id;
                return (
                  <li key={entry.job_id} className="history-item">
                    <button
                      type="button"
                      className="history-open"
                      disabled={Boolean(busyId)}
                      onClick={() => openEntry(entry.job_id)}
                    >
                      <span className="history-item-head">
                        <strong>{entry.filename || "未命名合同"}</strong>
                        <span className={`job-status job-status-${entry.status}`}>
                          {JOB_STATUS_LABELS[entry.status] || entry.status}
                        </span>
                      </span>
                      <span className="history-item-meta">
                        <span>{formatDateTime(entry.updated_at)}</span>
                        <span>进度 {entry.progress ?? 0}%</span>
                        <span>共 {summary.total ?? 0} 条风险</span>
                        <span className="history-high">高 {summary.high ?? 0}</span>
                        <span>中 {summary.medium ?? 0}</span>
                      </span>
                      <span className="history-item-action">
                        {busy ? "正在打开…" : "查看任务 →"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
