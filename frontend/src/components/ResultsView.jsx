import ContractViewer from "./ContractViewer.jsx";
import FindingsList from "./FindingsList.jsx";
import ReportActions from "./ReportActions.jsx";
import ReportPreview from "./ReportPreview.jsx";
import RevisedContractView from "./RevisedContractView.jsx";
import SummaryCards from "./SummaryCards.jsx";
import { RefreshIcon } from "./Icons.jsx";
import {
  EMBEDDING_MODE_LABELS,
  RERANK_MODE_LABELS,
  formatTime,
} from "../labels.js";

const VIEWS = [
  { key: "findings", label: "风险清单" },
  { key: "contract", label: "合同全文" },
  { key: "revised", label: "修订稿" },
  { key: "preview", label: "报告预览" },
];

export default function ResultsView({
  job,
  filter,
  onFilter,
  selectedId,
  onSelect,
  onExport,
  onExportRevised,
  onShowHigh,
  onReset,
  onResume,
  onCancel,
  activeView = "findings",
  onViewChange,
  locateTarget,
  busy,
  readOnly = false,
}) {
  const result = job?.result;
  if (!result) return null;
  const onTabKeyDown = (event, index) => {
    const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % VIEWS.length;
    if (event.key === "ArrowLeft") next = (index - 1 + VIEWS.length) % VIEWS.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = VIEWS.length - 1;
    onViewChange?.(VIEWS[next].key);
    event.currentTarget.parentElement?.children[next]?.focus();
  };
  const findings =
    filter === "all"
      ? result.findings
      : result.findings.filter((finding) => finding.severity === filter);

  return (
    <div className="results-view">
      <div className="results-head">
        <div>
          <p className="eyebrow">REVIEW REPORT</p>
          <h1>{result.contract.title || result.contract.filename}</h1>
          <div className="results-meta">
            <span>{result.contract.source_type?.toUpperCase()}</span>
            <span>{result.contract.clause_count} 条条款</span>
            <span>
              {result.contract.page_count ? `${result.contract.page_count} 页` : "无分页信息"}
            </span>
            <span className={`mode-dot ${result.mode === "live" ? "is-live" : ""}`}>
              {result.mode === "live" ? "LIVE" : "MOCK"}
            </span>
            <span>{formatTime(result.generated_at)}</span>
            {result.usage?.embedding_mode ? (
              <span>
                检索{" "}
                {EMBEDDING_MODE_LABELS[result.usage.embedding_mode] ||
                  result.usage.embedding_mode}
              </span>
            ) : null}
            {result.usage?.rerank_mode ? (
              <span>
                重排{" "}
                {RERANK_MODE_LABELS[result.usage.rerank_mode] ||
                  result.usage.rerank_mode}
              </span>
            ) : null}
            {result.usage?.llm_route_label ? (
              <span title={result.usage.llm_route || ""}>
                路由 {result.usage.llm_route_label}
              </span>
            ) : null}
            {result.usage?.llm_models?.length ? (
              <span title={result.usage.llm_models.join(" / ")}>
                模型 {result.usage.llm_models.join(" / ")}
              </span>
            ) : null}
            {readOnly ? (
              <span className="history-snapshot-badge">历史快照 · 只读</span>
            ) : null}
          </div>
        </div>
        <button type="button" className="btn btn-secondary" onClick={onReset}>
          <RefreshIcon size={17} />
          审查新合同
        </button>
      </div>

      {job.status === "awaiting_review" ? (
        <div className="review-banner is-awaiting">
          <div>
            <strong>审查已暂停，等待人工复核</strong>
            <span>
              已核实高风险全部处理后会自动完成；模型建议不阻断流程。
            </span>
          </div>
          <div className="review-banner-actions">
            {onCancel ? (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => onCancel(job.job_id)}
              >
                取消任务
              </button>
            ) : null}
            {(result.summary?.blocking_high_pending ?? 0) === 0 ? (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => onResume?.(job.job_id)}
              >
                继续完成
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {job.status === "resuming" ? (
        <div className="review-banner is-resuming">
          <strong>正在应用复核结果…</strong>
          <span>完成后即可导出报告。</span>
        </div>
      ) : null}

      {result.mode !== "live" ? (
        <div className="mock-banner">
          <strong>MOCK 演示模式</strong>
          <span>
            当前没有调用 LLM，风险来自确定性规则；修改建议使用 Playbook 模板。配置任一可用
            Provider 的 API Key（或在 llm_profiles.yaml 启用本地模型）后可切换 live，
            启用 3 个语义审查 Agent。
          </span>
        </div>
      ) : null}

      {result.warnings?.length ? (
        <details className="warnings-box">
          <summary>运行提示（{result.warnings.length}）</summary>
          <ul>
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </details>
      ) : null}

      <SummaryCards summary={result.summary} usage={result.usage} />

      <div className="view-tabs" role="tablist" aria-label="结果视图">
        {VIEWS.map((view, index) => (
          <button
            key={view.key}
            type="button"
            role="tab"
            aria-selected={activeView === view.key}
            className={`view-tab ${activeView === view.key ? "is-active" : ""}`}
            tabIndex={activeView === view.key ? 0 : -1}
            onClick={() => onViewChange?.(view.key)}
            onKeyDown={(event) => onTabKeyDown(event, index)}
          >
            {view.label}
          </button>
        ))}
      </div>

      {activeView === "findings" ? (
        <FindingsList
          findings={findings}
          summary={result.summary}
          filter={filter}
          onFilter={onFilter}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ) : null}

      {activeView === "contract" ? (
        <ContractViewer
          result={result}
          locateTarget={locateTarget}
          onSelectFinding={onSelect}
        />
      ) : null}

      {activeView === "revised" ? (
        <RevisedContractView
          job={job}
          readOnly={readOnly}
          onShowHigh={onShowHigh}
          onExportRevised={onExportRevised}
          busy={busy}
        />
      ) : null}

      {activeView === "preview" ? (
        <ReportPreview job={job} readOnly={readOnly} />
      ) : null}

      <ReportActions
        summary={result.summary}
        jobStatus={job.status}
        onExport={onExport}
        busy={busy}
        onShowHigh={onShowHigh}
        readOnly={readOnly}
      />
    </div>
  );
}
