"""按人工复核结果生成合同修订稿（DOCX / Markdown）。

原则：
- 只应用已采纳（accepted）的风险建议；
- 模板建议只在原条款后原位标注，不直接改写原文；
- 只有 live 模式、模型返回的完整条款才允许整条替换；
- 同一条款多条建议全部标注，不静默丢弃；
- 合同级/定位失败的建议追加到文末“补充条款”。
"""

from __future__ import annotations

import io
import re
from datetime import datetime

from docx import Document

from app.reporting.report import (
    _add_paragraph,
    _set_east_asian_font,
    _set_run_font,
    _style_table,
)
from app.schemas import (
    Clause,
    Finding,
    FindingStatus,
    ReviewResult,
    RevisedChange,
    RevisedContract,
)

ARTICLE_HEADING_RE = re.compile(
    r"^\s*第[一二三四五六七八九十百千零〇两\d]+条"
    r"(?:之[一二三四五六七八九十]+)?(?:\s|[：:]|$)"
)
ANNOTATION_PREFIX = "【修订建议】"
SUPPLEMENT_HEADING = "补充条款（AI 建议，需人工确认）"
SUPPLEMENT_INTRO = "以下内容来自已采纳的合同级或定位失败建议，需人工确认后并入正文。"
CHANGE_LABELS = {
    "replace": "自动替换",
    "annotate": "原位批注",
    "append": "补充条款",
}
RISK_LEVEL_LABELS = {
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
}
DEFAULT_DISCLAIMER = (
    "本报告由 AI 辅助生成，仅用于合同风险初筛，不构成法律意见。"
    "所有风险结论，尤其是高风险项，必须由具备法律知识的人员复核后再使用。"
)
AI_REVISION_NOTE = (
    "本修订稿由 AI 根据人工采纳的审查建议自动生成，仅作为修订草稿；"
    "所有条款必须由具备法律知识的人员复核后使用。"
)


class RevisedContractError(ValueError):
    """无法从当前任务生成修订稿（例如缺少合同全文）。"""


def _finding_text(finding: Finding) -> str:
    return (finding.suggested_text or finding.recommendation or "").strip()


def _severity_label(finding: Finding) -> str:
    return RISK_LEVEL_LABELS.get(finding.severity.value, finding.severity.value)


def _disclaimer(report: ReviewResult) -> str:
    base = (report.disclaimer or DEFAULT_DISCLAIMER).strip()
    if AI_REVISION_NOTE in base:
        return base
    return f"{base} {AI_REVISION_NOTE}"


def _annotation_block(finding: Finding, label: str) -> str:
    text = _finding_text(finding)
    if not text:
        return ""
    lines = [
        f"{ANNOTATION_PREFIX}（{label}）[{_severity_label(finding)}] {finding.title}",
        f"建议：{text}",
    ]
    note = finding.review_note.strip()
    if note:
        lines.append(f"人工意见：{note}")
    return "\n".join(lines)


def _article_heading(clause_text: str) -> str:
    first = clause_text.split("\n", 1)[0].strip()
    return first if ARTICLE_HEADING_RE.match(first) else ""


def _replacement_text(suggestion: str, clause_text: str) -> str:
    suggestion = suggestion.strip()
    heading = _article_heading(clause_text)
    first = suggestion.split("\n", 1)[0].strip()
    if heading and not ARTICLE_HEADING_RE.match(first):
        return f"{heading}\n{suggestion}"
    return suggestion


def _supplement_entry(
    finding: Finding, index: int, scope: str, text: str
) -> str:
    lines = [f"{index}. {finding.title}（{scope}）", text]
    note = finding.review_note.strip()
    if note:
        lines.append(f"人工意见：{note}")
    return "\n".join(lines)


