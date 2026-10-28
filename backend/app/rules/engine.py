"""YAML Playbook 规则引擎。

设计要点：
- 条件求值使用三值逻辑：True / False / None（未知）。
  None 表示事实没有抽取到，规则应跳过，而不是当成 False 误报。
- 只支持白名单操作符，不执行任意代码。
- 规则命中必须尽量定位到条款；条款缺失类规则允许是“合同级风险”。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.schemas import (
    Category,
    Clause,
    ClauseType,
    ContractFacts,
    Finding,
    FindingStatus,
    PlaybookRule,
    Severity,
    Source,
)


# ---------------------------------------------------------------------------
# 数据容器
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    clause_id: str | None = None
    clause_article: str | None = None
    clause_text: str | None = None
    quote: str = ""
    start: int | None = None
    end: int | None = None


@dataclass
class ConditionResult:
    matched: bool | None
    evidence: Evidence | None = None
    reason: str = ""


@dataclass
class RuleEvaluation:
    rule_id: str
    title: str
    result: str  # matched / not_matched / skipped
    reason: str


@dataclass
class RuleEngineResult:
    findings: list[Finding] = field(default_factory=list)
    evaluations: list[RuleEvaluation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RuleContext:
    clauses: list[Clause]
    facts: ContractFacts
    full_text: str
    facts_dict: dict[str, Any] = field(init=False)
    clause_types: set[ClauseType] = field(init=False)

    def __post_init__(self) -> None:
        self.facts_dict = self.facts.model_dump()
        self.clause_types = {clause.clause_type for clause in self.clauses}

    def clauses_of(self, types: list[ClauseType]) -> list[Clause]:
        if not types:
            return list(self.clauses)
        wanted = set(types)
        return [clause for clause in self.clauses if clause.clause_type in wanted]

    def clause_at(self, offset: int) -> Clause | None:
        for clause in self.clauses:
            if clause.start <= offset < clause.end:
                return clause
        return None


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def load_playbook(path: Path) -> list[PlaybookRule]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"playbook 必须是列表：{path}")
    rules = [PlaybookRule.model_validate(item) for item in data]
    ids = [rule.id for rule in rules]
    duplicates = {rule_id for rule_id in ids if ids.count(rule_id) > 1}
    if duplicates:
        raise ValueError(f"playbook 规则 ID 重复：{sorted(duplicates)}")
    return rules


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def get_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _sentence_window(text: str, start: int, end: int, limit: int = 300) -> tuple[int, int]:
    """把匹配扩展到一个较自然的句子窗口，并限制长度。"""
    left = max(0, start - 100)
    right = min(len(text), end + 100)
    sentence_breaks = "。；;\n"
    for index in range(left, min(start, len(text))):
        if index > 0 and text[index - 1] in sentence_breaks:
            left = index
            break
    for index in range(right, min(len(text), right + 100)):
        if text[index] in sentence_breaks:
            right = index + 1
            break
    if right - left > limit:
        right = min(len(text), left + limit)
    return left, right


def _evidence_for_clause(
    clause: Clause, match_start: int | None = None, match_end: int | None = None
) -> Evidence:
    text = clause.text
    if match_start is None:
        start, end = 0, min(len(text), 300)
    else:
        start, end = _sentence_window(text, match_start, match_end or match_start + 1)
    quote = text[start:end].strip()
    return Evidence(
        clause_id=clause.id,
        clause_article=clause.article,
        clause_text=text,
        quote=quote,
        start=start,
        end=end,
    )


def _evidence_from_match(ctx: RuleContext, match: re.Match[str]) -> Evidence:
    clause = ctx.clause_at(match.start())
    if clause is not None:
        return _evidence_for_clause(
            clause, match.start() - clause.start, match.end() - clause.start
        )
    start, end = _sentence_window(ctx.full_text, match.start(), match.end())
    return Evidence(quote=ctx.full_text[start:end].strip())


def _first_clause_evidence(ctx: RuleContext, types: list[ClauseType]) -> Evidence | None:
    """按 applies_to 的顺序选择证据条款。

    例如 LIA-004 的 applies_to 是 [warranty, liability, acceptance]，
    应优先引用质保条款，而不是文档中更靠前的验收条款。
    """
    if not types:
        return _evidence_for_clause(ctx.clauses[0]) if ctx.clauses else None
    for clause_type in types:
        for clause in ctx.clauses:
            if clause.clause_type == clause_type:
                return _evidence_for_clause(clause)
    return None


# ---------------------------------------------------------------------------
# 条件求值
# ---------------------------------------------------------------------------


def evaluate_condition(
    condition: dict[str, Any],
    ctx: RuleContext,
    applies_to: list[ClauseType] | None = None,
) -> ConditionResult:
    kind = str(condition.get("type", "")).strip()
    scope = applies_to or []

    if kind == "clause_type_absent":
        raw = condition.get("value")
        try:
            target = ClauseType(str(raw))
        except ValueError:
            return ConditionResult(None, reason=f"未知条款类型：{raw}")
        absent = target not in ctx.clause_types
        return ConditionResult(
            absent,
            evidence=None,
            reason=f"条款类型 {target.value} {'缺失' if absent else '存在'}",
        )

    if kind == "fact":
        field_name = str(condition.get("field", ""))
        op = str(condition.get("op", "==")).strip()
        expected = condition.get("value")
        actual = get_path(ctx.facts_dict, field_name)
        matched = _compare(actual, op, expected)
        evidence = _first_clause_evidence(ctx, _clause_types(condition, scope))
        return ConditionResult(
            matched,
            evidence=evidence if matched else None,
            reason=f"{field_name}={actual!r} {op} {expected!r} -> {matched}",
        )

    if kind == "regex":
        pattern = str(condition.get("pattern", ""))
        target = str(condition.get("target", "full_text"))
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return ConditionResult(None, reason=f"正则错误：{exc}")
        if target == "clause_text":
            for clause in ctx.clauses_of(_clause_types(condition, scope)):
                match = regex.search(clause.text)
                if match:
                    return ConditionResult(
                        True,
                        evidence=_evidence_for_clause(clause, match.start(), match.end()),
                        reason=f"条款 {clause.article} 命中 /{pattern}/",
                    )
            return ConditionResult(False, reason=f"条款文本未命中 /{pattern}/")
        match = regex.search(ctx.full_text)
        if match:
            return ConditionResult(
                True,
                evidence=_evidence_from_match(ctx, match),
                reason=f"全文命中 /{pattern}/",
            )
        return ConditionResult(False, reason=f"全文未命中 /{pattern}/")

    if kind == "all":
        results = [
            evaluate_condition(item, ctx, scope) for item in condition.get("conditions", [])
        ]
        if any(result.matched is False for result in results):
            failed = next(result for result in results if result.matched is False)
            return ConditionResult(False, reason=f"all 失败：{failed.reason}")
        if any(result.matched is None for result in results):
            return ConditionResult(None, reason="all 存在未知条件")
        evidence = next((result.evidence for result in results if result.evidence), None)
        return ConditionResult(True, evidence=evidence, reason="all 全部成立")

    if kind == "any":
        results = [
            evaluate_condition(item, ctx, scope) for item in condition.get("conditions", [])
        ]
        true_results = [result for result in results if result.matched is True]
        if true_results:
            evidence = next((result.evidence for result in true_results if result.evidence), None)
            return ConditionResult(True, evidence=evidence, reason="any 至少一项成立")
        if any(result.matched is None for result in results):
            return ConditionResult(None, reason="any 无真值但存在未知条件")
        return ConditionResult(False, reason="any 全部不成立")

    if kind == "not":
        inner = evaluate_condition(condition.get("condition", {}), ctx, scope)
        if inner.matched is None:
            return ConditionResult(None, reason=f"not 内部未知：{inner.reason}")
        return ConditionResult(not inner.matched, evidence=None, reason=f"not({inner.reason})")

    return ConditionResult(None, reason=f"未知条件类型：{kind}")


def _clause_types(
    condition: dict[str, Any], default_scope: list[ClauseType] | None = None
) -> list[ClauseType]:
    raw = condition.get("clause_types") or condition.get("applies_to")
    if not raw:
        return list(default_scope or [])
    result = []
    for item in raw:
        try:
            result.append(ClauseType(str(item)))
        except ValueError:
            continue
    return result


def _compare(actual: Any, op: str, expected: Any) -> bool | None:
    if op == "is_null":
        return actual is None
    if op == "not_null":
        return actual is not None
    if actual is None:
        return None
    if op in {"contains", "not_contains"}:
        if isinstance(actual, str):
            found = str(expected).casefold() in actual.casefold()
        elif isinstance(actual, (list, tuple, set)):
            found = expected in actual
        else:
            return None
        return found if op == "contains" else not found
    if op in {"==", "!="}:
        if isinstance(actual, bool) or isinstance(expected, bool):
            equal = bool(actual) == bool(expected)
        else:
            try:
                equal = float(actual) == float(expected)
            except (TypeError, ValueError):
                equal = str(actual).strip() == str(expected).strip()
        return equal if op == "==" else not equal
    try:
        left = float(actual)
        right = float(expected)
    except (TypeError, ValueError):
        return None
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def evaluate_rules(
    rules: list[PlaybookRule],
    clauses: list[Clause],
    facts: ContractFacts,
    full_text: str,
) -> RuleEngineResult:
    ctx = RuleContext(clauses=clauses, facts=facts, full_text=full_text)
    result = RuleEngineResult()
    for rule in rules:
        if not rule.enabled:
            result.evaluations.append(
                RuleEvaluation(rule.id, rule.title, "skipped", "规则已禁用")
            )
            continue
        try:
            condition_result = evaluate_condition(rule.condition, ctx, rule.applies_to)
        except Exception as exc:  # 单条规则异常不拖垮整条流水线
            message = f"规则 {rule.id} 执行失败：{exc}"
            result.warnings.append(message)
            result.evaluations.append(RuleEvaluation(rule.id, rule.title, "skipped", message))
            continue

        if condition_result.matched is None:
            result.evaluations.append(
                RuleEvaluation(rule.id, rule.title, "skipped", condition_result.reason)
            )
            continue
        if not condition_result.matched:
            result.evaluations.append(
                RuleEvaluation(rule.id, rule.title, "not_matched", condition_result.reason)
            )
            continue

        evidence = condition_result.evidence or Evidence()
        finding = Finding(
            id=f"rule-{rule.id}-{evidence.clause_id or 'contract'}",
            clause_id=evidence.clause_id,
            clause_article=evidence.clause_article,
            clause_text=evidence.clause_text,
            category=rule.category,
            severity=rule.severity,
            source=Source.RULE,
            title=rule.title,
            analysis=rule.description or rule.recommendation,
            evidence_quote=evidence.quote,
            evidence_start=evidence.start,
            evidence_end=evidence.end,
            legal_basis_ids=list(rule.legal_basis),
            rule_id=rule.id,
            confidence=1.0,
            recommendation=rule.recommendation,
            fallback=rule.fallback,
            status=FindingStatus.PENDING,
            gate_notes=["规则命中：依据为条款内容或条款缺失，建议人工确认适用范围"]
            if not evidence.quote
            else [],
        )
        result.findings.append(finding)
        result.evaluations.append(
            RuleEvaluation(rule.id, rule.title, "matched", condition_result.reason)
        )
    return result
