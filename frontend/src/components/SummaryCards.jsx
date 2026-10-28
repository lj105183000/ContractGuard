import { formatPercent, formatDuration } from "../labels.js";

export default function SummaryCards({ summary, usage }) {
  if (!summary) return null;
  const blocking = summary.blocking_high_pending ?? summary.high_pending ?? 0;
  const advisory = summary.advisory_high_pending ?? 0;
  const bySource = summary.by_source || {};
  const byConfidence = summary.by_confidence || {};
  const cards = [
    {
      key: "high",
      label: "高风险",
      value: summary.high,
      note: `已核实 ${blocking} 条阻断 · 模型建议 ${advisory} 条`,
    },
    { key: "medium", label: "中风险", value: summary.medium, note: "建议逐条确认" },
    { key: "low", label: "低风险", value: summary.low, note: "可批量浏览" },
    {
      key: "pending",
      label: "待复核",
      value: summary.pending_review,
      note: `${summary.needs_human} 条需人工确认`,
    },
  ];

  return (
    <div className="summary-wrap">
      <div className="summary-grid">
        {cards.map((card) => (
          <div key={card.key} className={`summary-card summary-${card.key}`}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            <small>{card.note}</small>
          </div>
        ))}
      </div>
      <div className="metric-strip">
        <span>
          已核实 <strong>{byConfidence.confirmed ?? 0}</strong>
        </span>
        <span>
          模型建议 <strong>{byConfidence.advisory ?? 0}</strong>
        </span>
        <span>
          待核实 <strong>{byConfidence.needs_review ?? 0}</strong>
        </span>
        <span>
          来源：规则 <strong>{bySource.rule ?? 0}</strong> / 模型{" "}
          <strong>{bySource.llm ?? 0}</strong> / 合并{" "}
          <strong>{bySource["rule+llm"] ?? 0}</strong>
        </span>
      </div>
      <div className="metric-strip">
        <span>
          证据定位率 <strong>{formatPercent(summary.evidence_valid_rate)}</strong>
          {summary.evidence_checked ? `（${summary.evidence_checked} 条）` : ""}
        </span>
        <span>
          法条 ID 可解析率{" "}
          <strong>{formatPercent(summary.citation_valid_rate)}</strong>
          {summary.citation_checked ? `（${summary.citation_checked} 个引用）` : ""}
        </span>
        <span>
          模型调用 <strong>{usage?.llm_calls ?? 0} 次</strong>
        </span>
        <span>
          总耗时 <strong>{formatDuration(usage?.duration_ms)}</strong>
        </span>
      </div>
    </div>
  );
}
