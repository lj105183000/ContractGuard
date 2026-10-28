import { useEffect, useMemo, useRef } from "react";
import {
  CONFIDENCE_LABELS,
  SEVERITY_LABELS,
  confidenceTierOf,
  severityClass,
} from "../labels.js";

const SEVERITY_RANK = { high: 0, medium: 1, low: 2 };

function findingSpan(finding, clauseText) {
  if (
    finding.evidence_start !== null &&
    finding.evidence_start !== undefined &&
    finding.evidence_end !== null &&
    finding.evidence_end !== undefined &&
    finding.evidence_end > finding.evidence_start
  ) {
    return [finding.evidence_start, finding.evidence_end];
  }
  if (finding.evidence_quote && clauseText) {
    const index = clauseText.indexOf(finding.evidence_quote);
    if (index >= 0) {
      return [index, index + finding.evidence_quote.length];
    }
  }
  return null;
}

function ClauseText({ clause, findings, activeFindingId, onSelectFinding }) {
  const text = clause.text || "";
  const marks = findings
    .map((finding) => {
      const span = findingSpan(finding, text);
      if (!span) return null;
      return { finding, start: span[0], end: span[1] };
    })
    .filter(Boolean)
    .sort((left, right) => left.start - right.start);

  if (!marks.length) {
    return <p className="contract-clause-text">{text || "—"}</p>;
  }

  const merged = [];
  for (const mark of marks) {
    const last = merged[merged.length - 1];
    if (last && mark.start <= last.end) {
      last.end = Math.max(last.end, mark.end);
      last.findings.push(mark.finding);
    } else {
      merged.push({
        start: mark.start,
        end: mark.end,
        findings: [mark.finding],
      });
    }
  }

  const nodes = [];
  let cursor = 0;
  merged.forEach((mark, index) => {
    if (mark.start > cursor) nodes.push(text.slice(cursor, mark.start));
    const active = mark.findings.some((finding) => finding.id === activeFindingId);
    const first = mark.findings[0];
    nodes.push(
      <mark
        key={`mark-${index}`}
        className={active ? "is-active" : ""}
        title={mark.findings.map((finding) => finding.title).join(" / ")}
        onClick={() => onSelectFinding?.(first.id)}
      >
        {text.slice(mark.start, mark.end)}
      </mark>,
    );
    cursor = mark.end;
  });
  if (cursor < text.length) nodes.push(text.slice(cursor));

  return <p className="contract-clause-text">{nodes}</p>;
}

export default function ContractViewer({
  result,
  locateTarget,
  onSelectFinding,
}) {
  const clauses = result?.clauses || [];
  const findings = result?.findings || [];
  const fullText = result?.full_text || "";
  const clauseRefs = useRef({});

  const locateFinding = useMemo(
    () => findings.find((finding) => finding.id === locateTarget) || null,
    [findings, locateTarget],
  );
  const activeClauseId = locateFinding?.clause_id || null;

  const findingsByClause = useMemo(() => {
    const grouped = {};
    for (const finding of findings) {
      if (!finding.clause_id) continue;
      (grouped[finding.clause_id] ||= []).push(finding);
    }
    return grouped;
  }, [findings]);

  const contractLevel = useMemo(
    () => findings.filter((finding) => !finding.clause_id),
    [findings],
  );

  useEffect(() => {
    if (!activeClauseId) return;
    clauseRefs.current[activeClauseId]?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }, [activeClauseId, locateTarget]);

  const scrollToClause = (clauseId) => {
    clauseRefs.current[clauseId]?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  };

  const renderClause = (clause) => {
    const items = findingsByClause[clause.id] || [];
    return (
      <section
        key={clause.id}
        ref={(node) => {
          clauseRefs.current[clause.id] = node;
        }}
        className={`contract-clause ${
          activeClauseId === clause.id ? "is-active" : ""
        }`}
      >
        <header className="contract-clause-head">
          <span className="contract-article">
            {clause.article || `条款 ${clause.index + 1}`}
          </span>
          <span className="contract-type">{clause.clause_type}</span>
          {items.length ? (
            <span className="contract-risk-count">{items.length} 条风险</span>
          ) : (
            <span className="contract-risk-count is-empty">无风险</span>
          )}
        </header>
        <ClauseText
          clause={clause}
          findings={items}
          activeFindingId={locateFinding?.id}
          onSelectFinding={onSelectFinding}
        />
        {items.length ? (
          <div className="contract-clause-risks">
            {items.map((finding) => (
              <button
                key={finding.id}
                type="button"
                className={`contract-risk-chip ${
                  finding.id === locateFinding?.id ? "is-active" : ""
                }`}
                onClick={() => onSelectFinding?.(finding.id)}
              >
                <span className={`severity-dot ${severityClass(finding.severity)}`} />
                {finding.title}
                <small>{CONFIDENCE_LABELS[confidenceTierOf(finding)]}</small>
              </button>
            ))}
          </div>
        ) : null}
      </section>
    );
  };

  let body = null;
  if (fullText && clauses.length) {
    const nodes = [];
    let cursor = 0;
    clauses.forEach((clause) => {
      if (clause.start > cursor) {
        nodes.push(
          <div className="contract-gap" key={`gap-${clause.id}`}>
            {fullText.slice(cursor, clause.start)}
          </div>,
        );
      }
      nodes.push(renderClause(clause));
      cursor = Math.max(cursor, clause.end ?? cursor);
    });
    if (cursor < fullText.length) {
      nodes.push(
        <div className="contract-gap" key="tail">
          {fullText.slice(cursor)}
        </div>,
      );
    }
    body = <article className="contract-body">{nodes}</article>;
  } else if (clauses.length) {
    body = (
      <article className="contract-body">
        <p className="contract-fallback-note">
          该快照未保存合同全文，以下为条款视图。
        </p>
        {clauses.map(renderClause)}
      </article>
    );
  } else {
    body = <div className="preview-empty card">这份结果没有可展示的条款。</div>;
  }

  return (
    <div className="contract-view">
      <aside className="contract-nav">
        <p className="eyebrow">CLAUSES</p>
        <h2>条款导航</h2>
        <div className="contract-nav-list">
          {clauses.map((clause) => {
            const items = findingsByClause[clause.id] || [];
            const highest = items.reduce(
              (current, finding) =>
                current === null ||
                (SEVERITY_RANK[finding.severity] ?? 9) <
                  (SEVERITY_RANK[current] ?? 9)
                  ? finding.severity
                  : current,
              null,
            );
            return (
              <button
                key={clause.id}
                type="button"
                className={`contract-nav-item ${
                  activeClauseId === clause.id ? "is-active" : ""
                }`}
                onClick={() => scrollToClause(clause.id)}
              >
                <span>{clause.article || `条款 ${clause.index + 1}`}</span>
                <small>{items.length ? `${items.length} 条` : "—"}</small>
                {highest ? (
                  <span
                    className={`severity-dot ${severityClass(highest)}`}
                    title={SEVERITY_LABELS[highest]}
                  />
                ) : null}
              </button>
            );
          })}
        </div>
        {contractLevel.length ? (
          <div className="contract-level-list">
            <p>合同级风险</p>
            {contractLevel.map((finding) => (
              <button
                key={finding.id}
                type="button"
                className={`contract-risk-chip ${
                  finding.id === locateFinding?.id ? "is-active" : ""
                }`}
                onClick={() => onSelectFinding?.(finding.id)}
              >
                <span className={`severity-dot ${severityClass(finding.severity)}`} />
                {finding.title}
              </button>
            ))}
          </div>
        ) : null}
      </aside>
      <div className="contract-paper">{body}</div>
    </div>
  );
}