def build_revised_contract(
    report: ReviewResult, job_id: str | None = None
) -> RevisedContract:
    if not report.full_text.strip():
        raise RevisedContractError("合同全文不可用，无法生成修订稿。")

    accepted = [
        finding
        for finding in report.findings
        if finding.status == FindingStatus.ACCEPTED
    ]
    revised = RevisedContract(
        job_id=job_id,
        contract_title=report.contract.title or "",
        source_filename=report.contract.filename or "",
        text=report.full_text,
        accepted_count=len(accepted),
        mode=report.mode,
        disclaimer=_disclaimer(report),
    )
    if not accepted:
        revised.warnings.append("没有已采纳的风险，修订稿与原文一致。")
        return revised

    clauses_by_id = {clause.id: clause for clause in report.clauses}
    finding_order = {finding.id: index for index, finding in enumerate(report.findings)}
    groups: dict[str, list[Finding]] = {}
    append_findings: list[tuple[Finding, str]] = []

    for finding in accepted:
        if finding.clause_id is None:
            append_findings.append((finding, "合同级"))
        elif finding.clause_id not in clauses_by_id:
            append_findings.append((finding, "条款定位失败"))
            revised.warnings.append(
                f"风险 {finding.id} 指向不存在的条款 {finding.clause_id}，"
                "已追加到补充条款。"
            )
        else:
            groups.setdefault(finding.clause_id, []).append(finding)

    valid_groups: list[tuple[Clause, list[Finding]]] = []
    for clause_id, findings in groups.items():
        clause = clauses_by_id[clause_id]
        valid = (
            0 <= clause.start < clause.end <= len(report.full_text)
            and report.full_text[clause.start : clause.end] == clause.text
        )
        if not valid:
            for finding in findings:
                append_findings.append((finding, "条款定位失败"))
            revised.warnings.append(
                f"条款 {clause_id} 的原文定位不一致，相关建议已追加到补充条款。"
            )
            continue
        valid_groups.append((clause, findings))

    # 重叠条款无法安全地同时替换，后来的整组转入补充条款。
    valid_groups.sort(key=lambda item: item[0].start)
    non_overlapping: list[tuple[Clause, list[Finding]]] = []
    last_end = -1
    for clause, findings in valid_groups:
        if clause.start < last_end:
            for finding in findings:
                append_findings.append((finding, "条款范围重叠"))
            revised.warnings.append(
                f"条款 {clause.id} 与前一条款范围重叠，相关建议已追加到补充条款。"
            )
            continue
        non_overlapping.append((clause, findings))
        last_end = clause.end

    edits: list[tuple[int, int, str]] = []
    clause_changes: list[tuple[int, RevisedChange]] = []

    for clause, findings in non_overlapping:
        texts = [(finding, _finding_text(finding)) for finding in findings]
        non_empty = [(finding, text) for finding, text in texts if text]
        for finding, text in texts:
            if not text:
                revised.skipped_count += 1
                revised.warnings.append(
                    f"风险 {finding.id} 没有可用的修改文本，已跳过。"
                )

        source = findings[0].suggested_text_source
        replace = (
            len(findings) == 1
            and len(non_empty) == 1
            and (
                source == "human"
                or (report.mode == "live" and source == "llm")
            )
        )
        if replace and source == "llm" and len(non_empty[0][1]) < 10:
            replace = False
            reason = "模型建议过短，疑似非完整条款"
        elif replace:
            reason = (
                "人工编辑条款，已替换原文"
                if source == "human"
                else "模型生成完整条款，已自动替换"
            )
        elif len(findings) > 1:
            reason = (
                f"同一条款存在 {len(findings)} 条已采纳建议，已全部原位标注"
            )
        elif source == "human":
            reason = "人工编辑建议为空，未替换原文"
        elif report.mode != "live":
            reason = "非 live 模式，未自动替换原文"
        elif source != "llm":
            reason = "模板建议，未自动替换原文"
        else:
            reason = "模型建议过短，疑似非完整条款"

        if replace:
            finding, suggestion = non_empty[0]
            replacement = _replacement_text(suggestion, clause.text)
            edits.append((clause.start, clause.end, replacement))
            revised.replaced_count += 1
            clause_changes.append(
                (
                    clause.start,
                    RevisedChange(
                        finding_id=finding.id,
                        clause_id=clause.id,
                        clause_article=clause.article,
                        change_type="replace",
                        title=finding.title,
                        severity=finding.severity,
                        suggestion_source=finding.suggested_text_source,
                        original_text=clause.text,
                        revised_text=replacement,
                        review_note=finding.review_note,
                        reason=reason,
                    ),
                )
            )
            continue

        blocks: list[str] = []
        for finding, text in non_empty:
            if len(findings) > 1:
                if finding.suggested_text_source == "llm":
                    label = "模型生成，需人工合并"
                elif finding.suggested_text_source == "human":
                    label = "人工编辑，需人工合并"
                else:
                    label = "模板建议，未自动替换原文"
            elif finding.suggested_text_source == "human":
                label = "人工编辑建议为空，未自动替换"
            elif finding.suggested_text_source == "llm":
                label = (
                    "模型建议过短，未自动替换"
                    if len(text) < 10
                    else "非 live 模式，未自动替换"
                )
            else:
                label = "模板建议，未自动替换原文"
            block = _annotation_block(finding, label)
            if not block:
                continue
            blocks.append(block)
            revised.annotated_count += 1
            clause_changes.append(
                (
                    clause.start,
                    RevisedChange(
                        finding_id=finding.id,
                        clause_id=clause.id,
                        clause_article=clause.article,
                        change_type="annotate",
                        title=finding.title,
                        severity=finding.severity,
                        suggestion_source=finding.suggested_text_source,
                        original_text=clause.text,
                        revised_text=block,
                        review_note=finding.review_note,
                        reason=reason,
                    ),
                )
            )
        if blocks:
            replacement = f"{clause.text}\n" + "\n".join(blocks)
            edits.append((clause.start, clause.end, replacement))

    text = report.full_text
    for start, end, replacement in sorted(
        edits, key=lambda item: item[0], reverse=True
    ):
        text = text[:start] + replacement + text[end:]

    append_changes: list[RevisedChange] = []
    append_findings.sort(key=lambda item: finding_order.get(item[0].id, 0))
    entries: list[str] = []
    for finding, scope in append_findings:
        suggestion = _finding_text(finding)
        if not suggestion:
            revised.skipped_count += 1
            revised.warnings.append(
                f"风险 {finding.id} 没有可用的修改文本，已跳过。"
            )
            continue
        index = len(entries) + 1
        entry = _supplement_entry(finding, index, scope, suggestion)
        entries.append(entry)
        revised.appended_count += 1
        append_changes.append(
            RevisedChange(
                finding_id=finding.id,
                clause_id=finding.clause_id,
                clause_article=finding.clause_article,
                change_type="append",
                title=finding.title,
                severity=finding.severity,
                suggestion_source=finding.suggested_text_source,
                original_text="",
                revised_text=entry,
                review_note=finding.review_note,
                reason=scope,
            )
        )

    if entries:
        section = (
            f"{SUPPLEMENT_HEADING}\n\n{SUPPLEMENT_INTRO}\n\n"
            + "\n\n".join(entries)
        )
        text = text.rstrip() + "\n\n" + section + "\n"

    revised.text = text
    revised.changes = [
        change for _start, change in sorted(clause_changes, key=lambda item: item[0])
    ] + append_changes
    return revised


