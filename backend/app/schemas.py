"""全项目共享的数据模型。

这些模型同时承担三个职责：
1. 约束 LLM 的结构化输出（JSON -> Pydantic 校验）；
2. 约束节点之间传递的数据；
3. 直接作为 FastAPI 的响应模型。

字段名用英文，枚举值用英文；中文只出现在展示文案里。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(str, Enum):
    PRICE = "price"
    PAYMENT = "payment"
    DELIVERY = "delivery"
    ACCEPTANCE = "acceptance"
    WARRANTY = "warranty"
    LIABILITY = "liability"
    TERMINATION = "termination"
    DISPUTE = "dispute"
    COMPLIANCE = "compliance"
    IP = "ip"
    GENERAL = "general"


class ClauseType(str, Enum):
    PREAMBLE = "preamble"
    SUBJECT = "subject"
    PRICE = "price"
    PAYMENT = "payment"
    DELIVERY = "delivery"
    ACCEPTANCE = "acceptance"
    WARRANTY = "warranty"
    LIABILITY = "liability"
    TERMINATION = "termination"
    CONFIDENTIALITY = "confidentiality"
    IP = "ip"
    DISPUTE = "dispute"
    FORCE_MAJEURE = "force_majeure"
    GENERAL = "general"
    ANNEX = "annex"
    OTHER = "other"


class Source(str, Enum):
    RULE = "rule"
    LLM = "llm"
    RULE_LLM = "rule+llm"


class FindingStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_HUMAN = "needs_human"


class ConfidenceTier(str, Enum):
    """确定性的置信分层。

    只依据可验证信号（来源、证据定位、法条校验），不采用模型自评分数。
    """

    CONFIRMED = "confirmed"
    ADVISORY = "advisory"
    NEEDS_REVIEW = "needs_review"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    RESUMING = "resuming"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Decision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# 枚举归一化：LLM 可能返回中文、大小写不一致或同义词
# ---------------------------------------------------------------------------

_SEVERITY_ALIASES = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "高": "high",
    "中": "medium",
    "低": "low",
    "严重": "high",
    "一般": "medium",
    "轻微": "low",
}

_CATEGORY_ALIASES = {
    "price": "price",
    "amount": "price",
    "payment": "payment",
    "delivery": "delivery",
    "acceptance": "acceptance",
    "warranty": "warranty",
    "liability": "liability",
    "breach": "liability",
    "termination": "termination",
    "dispute": "dispute",
    "jurisdiction": "dispute",
    "compliance": "compliance",
    "confidentiality": "compliance",
    "ip": "ip",
    "intellectual_property": "ip",
    "general": "general",
    "other": "general",
    "价款": "price",
    "价格": "price",
    "金额": "price",
    "付款": "payment",
    "支付": "payment",
    "交付": "delivery",
    "交货": "delivery",
    "验收": "acceptance",
    "质保": "warranty",
    "保修": "warranty",
    "违约": "liability",
    "赔偿": "liability",
    "责任": "liability",
    "解除": "termination",
    "终止": "termination",
    "争议": "dispute",
    "管辖": "dispute",
    "诉讼": "dispute",
    "仲裁": "dispute",
    "合规": "compliance",
    "保密": "compliance",
    "知识产权": "ip",
    "其他": "general",
    "一般条款": "general",
}

_CLAUSE_TYPE_ALIASES = {
    "preamble": "preamble",
    "subject": "subject",
    "price": "price",
    "payment": "payment",
    "delivery": "delivery",
    "acceptance": "acceptance",
    "warranty": "warranty",
    "liability": "liability",
    "termination": "termination",
    "confidentiality": "confidentiality",
    "ip": "ip",
    "dispute": "dispute",
    "force_majeure": "force_majeure",
    "general": "general",
    "annex": "annex",
    "other": "other",
    "首部": "preamble",
    "主体": "subject",
    "标的": "subject",
    "价款": "price",
    "价格": "price",
    "付款": "payment",
    "支付": "payment",
    "交付": "delivery",
    "交货": "delivery",
    "验收": "acceptance",
    "质保": "warranty",
    "保修": "warranty",
    "违约": "liability",
    "违约责任": "liability",
    "解除": "termination",
    "终止": "termination",
    "保密": "confidentiality",
    "知识产权": "ip",
    "争议解决": "dispute",
    "管辖": "dispute",
    "不可抗力": "force_majeure",
    "其他": "general",
    "通用": "general",
    "附件": "annex",
}


def _normalize(value: Any, aliases: dict[str, str], fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, Enum):
        value = value.value
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return aliases.get(key, fallback)


def _normalize_severity(value: Any) -> str:
    return _normalize(value, _SEVERITY_ALIASES, "medium")


def _normalize_category(value: Any) -> str:
    return _normalize(value, _CATEGORY_ALIASES, "general")


def _normalize_clause_type(value: Any) -> str:
    return _normalize(value, _CLAUSE_TYPE_ALIASES, "other")


def _normalize_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("，", ",").replace("、", ",").split(",")
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


# ---------------------------------------------------------------------------
# 条款与事实
# ---------------------------------------------------------------------------


class Clause(BaseModel):
    """合同中的一个条款，保留原文偏移以便前端高亮。"""

    id: str
    index: int
    article: str
    text: str
    start: int
    end: int
    page: int | None = None
    clause_type: ClauseType = ClauseType.OTHER
    summary: str | None = None


class PaymentFacts(BaseModel):
    prepayment_ratio: float | None = None
    payment_days: int | None = None
    retention_ratio: float | None = None
    retention_release_days: int | None = None
    payment_after_acceptance: bool | None = None
    full_payment_on_delivery: bool | None = None
    payment_terms_text: str | None = None


class DeliveryFacts(BaseModel):
    date_text: str | None = None
    days: int | None = None
    place: str | None = None
    method: str | None = None
    vague: bool | None = None


class AcceptanceFacts(BaseModel):
    present: bool | None = None
    period_days: int | None = None
    standard: str | None = None
    deemed_acceptance: bool | None = None


class WarrantyFacts(BaseModel):
    present: bool | None = None
    months: int | None = None


class LiabilityFacts(BaseModel):
    penalty_daily_rate: float | None = None
    penalty_cap_ratio: float | None = None
    unlimited_liability: bool | None = None
    indirect_damages: bool | None = None
    quality_remedy: bool | None = None


class TerminationFacts(BaseModel):
    buyer_right: bool | None = None
    one_sided: bool | None = None
    notice_days: int | None = None
    auto_renewal: bool | None = None


class DisputeFacts(BaseModel):
    court: str | None = None
    arbitration: str | None = None
    both_litigation_and_arbitration: bool | None = None
    unfavorable_venue: bool | None = None
    clause_present: bool | None = None


class ComplianceFacts(BaseModel):
    confidentiality_present: bool | None = None
    confidentiality_mutual: bool | None = None
    ip_ownership: str | None = None
    attachments_present: bool | None = None
    effective_condition: str | None = None
    signatory_clear: bool | None = None


class ContractFacts(BaseModel):
    buyer: str | None = None
    supplier: str | None = None
    contract_value: float | None = None
    currency: str | None = None
    tax_included: bool | None = None
    subject_matter: str | None = None
    payment: PaymentFacts = Field(default_factory=PaymentFacts)
    delivery: DeliveryFacts = Field(default_factory=DeliveryFacts)
    acceptance: AcceptanceFacts = Field(default_factory=AcceptanceFacts)
    warranty: WarrantyFacts = Field(default_factory=WarrantyFacts)
    liability: LiabilityFacts = Field(default_factory=LiabilityFacts)
    termination: TerminationFacts = Field(default_factory=TerminationFacts)
    dispute: DisputeFacts = Field(default_factory=DisputeFacts)
    compliance: ComplianceFacts = Field(default_factory=ComplianceFacts)
    raw_notes: list[str] = Field(default_factory=list)

    @field_validator("contract_value", mode="before")
    @classmethod
    def _coerce_value(cls, value: Any) -> float | None:
        if value in (None, "", "null", "None"):
            return None
        if isinstance(value, str):
            cleaned = value.replace(",", "").replace("，", "").replace("元", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @field_validator("currency", mode="before")
    @classmethod
    def _coerce_currency(cls, value: Any) -> str | None:
        if value in (None, "", "null", "None"):
            return None
        text = str(value).strip().upper()
        return {"人民币": "CNY", "元": "CNY", "RMB": "CNY", "美元": "USD", "USD": "USD"}.get(
            text, text or None
        )


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------


class LegalArticle(BaseModel):
    id: str
    law: str
    article: str
    title: str
    text: str
    effective_date: str
    source_url: str
    tags: list[str] = Field(default_factory=list)


class PlaybookRule(BaseModel):
    id: str
    title: str
    description: str = ""
    category: Category
    severity: Severity
    applies_to: list[ClauseType] = Field(default_factory=list)
    condition: dict[str, Any]
    recommendation: str
    fallback: str = ""
    legal_basis: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value: Any) -> str:
        return _normalize_category(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value: Any) -> str:
        return _normalize_severity(value)

    @field_validator("applies_to", mode="before")
    @classmethod
    def _applies_to(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [_normalize_clause_type(item) for item in value]


class SampleInfo(BaseModel):
    id: str
    title: str
    filename: str
    format: str
    description: str = ""


# ---------------------------------------------------------------------------
# 风险与报告
# ---------------------------------------------------------------------------


class FindingReviewEvent(BaseModel):
    decision: Decision
    note: str = ""
    at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    )


class Finding(BaseModel):
    id: str
    clause_id: str | None = None
    clause_article: str | None = None
    clause_text: str | None = None
    category: Category
    severity: Severity
    source: Source
    title: str
    analysis: str = ""
    evidence_quote: str = ""
    evidence_start: int | None = None
    evidence_end: int | None = None
    legal_basis_ids: list[str] = Field(default_factory=list)
    rule_id: str | None = None
    rule_hint: str | None = None
    agent: str | None = None
    confidence: float = 0.0
    confidence_tier: ConfidenceTier | None = None
    confidence_reasons: list[str] = Field(default_factory=list)
    rejected_legal_basis_ids: list[str] = Field(default_factory=list)
    recommendation: str = ""
    fallback: str = ""
    suggested_text: str = ""
    suggested_text_source: Literal["llm", "template", "human"] = "template"
    negotiation_note: str = ""
    review_note: str = ""
    reviewed_at: str | None = None
    review_history: list[FindingReviewEvent] = Field(default_factory=list)
    status: FindingStatus = FindingStatus.PENDING
    gate_notes: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> float:
        return _clamp_confidence(value)


class ReviewSummary(BaseModel):
    total: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    pending_review: int = 0
    high_pending: int = 0
    accepted: int = 0
    rejected: int = 0
    needs_human: int = 0
    evidence_valid_rate: float | None = None
    citation_valid_rate: float | None = None
    by_source: dict[str, int] = Field(default_factory=dict)
    by_confidence: dict[str, int] = Field(default_factory=dict)
    blocking_high_pending: int = 0
    advisory_high_pending: int = 0
    evidence_checked: int = 0
    citation_checked: int = 0


class LLMCallTrace(BaseModel):
    name: str
    model: str
    profile: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    mock: bool = False
    success: bool = True
    error: str | None = None


class UsageSummary(BaseModel):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    mode: str = "mock"
    embedding_mode: str = "bm25_only"
    rerank_mode: str = "off"
    llm_route: str | None = None
    llm_route_label: str | None = None
    llm_models: list[str] = Field(default_factory=list)
    traces: list[LLMCallTrace] = Field(default_factory=list)


class ContractMeta(BaseModel):
    filename: str
    source_type: str = "txt"
    title: str | None = None
    page_count: int = 0
    clause_count: int = 0


class ReviewResult(BaseModel):
    contract: ContractMeta
    summary: ReviewSummary
    clauses: list[Clause]
    findings: list[Finding]
    full_text: str = ""
    legal_basis: dict[str, LegalArticle] = Field(default_factory=dict)
    playbook: dict[str, PlaybookRule] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    usage: UsageSummary = Field(default_factory=UsageSummary)
    mode: str = "mock"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    )
    disclaimer: str = ""


class RevisedChange(BaseModel):
    """一条已采纳建议在修订稿中的落地方式。"""

    finding_id: str
    clause_id: str | None = None
    clause_article: str | None = None
    change_type: Literal["replace", "annotate", "append"]
    title: str
    severity: Severity
    suggestion_source: Literal["llm", "template", "human"] | None = None
    original_text: str = ""
    revised_text: str = ""
    review_note: str = ""
    reason: str = ""


class RevisedContract(BaseModel):
    """按需生成的合同修订稿，不写回 JobState。"""

    job_id: str | None = None
    contract_title: str = ""
    source_filename: str = ""
    text: str
    changes: list[RevisedChange] = Field(default_factory=list)
    accepted_count: int = 0
    replaced_count: int = 0
    annotated_count: int = 0
    appended_count: int = 0
    skipped_count: int = 0
    mode: str = "mock"
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = ""
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    )


class JobEvent(BaseModel):
    node: str
    label: str
    status: EventStatus
    message: str = ""
    progress: int = 0
    duration_ms: int = 0
    at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    )


class JobState(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    filename: str = ""
    llm_route: str | None = None
    batch_id: str | None = None
    interrupt_id: str | None = None
    pending_review_ids: list[str] = Field(default_factory=list)
    source_path: str | None = None
    cleanup_source: bool = True
    cancel_requested: bool = False
    revision: int = 0
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    )
    events: list[JobEvent] = Field(default_factory=list)
    result: ReviewResult | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class JobSummary(BaseModel):
    job_id: str
    filename: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    llm_route: str | None = None
    batch_id: str | None = None
    created_at: str
    updated_at: str
    error: str | None = None
    summary: ReviewSummary | None = None


class BatchStats(BaseModel):
    contracts: int = 0
    total: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    pending_review: int = 0
    blocking_high_pending: int = 0
    accepted: int = 0
    rejected: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, dict[str, int]] = Field(default_factory=dict)


class BatchBlockedItem(BaseModel):
    job_id: str
    filename: str
    status: JobStatus
    reason: str
    blocking_high_pending: int = 0


class BatchState(BaseModel):
    batch_id: str
    created_at: str
    updated_at: str
    stats: BatchStats
    export_allowed: bool = False
    blocked_items: list[BatchBlockedItem] = Field(default_factory=list)
    items: list[JobSummary] = Field(default_factory=list)


class BatchDecisionRequest(BaseModel):
    decision: Decision
    note: str = ""
    finding_ids: list[str] | None = None


class BatchDecisionItem(BaseModel):
    job_id: str
    filename: str
    status: JobStatus
    decided: int = 0
    skipped: int = 0
    error: str | None = None


class BatchDecisionResponse(BaseModel):
    batch_id: str
    decided: int = 0
    skipped: int = 0
    items: list[BatchDecisionItem] = Field(default_factory=list)


class LLMRouteInfo(BaseModel):
    id: str
    label: str
    description: str = ""
    available: bool = False
    reason: str = ""
    models: dict[str, list[str]] = Field(default_factory=dict)


class MetaResponse(BaseModel):
    app: str = "ContractGuard"
    version: str = "0.1.0"
    mode: str
    mode_reason: str
    llm_model: str
    llm_route: str | None = None
    llm_routes: list[LLMRouteInfo] = Field(default_factory=list)
    embedding_model: str
    legal_count: int
    rule_count: int
    sample_count: int
    embedding_mode: str
    rerank_mode: str = "off"
    rerank_model: str | None = None
    disclaimer: str
    max_upload_mb: int
    ocr_max_pages: int


class ReviewDecisionRequest(BaseModel):
    decision: Decision
    note: str = ""


class SuggestionUpdateRequest(BaseModel):
    suggested_text: str = Field(min_length=1, max_length=5000)


class ReviewDecisionResponse(BaseModel):
    finding: Finding
    summary: ReviewSummary


# ---------------------------------------------------------------------------
# LLM 输出 Schema（严格模式，只用于校验，不直接进数据库）
# ---------------------------------------------------------------------------


class ExtractedClause(BaseModel):
    clause_id: str
    clause_type: ClauseType = ClauseType.OTHER
    summary: str | None = None

    @field_validator("clause_type", mode="before")
    @classmethod
    def _clause_type(cls, value: Any) -> str:
        return _normalize_clause_type(value)


class ClauseExtraction(BaseModel):
    clauses: list[ExtractedClause] = Field(default_factory=list)
    facts: ContractFacts = Field(default_factory=ContractFacts)


class LLMFinding(BaseModel):
    category: Category = Category.GENERAL
    rule_hint: str | None = None
    severity: Severity = Severity.MEDIUM
    clause_id: str
    title: str
    analysis: str = ""
    evidence_quote: str = ""
    legal_basis_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value: Any) -> str:
        return _normalize_category(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value: Any) -> str:
        return _normalize_severity(value)

    @field_validator("legal_basis_ids", mode="before")
    @classmethod
    def _legal_ids(cls, value: Any) -> list[str]:
        return _normalize_ids(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> float:
        return _clamp_confidence(value)

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _quote(cls, value: Any) -> str:
        return str(value or "").strip()


class AgentOutput(BaseModel):
    findings: list[LLMFinding] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _unwrap_list(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {"findings": value}
        return value


class RedlineItem(BaseModel):
    finding_id: str
    suggested_text: str
    fallback: str = ""
    negotiation_note: str = ""


class RedlineOutput(BaseModel):
    redlines: list[RedlineItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _unwrap_list(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {"redlines": value}
        return value


class EvalLabel(BaseModel):
    id: str
    clause_article: str = ""
    category: Category
    rule_id: str | None = None
    severity: Severity
    keywords: list[str] = Field(default_factory=list)
    # 真实合同标签的稳定锚点；合成标签可以留空，继续按 clause_article 匹配。
    clause_id: str | None = None
    quote: str | None = None
    page: int | None = None
    scope: Literal["in_scope", "adjacent", "out_of_scope"] | None = None
    label_layer: Literal["rule_detectable", "semantic", "out_of_scope"] | None = None
    annotator: str = "author"
    annotation_pass: int = 1
    confidence: Literal["high", "medium", "low"] | None = None
    notes: str = ""

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value: Any) -> str:
        return _normalize_category(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value: Any) -> str:
        return _normalize_severity(value)


ReportFormat = Literal["md", "json", "docx"]
