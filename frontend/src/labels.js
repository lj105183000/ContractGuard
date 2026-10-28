export const SEVERITY_LABELS = {
  high: "高风险",
  medium: "中风险",
  low: "低风险",
};

export const CATEGORY_LABELS = {
  price: "价款",
  payment: "付款",
  delivery: "交付",
  acceptance: "验收",
  warranty: "质保",
  liability: "违约责任",
  termination: "解除/终止",
  dispute: "争议解决",
  compliance: "合规",
  ip: "知识产权",
  general: "其他",
};

export const SOURCE_LABELS = {
  rule: "规则",
  llm: "模型",
  "rule+llm": "规则 + 模型",
};

export const STATUS_LABELS = {
  pending: "待复核",
  accepted: "已采纳",
  rejected: "已忽略",
  needs_human: "需人工确认",
};

export const STATUS_TONE = {
  pending: "warning",
  accepted: "success",
  rejected: "muted",
  needs_human: "danger",
};

export const EMBEDDING_MODE_LABELS = {
  hybrid: "混合",
  bm25_only: "BM25",
};

export const RERANK_MODE_LABELS = {
  off: "关闭",
  dashscope: "DashScope",
  unavailable: "不可用",
  error: "失败回退",
};

export const JOB_STATUS_LABELS = {
  queued: "排队中",
  running: "审查中",
  awaiting_review: "待人工复核",
  resuming: "完成复核中",
  completed: "已完成",
  completed_with_warnings: "完成（有提示）",
  failed: "失败",
  cancelled: "已取消",
};

export const CONFIDENCE_LABELS = {
  confirmed: "已核实",
  advisory: "模型建议",
  needs_review: "待核实",
  unknown: "未分层",
};

export const CONFIDENCE_TONE = {
  confirmed: "success",
  advisory: "warning",
  needs_review: "danger",
  unknown: "muted",
};

export const CONFIDENCE_ORDER = {
  confirmed: 0,
  advisory: 1,
  needs_review: 2,
  unknown: 3,
};

export const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 };

export function confidenceTierOf(finding) {
  if (finding?.confidence_tier) return finding.confidence_tier;
  // 旧快照没有 tier：规则来源视为已核实，模型来源按模型建议展示。
  if (finding?.source === "rule" || finding?.source === "rule+llm") {
    return "confirmed";
  }
  return "advisory";
}

export function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatDuration(ms) {
  if (!ms) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

export function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function severityClass(severity) {
  return `severity-${severity || "low"}`;
}
