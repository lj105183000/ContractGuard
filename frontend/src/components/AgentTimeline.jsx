import {
  CheckIcon,
  LoaderIcon,
  XIcon,
} from "./Icons.jsx";
import { formatDuration, formatTime } from "../labels.js";

export default function AgentTimeline({ job }) {
  const events = job?.events || [];
  const latestByNode = new Map();
  events.forEach((event) => latestByNode.set(event.node, event));
  const items = [...latestByNode.values()].sort(
    (left, right) => new Date(left.at).getTime() - new Date(right.at).getTime(),
  );
  const progress = job?.progress ?? 0;
  const isRunning =
    job && ["queued", "running", "resuming"].includes(job.status);
  const isAwaiting = job?.status === "awaiting_review";

  return (
    <section className="card timeline-card">
      <div className="card-heading">
        <div>
          <p className="eyebrow">STEP 02</p>
          <h2>审查进程</h2>
        </div>
        <span className={`progress-number ${job?.status === "failed" ? "is-danger" : ""}`}>
          {progress}%
        </span>
      </div>

      <div className="progress-track" aria-label="审查进度">
        <div
          className={`progress-fill ${job?.status === "failed" ? "is-danger" : ""}`}
          style={{ width: `${Math.max(progress, isRunning ? 4 : 0)}%` }}
        />
      </div>

      {items.length === 0 ? (
        <div className="timeline-empty">
          {isRunning ? <LoaderIcon size={18} /> : null}
          <span>
            {isRunning
              ? "任务处理中，准备解析合同…"
              : isAwaiting
                ? "审查已暂停，等待人工复核。"
                : "上传合同后，这里会显示每个 Agent 的进度。"}
          </span>
        </div>
      ) : (
        <ol className="timeline">
          {items.map((event) => {
            const failed = event.status === "failed";
            const completed = event.status === "completed";
            const skipped = event.status === "skipped";
            return (
              <li
                key={`${event.node}-${event.at}`}
                className={`timeline-item ${failed ? "is-failed" : ""} ${
                  completed ? "is-completed" : ""
                } ${event.node.startsWith("agent_") ? "is-agent" : ""}`}
              >
                <span className="timeline-dot" aria-hidden="true">
                  {failed ? (
                    <XIcon size={13} />
                  ) : completed ? (
                    <CheckIcon size={13} />
                  ) : skipped ? (
                    <span className="dot-dash">–</span>
                  ) : (
                    <LoaderIcon size={13} />
                  )}
                </span>
                <div className="timeline-body">
                  <div className="timeline-title">
                    <strong>{event.label}</strong>
                    <span>{formatTime(event.at)}</span>
                  </div>
                  {event.message ? <p>{event.message}</p> : null}
                  {event.duration_ms ? (
                    <small>耗时 {formatDuration(event.duration_ms)}</small>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {job?.warnings?.length ? (
        <div className="inline-warning">
          <strong>有 {job.warnings.length} 条运行提示</strong>
          <span>{job.warnings[0]}</span>
        </div>
      ) : null}
    </section>
  );
}
