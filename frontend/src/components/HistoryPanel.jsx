import { useCallback, useEffect, useRef, useState } from "react";
import { desktopBridge } from "../desktop.js";
import { formatDateTime } from "../labels.js";
import { useFocusTrap } from "../useFocusTrap.js";
import { FolderIcon, HistoryIcon, RefreshIcon, TrashIcon, XIcon } from "./Icons.jsx";

export default function HistoryPanel({ onClose, onOpen }) {
  const dialogRef = useRef(null);
  useFocusTrap(dialogRef);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [autoSave, setAutoSave] = useState(
    () =>
      window.localStorage.getItem("contractguard.autoSaveHistory") !== "0",
  );

  const refresh = useCallback(async () => {
    if (!desktopBridge) {
      setError("当前不在桌面端运行，无法读取本地历史。");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const data = await desktopBridge.history.list();
      setEntries(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || "无法读取历史记录。");
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
    if (!desktopBridge || busyId) return;
    setBusyId(jobId);
    setError("");
    try {
      const entry = await desktopBridge.history.get(jobId);
      if (!entry?.job) {
        setError("这条历史记录不存在或已损坏。");
        setBusyId("");
        return;
      }
      onOpen(entry);
    } catch (err) {
      setError(err.message || "无法打开历史记录。");
      setBusyId("");
    }
  };

  const removeEntry = async (event, jobId) => {
    event.stopPropagation();
    if (!desktopBridge) return;
    if (!window.confirm("删除这条历史记录？此操作不可恢复。")) return;
    try {
      await desktopBridge.history.remove(jobId);
      await refresh();
    } catch (err) {
      setError(err.message || "删除失败。");
    }
  };

  const clearAll = async () => {
    if (!desktopBridge || entries.length === 0) return;
    if (!window.confirm(`清空全部 ${entries.length} 条历史记录？此操作不可恢复。`)) return;
    try {
      await desktopBridge.history.clear();
      await refresh();
    } catch (err) {
      setError(err.message || "清空失败。");
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
        aria-label="本地审查历史"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="drawer-head">
          <div>
            <p className="eyebrow">LOCAL HISTORY</p>
            <h2>本地审查历史</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <XIcon size={20} />
          </button>
        </header>

        <div className="history-toolbar">
          <span className="hint">
            仅保存在本机，最多 30 条、单条 20MB、总计约 200MB；历史快照为只读。
          </span>
          <div className="history-toolbar-actions">
            <label className="history-autosave">
              <input
                type="checkbox"
                checked={autoSave}
                onChange={(event) => {
                  const next = event.target.checked;
                  setAutoSave(next);
                  window.localStorage.setItem(
                    "contractguard.autoSaveHistory",
                    next ? "1" : "0",
                  );
                }}
              />
              自动保存
            </label>
            <button
              type="button"
              className="btn btn-secondary btn-compact"
              onClick={() => desktopBridge?.history.openFolder()}
            >
              <FolderIcon size={15} />
              打开目录
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-compact"
              disabled={entries.length === 0}
              onClick={clearAll}
            >
              <TrashIcon size={15} />
              清空
            </button>
          </div>
        </div>

        <div className="drawer-scroll history-scroll">
          {loading ? (
            <div className="history-state">
              <RefreshIcon size={22} />
              <p>正在读取历史记录…</p>
            </div>
          ) : null}

          {!loading && error ? (
            <div className="history-state is-error">
              <p>{error}</p>
              <button type="button" className="btn btn-secondary btn-compact" onClick={refresh}>
                重试
              </button>
            </div>
          ) : null}

          {!loading && !error && entries.length === 0 ? (
            <div className="history-state">
              <HistoryIcon size={26} />
              <p>还没有历史记录。</p>
              <span className="hint">完成一次合同审查后会自动保存到这里。</span>
            </div>
          ) : null}

          {!loading && !error && entries.length > 0 ? (
            <ul className="history-list">
              {entries.map((entry) => {
                const summary = entry.summary || {};
                const busy = busyId === entry.jobId;
                return (
                  <li key={entry.jobId} className="history-item">
                    <button
                      type="button"
                      className="history-open"
                      disabled={Boolean(busyId)}
                      onClick={() => openEntry(entry.jobId)}
                    >
                      <span className="history-item-head">
                        <strong>{entry.filename || "未命名合同"}</strong>
                        <span
                          className={`mode-badge ${
                            entry.mode === "live" ? "is-live" : "is-mock"
                          }`}
                        >
                          {entry.mode === "live" ? "LIVE" : "MOCK"}
                        </span>
                      </span>
                      <span className="history-item-meta">
                        <span>{formatDateTime(entry.savedAt)}</span>
                        <span>共 {summary.total ?? 0} 条风险</span>
                        <span className="history-high">高 {summary.high ?? 0}</span>
                        <span>中 {summary.medium ?? 0}</span>
                        <span>低 {summary.low ?? 0}</span>
                      </span>
                      <span className="history-item-action">
                        {busy ? "正在打开…" : "查看快照 →"}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="icon-button history-delete"
                      aria-label={`删除 ${entry.filename || entry.jobId}`}
                      onClick={(event) => removeEntry(event, entry.jobId)}
                    >
                      <TrashIcon size={16} />
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
