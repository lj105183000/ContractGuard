import { DownloadIcon, LockIcon } from "./Icons.jsx";

export default function ReportActions({
  summary,
  jobStatus,
  onExport,
  busy,
  onShowHigh,
  readOnly = false,
}) {
  const pending = summary?.blocking_high_pending ?? summary?.high_pending ?? 0;
  const advisory = summary?.advisory_high_pending ?? 0;
  const blocked = pending > 0;
  const finalizing = ["awaiting_review", "resuming"].includes(jobStatus);
  const disabled = busy || blocked || readOnly || finalizing;

  return (
    <section className={`report-actions ${blocked ? "is-blocked" : ""}`}>
      <div className="report-copy">
        <p className="eyebrow">STEP 04</p>
        <h2>导出审查报告</h2>
        {readOnly ? (
          <p className="gate-message">
            <LockIcon size={16} />
            历史快照为只读，请重新上传合同后再导出报告。
          </p>
        ) : blocked ? (
          <p className="gate-message">
            <LockIcon size={16} />
            还有 <strong>{pending}</strong> 条已核实高风险未复核。
            <button type="button" className="text-button" onClick={onShowHigh}>
              去处理
            </button>
          </p>
        ) : finalizing ? (
          <p className="gate-message">
            <LockIcon size={16} />
            复核尚未完成，请稍候再导出。
          </p>
        ) : (
          <p className="gate-message is-ready">
            已核实高风险已全部复核，可导出 Word / Markdown / JSON。
            {advisory > 0
              ? `另有 ${advisory} 条模型建议高风险待核实，不阻断导出。`
              : ""}
          </p>
        )}
        <p className="hint">
          报告包含摘要、风险清单、原文证据、法条附录、企业规则与人工意见；
          已采纳建议可在「修订稿」Tab 生成新合同 DOCX。
        </p>
      </div>
      <div className="export-buttons">
        <button
          type="button"
          className="btn btn-primary"
          disabled={disabled}
          onClick={() => onExport("docx")}
        >
          <DownloadIcon size={17} />
          Word 报告
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={disabled}
          onClick={() => onExport("md")}
        >
          Markdown
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={disabled}
          onClick={() => onExport("json")}
        >
          JSON
        </button>
      </div>
    </section>
  );
}