def build_revised_markdown(revised: RevisedContract) -> str:
    lines = ["# 合同修订稿", ""]
    if revised.contract_title or revised.source_filename:
        lines.append(f"- 原合同：{revised.contract_title or revised.source_filename}")
    lines.extend(
        [
            f"- 生成时间：{revised.generated_at}",
            f"- 运行模式：{revised.mode}",
            (
                f"- 已采纳：{revised.accepted_count} 条（替换 "
                f"{revised.replaced_count} / 标注 {revised.annotated_count} / "
                f"追加 {revised.appended_count} / 跳过 {revised.skipped_count}）"
            ),
            "",
            f"> {revised.disclaimer}",
            "",
            "## 修订后合同全文",
            "",
            "~~~~text",
            revised.text,
            "~~~~",
            "",
            "## 修订变更清单",
            "",
        ]
    )
    if not revised.changes:
        lines.append("- 没有已采纳的修改。")
    for index, change in enumerate(revised.changes, start=1):
        lines.append(
            f"{index}. **{change.clause_article or '合同级'}** · "
            f"{CHANGE_LABELS[change.change_type]} · "
            f"{RISK_LEVEL_LABELS.get(change.severity.value, change.severity.value)} · "
            f"{change.title}"
        )
        if change.reason:
            lines.append(f"   - 原因：{change.reason}")
        if change.review_note:
            lines.append(f"   - 人工意见：{change.review_note}")
    if revised.warnings:
        lines.extend(["", "## 提示", ""])
        lines.extend(f"- {warning}" for warning in revised.warnings)
    return "\n".join(lines)


