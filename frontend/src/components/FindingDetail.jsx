import { useEffect, useRef, useState } from "react";
import {
  CATEGORY_LABELS,
  CONFIDENCE_LABELS,
  SEVERITY_LABELS,
  SOURCE_LABELS,
  STATUS_LABELS,
  confidenceTierOf,
  formatPercent,
  formatTime,
  severityClass,
} from "../labels.js";
import {
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  ScaleIcon,
  XIcon,
} from "./Icons.jsx";
import { useFocusTrap } from "../useFocusTrap.js";

function EvidenceBlock({ finding, result }) {
  const clause = result.clauses?.find((item) => item.id === finding.clause_id);
  const text = finding.clause_text || clause?.text || "";
  let start = finding.evidence_start;
  let end = finding.evidence_end;
  if ((start === null || start === undefined) && finding.evidence_quote && text) {
    const index = text.indexOf(finding.evidence_quote);
    if (index >= 0) {
      start = index;
      end = index + finding.evidence_quote.length;
    }
  }
  if (text && start !== null && start !== undefined && end && start < end) {
    return (
      <p className="clause-text">
        {text.slice(0, start)}
        <mark>{text.slice(start, end)}</mark>
        {text.slice(end)}
      </p>
    );
  }
  if (text) return <p className="clause-text">{text}</p>;
  if (finding.evidence_quote) {
    return <p className="clause-text contract-level">{finding.evidence_quote}</p>;
  }
  return <p className="clause-text muted">合同级风险，无可定位条款。</p>;
}

