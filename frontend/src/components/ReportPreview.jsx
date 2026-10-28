import { useEffect, useState } from "react";
import { getReportPreview } from "../api.js";
import MarkdownLite from "./MarkdownLite.jsx";

export default function ReportPreview({ job, readOnly = false }) {
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const jobId = job?.job_id;
  const accepted = job?.result?.summary?.accepted ?? 0;
  const rejected = job?.result?.summary?.rejected ?? 0;

  useEffect(() => {
    if (readOnly || !jobId) return undefined;
    let cancelled = false;
    setLoading(true);
    setError("");
    getReportPreview(jobId)
      .then((data) => {
        if (!cancelled) setPreview(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "报告预览加载失败。");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, accepted, rejected, readOnly]);

  if (readOnly) {
    return (
      <div className="preview-empty card">
        <strong>历史快照不支持重新生成报告</strong>
        <span>请重新上传合同并完成复核后再预览报告。</span>
      </div>
    );
  }

  if (loading && !preview) {
    return <div className="preview-empty card">正在生成报告预览…</div>;
  }

  if (error) {
    return (
      <div className="preview-empty card preview-error">
        <strong>报告预览加载失败</strong>
        <span>{error}</span>
      </div>
    );
  }

  if (!preview) return null;

  return (
    <div className="report-preview">
      <div
        className={`preview-banner ${
          preview.pending_high ? "is-blocked" : "is-ready"
        }`}
      >
        {preview.pending_high ? (
          <span>
            还有 <strong>{preview.pending_high}</strong> 条已核实高风险未复核，
            导出仍被阻止。
            {preview.advisory_high
              ? `另有 ${preview.advisory_high} 条模型建议高风险待核实（不阻断导出）。`
              : ""}
          </span>
        ) : (
          <span>
            已核实高风险已全部复核，可以导出。
            {preview.advisory_high
              ? `另有 ${preview.advisory_high} 条模型建议高风险待核实（不阻断导出）。`
              : ""}
          </span>
        )}
      </div>
      <div className="preview-paper">
        <MarkdownLite markdown={preview.markdown} />
      </div>
    </div>
  );
}
