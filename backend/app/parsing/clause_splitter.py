"""把合同文本切分成条款，并保留原文偏移与页码。

优先识别「第X条」「1.」「一、」等标题；识别不到时退回段落/句子切分。
切分必须是确定性的，方便评测标签稳定复现。
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from app.parsing.contract_parser import ParsedContract
from app.schemas import Clause, ClauseType

ARTICLE_PATTERNS = [
    re.compile(r"(?m)^[ \t]*(第[一二三四五六七八九十百千零〇\d]+条)[ \t　]*(?=\S|$)"),
    re.compile(r"(?m)^[ \t]*(\d+(?:\.\d+){0,3})[、.．][ \t]*(?=\S|$)"),
    re.compile(r"(?m)^[ \t]*([一二三四五六七八九十]+)[、.．][ \t]*(?=\S|$)"),
]

TYPE_KEYWORDS: dict[ClauseType, list[str]] = {
    ClauseType.FORCE_MAJEURE: ["不可抗力"],
    ClauseType.DISPUTE: ["争议", "管辖", "仲裁", "诉讼", "法院"],
    ClauseType.CONFIDENTIALITY: ["保密", "商业秘密"],
    ClauseType.IP: ["知识产权", "著作权", "软件著作权", "专利", "商标", "源代码"],
    ClauseType.TERMINATION: ["解除", "终止", "续约", "续期", "顺延"],
    ClauseType.WARRANTY: ["质保", "保修", "质量保证", "三包"],
    ClauseType.LIABILITY: ["违约", "赔偿", "责任", "罚款", "滞纳金"],
    ClauseType.ACCEPTANCE: ["验收", "检验", "签收", "质量异议"],
    ClauseType.DELIVERY: ["交付", "交货", "运输", "发货", "到货", "送货"],
    ClauseType.PAYMENT: ["付款", "支付", "预付款", "账期", "结算", "发票", "质保金"],
    ClauseType.PRICE: ["合同总价", "合同金额", "总金额", "单价", "价款", "价格"],
    ClauseType.SUBJECT: ["标的", "产品", "货物", "设备", "品名", "规格", "数量"],
    ClauseType.ANNEX: ["附件", "附录"],
    ClauseType.GENERAL: ["生效", "签字", "盖章", "其他"],
}

TYPE_PRIORITY = [
    ClauseType.PREAMBLE,
    ClauseType.FORCE_MAJEURE,
    ClauseType.DISPUTE,
    ClauseType.CONFIDENTIALITY,
    ClauseType.IP,
    ClauseType.TERMINATION,
    ClauseType.WARRANTY,
    ClauseType.LIABILITY,
    ClauseType.ACCEPTANCE,
    ClauseType.DELIVERY,
    ClauseType.PAYMENT,
    ClauseType.PRICE,
    ClauseType.SUBJECT,
    ClauseType.ANNEX,
    ClauseType.GENERAL,
    ClauseType.OTHER,
]


@dataclass
class _Span:
    start: int
    end: int
    article: str


def infer_clause_type(text: str, is_first: bool = False) -> ClauseType:
    if is_first and "甲方" in text and "乙方" in text and not any(
        keyword in text for keyword in ("付款", "交付", "验收", "违约")
    ):
        return ClauseType.PREAMBLE
    scores = {
        clause_type: sum(1 for keyword in keywords if keyword in text)
        for clause_type, keywords in TYPE_KEYWORDS.items()
    }
    best_score = max(scores.values(), default=0)
    if best_score == 0:
        return ClauseType.OTHER
    for clause_type in TYPE_PRIORITY:
        if scores.get(clause_type, 0) == best_score:
            return clause_type
    return ClauseType.OTHER


def _page_for_offset(offset: int, page_offsets: list[int]) -> int | None:
    if not page_offsets:
        return None
    return bisect.bisect_right(page_offsets, offset)


def _make_clause(
    text: str,
    start: int,
    end: int,
    article: str,
    index: int,
    parsed: ParsedContract,
) -> Clause | None:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return None
    left = len(raw) - len(raw.lstrip())
    start += left
    end = start + len(stripped)
    return Clause(
        id=f"cl_{index:03d}",
        index=index,
        article=article,
        text=stripped,
        start=start,
        end=end,
        page=_page_for_offset(start, parsed.page_offsets),
        clause_type=infer_clause_type(stripped, is_first=index == 0),
    )


def _heading_spans(text: str) -> list[_Span]:
    matches: list[tuple[int, int, str]] = []
    for pattern in ARTICLE_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(1), match.start(), match.group(1).strip()))
    # 去重：同一行可能被多个模式命中，保留最早的
    matches.sort(key=lambda item: (item[0], item[1]))
    deduped: list[tuple[int, int, str]] = []
    for item in matches:
        if deduped and item[0] <= deduped[-1][0]:
            continue
        deduped.append(item)

    spans: list[_Span] = []
    for index, (start, _line_start, article) in enumerate(deduped):
        end = deduped[index + 1][0] if index + 1 < len(deduped) else len(text)
        spans.append(_Span(start=start, end=end, article=article))
    return spans


def _paragraph_spans(text: str) -> list[_Span]:
    pattern = re.compile(r"(?s)(?:\A|\n\s*\n)(.*?)(?=\n\s*\n|\Z)")
    spans: list[_Span] = []
    for match in pattern.finditer(text):
        start = match.start(1)
        raw = match.group(1)
        stripped = raw.strip()
        if not stripped:
            continue
        left = len(raw) - len(raw.lstrip())
        spans.append(
            _Span(start=start + left, end=start + left + len(stripped), article=f"第{len(spans)+1}段")
        )
    # 把过短的块并入下一块，避免只有标题的“条款”
    merged: list[_Span] = []
    for span in spans:
        if merged and len(text[merged[-1].start : merged[-1].end]) < 30:
            merged[-1].end = span.end
        else:
            merged.append(span)
    return merged


def _sentence_spans(text: str) -> list[_Span]:
    spans: list[_Span] = []
    start = 0
    buffer_start = 0
    for match in re.finditer(r"[^。\n]+[。]?", text):
        if not match.group(0).strip():
            continue
        if match.end() - buffer_start >= 300 and match.end() > buffer_start:
            spans.append(
                _Span(start=buffer_start, end=match.end(), article=f"第{len(spans)+1}段")
            )
            buffer_start = match.end()
        start = match.end()
    if buffer_start < len(text):
        spans.append(_Span(start=buffer_start, end=len(text), article=f"第{len(spans)+1}段"))
    return spans


def split_clauses(parsed: ParsedContract) -> list[Clause]:
    text = parsed.text
    heading_spans = _heading_spans(text)
    spans: list[_Span]
    if len(heading_spans) >= 2:
        spans = heading_spans
    else:
        spans = _paragraph_spans(text)
        if len(spans) < 2:
            spans = _sentence_spans(text)
    if not spans:
        spans = [_Span(start=0, end=len(text), article="全文")]

    clauses: list[Clause] = []
    first_start = spans[0].start
    if first_start > 0:
        preamble = _make_clause(text, 0, first_start, "首部", 0, parsed)
        if preamble and len(preamble.text) >= 5:
            clauses.append(preamble)

    for span in spans:
        clause = _make_clause(
            text, span.start, span.end, span.article, len(clauses), parsed
        )
        if clause and clause.text:
            clauses.append(clause)
    if not clauses:
        fallback = _make_clause(text, 0, len(text), "全文", 0, parsed)
        if fallback:
            clauses.append(fallback)
    return clauses
