import { useEffect, useMemo, useState } from "react";
import { getRevisedContract } from "../api.js";
import { lineDiff } from "../diff.js";
import { DownloadIcon } from "./Icons.jsx";
import { SEVERITY_LABELS } from "../labels.js";

const COMPLETED = new Set(["completed", "completed_with_warnings"]);
const CHANGE_LABELS = {
  replace: "自动替换",
  annotate: "原位批注",
  append: "补充条款",
};

export default function RevisedContractView({
  job,
  readOnly = false,
  onShowHigh,
  onExportRevised,
  busy = false,
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  const jobId = job?.job_id;
  const status = job?.status;
  const summary = job?.result?.summary;
  const blocking = summary?.blocking_high_pending ?? 0;
  const accepted = summary?.accepted ?? 0;
  const canGenerate =
    !readOnly && Boolean(jobId) && COMPLETED.has(status) && blocking === 0;

  const decisionSignature = useMemo(
    () =>
      (job?.result?.findings || [])
        .map((finding) => `${finding.id}:${finding.status}:${finding.review_note}`)
        .join("|"),
    [job?.result?.findings],
  );

  useEffect(() => {
    if (!canGenerate) {
      setData(null);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    getRevisedContract(jobId)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err) => {
        if (!cancelled) {
          setData(null);
          setError(err.message || "修订稿生成失败。");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [canGenerate, jobId, status, decisionSignature]);

  if (readOnly) {
    return (
      <div className="preview-empty card">
        <strong>历史快照不支持生成修订稿</strong>
        <span>请重新上传合同并完成复核后再生成修订稿。</span>
      </div>
    );
  }

  if (blocking > 0) {
    return (
      <div className="preview-empty card">
        <strong>还有 {blocking} 条已核实高风险未复核</strong>
        <span>已核实高风险全部处理完、任务完成后才能生成修订稿。</span>
        {onShowHigh ? (
          <button type="button" className="text-button" onClick={onShowHigh}>
            去处理
          </button>
        ) : null}
      </div>
    );
  }

  if (!canGenerate) {
    return (
      <div className="preview-empty card">
        <strong>复核完成后可生成修订稿</strong>
        <span>当前任务还未完成，修订稿只应用已完成复核的采纳建议。</span>
      </div>
    );
  }

  if (loading && !data) {
    return <div className="preview-empty card">正在生成合同修订稿…</div>;
  }

  if (error) {
    return (
      <div className="preview-empty card preview-error">
        <strong>修订稿生成失败</strong>
        <span>{error}</span>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="revised-view">
      <div
        className={`preview-banner ${
          data.accepted_count ? "is-ready" : "is-blocked"
        }`}
      >
        {data.accepted_count ? (
          <span>
            已按 <strong>{data.accepted_count}</strong> 条已采纳建议生成修订稿：
            替换 {data.replaced_count}、标注 {data.annotated_count}、补充{" "}
            {data.appended_count}、跳过 {data.skipped_count}。
          </span>
        ) : (
          <span>
            还没有已采纳的建议，当前修订稿与原文一致（已采纳 {accepted} 条）。
          </span>
        )}
      </div>

      {data.mode !== "live" ? (
        <div className="mock-banner">
          <strong>MOCK 演示模式</strong>
          <span>
            模板建议不会直接改写原文，而是以【修订建议】标注在对应条款后；
            配置模型 Key 并切换 live 后，模型返回的完整条款才可自动替换。
          </span>
        </div>
      ) : null}

      <div className="revised-stats">
        <span className="revised-stat">
          已采纳<strong>{data.accepted_count}</strong>
        </span>
        <span className="revised-stat">
          自动替换<strong>{data.replaced_count}</strong>
        </span>
        <span className="revised-stat">
          原位批注<strong>{data.annotated_count}</strong>
        </span>
        <span className="revised-stat">
          补充条款<strong>{data.appended_count}</strong>
        </span>
        <span className="revised-stat">
          跳过<strong>{data.skipped_count}</strong>
        </span>
      </div>

      {data.warnings?.length ? (
        <details className="warnings-box">
          <summary>生成提示（{data.warnings.length}）</summary>
          <ul>
            {data.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="revised-layout">
        <section className="card revised-changes">
          <div className="card-heading">
            <div>
              <p className="eyebrow">CHANGES</p>
              <h2>修订变更清单</h2>
            </div>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setShowDiff((visible) => !visible)}
            >
              {showDiff ? "关闭对比" : "并排对比"}
            </button>
          </div>
          {data.changes?.length ? (
            <div className="revised-change-list">
              {data.changes.map((change, index) => (
                <article
                  className="revised-change-card"
                  key={`${change.finding_id}-${change.change_type}-${index}`}
                >
                  <header>
                    <span className={`change-badge change-${change.change_type}`}>
                      {CHANGE_LABELS[change.change_type] || change.change_type}
                    </span>
                    <span className={`severity-pill severity-${change.severity}`}>
                      {SEVERITY_LABELS[change.severity] || change.severity}
                    </span>
                    <strong>{change.clause_article || "合同级"}</strong>
                    <span>{change.title}</span>
                  </header>
                  {change.reason ? (
                    <p className="hint">{change.reason}</p>
                  ) : null}
                  {showDiff ? (
                    change.change_type === "replace" ? (
                      <div className="revised-diff" aria-label="修订差异">
                        {lineDiff(change.original_text, change.revised_text).map(
                          (row, rowIndex) => (
                            <div
                              className={`revised-diff-row diff-${row.type}`}
                              key={`${index}-${rowIndex}`}
                            >
                              <span>
                                {row.type === "add"
                                  ? "+"
                                  : row.type === "del"
                                    ? "−"
                                    : " "}
                              </span>
                              <code>{row.text || " "}</code>
                            </div>
                          ),
                        )}
                      </div>
                    ) : (
                      <div className="revised-compare-grid">
                        <div>
                          <strong>原条款</strong>
                          <pre className="revised-compare">
                            {change.original_text || "（新增内容）"}
                          </pre>
                        </div>
                        <div>
                          <strong>修订后 / 建议</strong>
                          <pre className="revised-compare is-revised">
                            {change.revised_text}
                          </pre>
                        </div>
                      </div>
                    )
                  ) : (
                    <details>
                      <summary>查看原文 / 修订后</summary>
                      {change.original_text ? (
                        <pre className="revised-compare">{change.original_text}</pre>
                      ) : null}
                      <pre className="revised-compare is-revised">
                        {change.revised_text}
                      </pre>
                    </details>
                  )}
                  {change.review_note ? (
                    <p className="revised-note">人工意见：{change.review_note}</p>
                  ) : null}
                </article>
              ))}
            </div>
          ) : (
            <p className="hint">没有已采纳的修改，修订稿与原文一致。</p>
          )}
        </section>

        <section className="card revised-document">
          <div className="card-heading">
            <div>
              <p className="eyebrow">REVISED CONTRACT</p>
              <h2>修订后合同全文</h2>
            </div>
            <div className="export-buttons">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => onExportRevised?.("docx")}
              >
                <DownloadIcon size={17} />
                下载修订稿 DOCX
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => onExportRevised?.("md")}
              >
                Markdown
              </button>
            </div>
          </div>
          <pre className="revised-text">{data.text}</pre>
        </section>
      </div>
    </div>
  );
}
