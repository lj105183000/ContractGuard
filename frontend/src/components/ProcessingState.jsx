import { LoaderIcon } from "./Icons.jsx";
import { formatTime } from "../labels.js";

const AGENT_NODES = [
  { key: "agent_commercial", label: "商务 / 付款 / 交付" },
  { key: "agent_liability", label: "违约 / 赔偿 / 解除" },
  { key: "agent_compliance", label: "争议 / 合规 / 知识产权" },
];

export default function ProcessingState({ job, onCancel, busy = false }) {
  const events = job?.events || [];
  const latest = [...events].reverse()[0];
  const statusByNode = new Map();
  events.forEach((event) => statusByNode.set(event.node, event.status));

  return (
    <div className="processing-state">
      <div className="processing-hero card">
        <div className="processing-icon">
          <LoaderIcon size={30} />
        </div>
        <div>
          <p className="eyebrow">REVIEW IN PROGRESS</p>
          <h1>正在审查合同</h1>
          <p className="processing-filename">{job?.filename || "合同文件"}</p>
          <p className="processing-stage">
            {latest ? `${latest.label}：${latest.message || "处理中…"}` : "任务排队中…"}
            {latest ? <span> · {formatTime(latest.at)}</span> : null}
          </p>
        </div>
        <div className="processing-progress">
          <strong>{job?.progress ?? 0}%</strong>
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{ width: `${Math.max(job?.progress ?? 0, 4)}%` }}
            />
          </div>
          {job?.job_id && onCancel ? (
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busy}
              onClick={() => onCancel(job.job_id)}
            >
              取消任务
            </button>
          ) : null}
        </div>
      </div>

      <div className="agent-status-grid">
        {AGENT_NODES.map((agent, index) => {
          const status = statusByNode.get(agent.key);
          const done = status === "completed";
          const failed = status === "failed";
          return (
            <div
              key={agent.key}
              className={`agent-status ${done ? "is-done" : ""} ${failed ? "is-failed" : ""}`}
            >
              <span className="agent-index">{String(index + 1).padStart(2, "0")}</span>
              <div>
                <strong>{agent.label}</strong>
                <small>
                  {done
                    ? "已完成"
                    : failed
                      ? "调用失败，继续其他分支"
                      : status
                        ? "并行审查中"
                        : "等待中"}
                </small>
              </div>
              <span className={`agent-dot ${done ? "is-done" : failed ? "is-failed" : ""}`} />
            </div>
          );
        })}
      </div>

      <div className="skeleton-card">
        <div className="skeleton-line short" />
        <div className="skeleton-line" />
        <div className="skeleton-line" />
        <div className="skeleton-line medium" />
      </div>
      <p className="processing-hint">
        规则引擎与 3 个 Agent 正在并行工作。通常需要几秒到半分钟；扫描件 OCR 可能需要数十秒。
      </p>
    </div>
  );
}