export default function FindingDetail({
  finding,
  result,
  onClose,
  onDecide,
  onUpdateSuggestion,
  onLocate,
  busy = false,
  readOnly = false,
}) {
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);
  const [editingSuggestion, setEditingSuggestion] = useState(false);
  const [suggestionDraft, setSuggestionDraft] = useState("");
  const dialogRef = useRef(null);
  useFocusTrap(dialogRef);
  const tier = confidenceTierOf(finding);

  useEffect(() => {
    setNote("");
    setCopied(false);
    setEditingSuggestion(false);
    setSuggestionDraft("");
  }, [finding.id]);

  useEffect(() => {
    const handler = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const legalBasis = (finding.legal_basis_ids || [])
    .map((id) => result.legal_basis?.[id])
    .filter(Boolean);
  const rule = result.playbook?.[finding.rule_id || finding.rule_hint];

  const copySuggested = async () => {
    if (!finding.suggested_text) return;
    try {
      await navigator.clipboard.writeText(finding.suggested_text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="drawer-overlay" onMouseDown={onClose}>
      <aside
        ref={dialogRef}
        tabIndex={-1}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={finding.title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="drawer-head">
          <div>
            <div className="drawer-badges">
              <span className={`severity-pill ${severityClass(finding.severity)}`}>
                {SEVERITY_LABELS[finding.severity]}
              </span>
              <span className="meta-pill">{finding.clause_article || "合同级"}</span>
              <span className="meta-pill">
                {CATEGORY_LABELS[finding.category] || finding.category}
              </span>
              <span className={`tier-pill tier-${tier}`}>
                {CONFIDENCE_LABELS[tier]}
              </span>
            </div>
            <h2>{finding.title}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <XIcon size={20} />
          </button>
        </header>

        <div className="drawer-scroll">
          <div className="detail-meta">
            <span>来源：{SOURCE_LABELS[finding.source] || finding.source}</span>
            <span>状态：{STATUS_LABELS[finding.status] || finding.status}</span>
            <span>置信分层：{CONFIDENCE_LABELS[tier]}</span>
            {tier !== "confirmed" ? (
              <span>
                模型自评：{formatPercent(finding.confidence)}（未校准）
              </span>
            ) : null}
          </div>
          {finding.confidence_reasons?.length ? (
            <ul className="confidence-reasons">
              {finding.confidence_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}

          <section className="detail-section">
            <div className="section-title-row">
              <h3>原文证据</h3>
              {finding.clause_id && onLocate ? (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => onLocate(finding)}
                >
                  在合同中定位
                </button>
              ) : null}
            </div>
            <EvidenceBlock finding={finding} result={result} />
            {finding.gate_notes?.length ? (
              <ul className="gate-notes">
                {finding.gate_notes.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : null}
          </section>

          <section className="detail-section">
            <h3>风险分析</h3>
            <p className="analysis-text">{finding.analysis || "—"}</p>
          </section>

          <section className="detail-section">
            <h3>法律依据</h3>
            {legalBasis.length === 0 ? (
              <p className="muted">无检索到的法条依据（模型建议）。</p>
            ) : (
              <div className="legal-list">
                {legalBasis.map((article) => (
                  <details key={article.id} className="legal-item">
                    <summary>
                      <span className="legal-dot" aria-hidden="true">
                        <ScaleIcon size={15} />
                      </span>
                      <span className="legal-title">
                        <strong>
                          {article.law} {article.article}
                        </strong>
                        <small>{article.title}</small>
                      </span>
                      <ChevronDownIcon size={16} />
                    </summary>
                    <p>{article.text}</p>
                    <a href={article.source_url} target="_blank" rel="noreferrer">
                      查看官方来源
                    </a>
                  </details>
                ))}
              </div>
            )}
          </section>

          {rule ? (
            <section className="detail-section">
              <h3>企业规则</h3>
              <div className="rule-box">
                <strong>
                  {rule.id} · {rule.title}
                </strong>
                <p>{rule.recommendation}</p>
                {rule.fallback ? <small>退让底线：{rule.fallback}</small> : null}
              </div>
            </section>
          ) : null}

          <section className="detail-section">
            <div className="section-title-row">
              <h3>
                建议修改为
                {finding.suggested_text_source === "human" ? (
                  <span className="meta-pill">人工编辑</span>
                ) : null}
              </h3>
              <div className="section-title-actions">
                {finding.suggested_text ? (
                  <button type="button" className="copy-button" onClick={copySuggested}>
                    <CopyIcon size={14} />
                    {copied ? "已复制" : "复制"}
                  </button>
                ) : null}
                {!readOnly && onUpdateSuggestion && !editingSuggestion ? (
                  <button
                    type="button"
                    className="copy-button"
                    onClick={() => {
                      setSuggestionDraft(finding.suggested_text || "");
                      setEditingSuggestion(true);
                    }}
                  >
                    编辑
                  </button>
                ) : null}
              </div>
            </div>
            {editingSuggestion ? (
              <div className="suggestion-editor">
                <textarea
                  value={suggestionDraft}
                  onChange={(event) => setSuggestionDraft(event.target.value)}
                  rows={7}
                  placeholder="编辑后可保存；生成修订稿会使用这段文本。"
                />
                <div className="decision-row">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy || !suggestionDraft.trim()}
                    onClick={async () => {
                      await onUpdateSuggestion(finding, suggestionDraft);
                      setEditingSuggestion(false);
                    }}
                  >
                    保存修改
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => setEditingSuggestion(false)}
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <div className="suggest-box">{finding.suggested_text || "—"}</div>
            )}
            <div className="two-col">
              <div>
                <h4>退让方案</h4>
                <p>{finding.fallback || "—"}</p>
              </div>
              <div>
                <h4>谈判要点</h4>
                <p>{finding.negotiation_note || "—"}</p>
              </div>
            </div>
          </section>

          {finding.review_note || finding.reviewed_at ? (
            <section className="detail-section">
              <h3>人工意见</h3>
              {finding.review_note ? (
                <p className="analysis-text">{finding.review_note}</p>
              ) : null}
              {finding.reviewed_at ? (
                <p className="hint">
                  复核时间：{formatTime(finding.reviewed_at)}
                  {finding.review_history?.length
                    ? ` · 共 ${finding.review_history.length} 次决策`
                    : ""}
                </p>
              ) : null}
            </section>
          ) : null}

          <p className="detail-disclaimer">{result.disclaimer}</p>
        </div>

        <footer className="drawer-foot">
          {readOnly ? (
            <p className="history-readonly-note">
              历史快照为只读，请重新上传合同后继续复核。
            </p>
          ) : (
            <>
              <label className="note-label">
                复核意见（可选）
                <textarea
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="例如：已与供应商确认，接受该修改；或：需法务进一步评估。"
                  rows={2}
                />
              </label>
              <div className="decision-row">
                <button
                  type="button"
                  className="btn btn-success"
                  disabled={busy}
                  onClick={() => onDecide(finding, "accepted", note)}
                >
                  <CheckIcon size={17} />
                  采纳建议
                </button>
                <button
                  type="button"
                  className="btn btn-outline-danger"
                  disabled={busy}
                  onClick={() => onDecide(finding, "rejected", note)}
                >
                  <XIcon size={17} />
                  忽略风险
                </button>
              </div>
            </>
          )}
        </footer>
      </aside>
    </div>
  );
}