def build_revised_docx(revised: RevisedContract) -> bytes:
    doc = Document()
    _set_east_asian_font(doc.styles["Normal"])
    for style_name in ("Heading 1", "Heading 2", "Heading 3", "Title"):
        try:
            _set_east_asian_font(doc.styles[style_name], name="微软雅黑")
        except KeyError:
            pass

    title = revised.contract_title or revised.source_filename or "采购合同"
    heading = doc.add_heading(f"合同修订稿：{title}", level=0)
    for run in heading.runs:
        _set_run_font(run, size=20, bold=True)

    disclaimer = doc.add_paragraph()
    run = disclaimer.add_run(revised.disclaimer)
    _set_run_font(run, size=9)
    run.italic = True

    doc.add_heading("基本信息", level=1)
    for text in [
        f"原文件：{revised.source_filename or '—'}",
        f"生成时间：{revised.generated_at}",
        f"运行模式：{revised.mode}",
        (
            f"已采纳：{revised.accepted_count} 条（替换 "
            f"{revised.replaced_count} / 标注 {revised.annotated_count} / "
            f"追加 {revised.appended_count} / 跳过 {revised.skipped_count}）"
        ),
    ]:
        _add_paragraph(doc, text)

    doc.add_heading("修订后合同全文", level=1)
    for line in revised.text.splitlines():
        if not line.strip():
            doc.add_paragraph()
            continue
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(line)
        bold = bool(ARTICLE_HEADING_RE.match(line)) or line.startswith(
            ANNOTATION_PREFIX
        )
        _set_run_font(run, bold=bold)

    doc.add_page_break()
    doc.add_heading("修订变更清单", level=1)
    if revised.changes:
        table = doc.add_table(rows=1, cols=5)
        headers = ["#", "条款", "风险", "处理方式", "人工意见"]
        for index, header in enumerate(headers):
            cell = table.rows[0].cells[index]
            cell.text = header
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    _set_run_font(run, bold=True)
        for index, change in enumerate(revised.changes, start=1):
            cells = table.add_row().cells
            cells[0].text = str(index)
            cells[1].text = change.clause_article or "合同级"
            cells[2].text = change.title
            cells[3].text = CHANGE_LABELS[change.change_type]
            cells[4].text = change.review_note or "—"
        _style_table(table)
    else:
        _add_paragraph(doc, "没有已采纳的修改。")

    doc.add_heading("逐条对照", level=1)
    for index, change in enumerate(revised.changes, start=1):
        doc.add_heading(
            f"{index}. {change.clause_article or '合同级'} · {change.title}",
            level=2,
        )
        _add_paragraph(
            doc,
            f"处理方式：{CHANGE_LABELS[change.change_type]}；"
            f"等级：{RISK_LEVEL_LABELS.get(change.severity.value, change.severity.value)}；"
            f"原因：{change.reason or '—'}",
        )
        if change.original_text:
            _add_paragraph(doc, f"原条款：{change.original_text}")
        _add_paragraph(doc, f"修订后/建议：{change.revised_text}")
        if change.review_note:
            _add_paragraph(doc, f"人工意见：{change.review_note}")

    if revised.warnings:
        doc.add_heading("提示", level=1)
        for warning in revised.warnings:
            _add_paragraph(doc, f"• {warning}")

    final = doc.add_paragraph()
    run = final.add_run(revised.disclaimer)
    _set_run_font(run, size=9)
    run.italic = True

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def safe_revised_filename(report: ReviewResult, extension: str) -> str:
    stem = (
        report.contract.title or report.contract.filename or "采购合同"
    ).strip()
    stem = (
        "".join(char for char in stem if char not in '\\/:*?"<>|')
        .replace(" ", "_")
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    return f"ContractGuard_合同修订稿_{stem}_{timestamp}.{extension}"
