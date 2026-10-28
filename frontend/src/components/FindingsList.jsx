import { useMemo, useState } from "react";
import {
  CATEGORY_LABELS,
  CONFIDENCE_LABELS,
  CONFIDENCE_ORDER,
  SEVERITY_LABELS,
  SEVERITY_ORDER,
  SOURCE_LABELS,
  STATUS_LABELS,
  confidenceTierOf,
  severityClass,
} from "../labels.js";

const SEVERITY_FILTERS = [
  { key: "all", label: "全部" },
  { key: "high", label: "高风险" },
  { key: "medium", label: "中风险" },
  { key: "low", label: "低风险" },
];

const STATUS_FILTERS = [
  { key: "all", label: "全部状态" },
  { key: "pending", label: "待复核" },
  { key: "needs_human", label: "需人工确认" },
  { key: "accepted", label: "已采纳" },
  { key: "rejected", label: "已忽略" },
];

const SORT_OPTIONS = [
  { key: "severity", label: "按严重度" },
  { key: "clause", label: "按条款顺序" },
  { key: "tier", label: "按置信分层" },
  { key: "status", label: "按状态" },
];

function sortFindings(findings, sortBy) {
  const copy = [...findings];
  copy.sort((left, right) => {
    const severity =
      (SEVERITY_ORDER[left.severity] ?? 9) - (SEVERITY_ORDER[right.severity] ?? 9);
    const tier =
      (CONFIDENCE_ORDER[confidenceTierOf(left)] ?? 9) -
      (CONFIDENCE_ORDER[confidenceTierOf(right)] ?? 9);
    if (sortBy === "clause") {
      return (
        (left.clause_article || "zzz").localeCompare(
          right.clause_article || "zzz",
          "zh-CN",
        ) || severity
      );
    }
    if (sortBy === "tier") return tier || severity;
    if (sortBy === "status") {
      return (
        (left.status || "").localeCompare(right.status || "") ||
        severity ||
        tier
      );
    }
    return severity || tier || (right.confidence ?? 0) - (left.confidence ?? 0);
  });
  return copy;
}

export default function FindingsList({
  findings,
  summary,
  filter,
  onFilter,
  selectedId,
  onSelect,
}) {
  const [statusFilter, setStatusFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState("severity");
  const [onlyConfirmed, setOnlyConfirmed] = useState(false);

  const counts = {
    all: summary?.total ?? findings.length,
    high: summary?.high ?? 0,
    medium: summary?.medium ?? 0,
    low: summary?.low ?? 0,
  };

  const statusCounts = useMemo(() => {
    const result = { all: findings.length };
    for (const finding of findings) {
      result[finding.status] = (result[finding.status] || 0) + 1;
    }
    return result;
  }, [findings]);

  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const filtered = findings.filter((finding) => {
      if (statusFilter !== "all" && finding.status !== statusFilter) return false;
      if (onlyConfirmed && confidenceTierOf(finding) !== "confirmed") return false;
      if (!keyword) return true;
      const haystack = [
        finding.title,
        finding.analysis,
        finding.evidence_quote,
        finding.clause_article,
        CATEGORY_LABELS[finding.category],
        SOURCE_LABELS[finding.source],
        STATUS_LABELS[finding.status],
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(keyword);
    });
    return sortFindings(filtered, sortBy);
  }, [findings, statusFilter, onlyConfirmed, query, sortBy]);

  return (
    <section className="findings-section">
      <div className="findings-head">
        <div>
          <p className="eyebrow">STEP 03</p>
          <h2>风险清单</h2>
        </div>
        <div className="filter-tabs" role="tablist" aria-label="按风险等级筛选">
          {SEVERITY_FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={filter === item.key}
              className={`filter-tab ${filter === item.key ? "is-active" : ""}`}
              onClick={() => onFilter(item.key)}
            >
              {item.label}
              <span>{counts[item.key]}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="findings-toolbar">
        <input
          className="findings-search"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索风险、条款或原文…"
          aria-label="搜索风险"
        />
        <select
          className="findings-sort"
          value={sortBy}
          onChange={(event) => setSortBy(event.target.value)}
          aria-label="排序方式"
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </select>
        <label className="findings-confirmed">
          <input
            type="checkbox"
            checked={onlyConfirmed}
            onChange={(event) => setOnlyConfirmed(event.target.checked)}
          />
          只看已核实
        </label>
      </div>

      <div className="status-tabs" role="tablist" aria-label="按复核状态筛选">
        {STATUS_FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={statusFilter === item.key}
            className={`status-tab ${statusFilter === item.key ? "is-active" : ""}`}
            onClick={() => setStatusFilter(item.key)}
          >
            {item.label}
            <span>{statusCounts[item.key] ?? 0}</span>
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <div className="empty-findings">
          <strong>这一筛选下没有风险</strong>
          <span>可以切换筛选条件或清空搜索词后再看。</span>
        </div>
      ) : (
        <div className="findings-list">
          {visible.map((finding) => {
            const tier = confidenceTierOf(finding);
            return (
              <button
                key={finding.id}
                type="button"
                className={`finding-card ${
                  selectedId === finding.id ? "is-selected" : ""
                } ${finding.status === "rejected" ? "is-rejected" : ""}`}
                onClick={() => onSelect(finding.id)}
              >
                <span className={`severity-bar ${severityClass(finding.severity)}`} />
                <div className="finding-main">
                  <div className="finding-topline">
                    <span
                      className={`severity-pill ${severityClass(finding.severity)}`}
                    >
                      {SEVERITY_LABELS[finding.severity]}
                    </span>
                    <span className="finding-clause">
                      {finding.clause_article || "合同级"}
                    </span>
                    <span className="finding-category">
                      {CATEGORY_LABELS[finding.category] || finding.category}
                    </span>
                    <span className={`tier-pill tier-${tier}`}>
                      {CONFIDENCE_LABELS[tier]}
                    </span>
                  </div>
                  <h3>{finding.title}</h3>
                  <p className="finding-excerpt">
                    {finding.evidence_quote || finding.analysis || "—"}
                  </p>
                  <div className="finding-foot">
                    <span>来源：{SOURCE_LABELS[finding.source] || finding.source}</span>
                    <span className={`status-pill status-${finding.status}`}>
                      {STATUS_LABELS[finding.status] || finding.status}
                    </span>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
