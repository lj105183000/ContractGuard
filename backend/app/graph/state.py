"""LangGraph 状态。

并行分支只写 findings / warnings，所以这两个字段用 operator.add 合并；
其余字段由单一节点写入，避免并发覆盖。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from app.llm.trace import TraceCollector
from app.schemas import Clause, ContractFacts, Finding, ReviewResult


class ReviewState(TypedDict, total=False):
    job_id: str
    filename: str
    source_path: str
    llm_route: str | None
    llm_mode: str

    contract_text: str
    source_type: str
    page_count: int
    page_offsets: list[int]
    title: str | None

    clauses: list[Clause]
    facts: ContractFacts

    findings: Annotated[list[Finding], operator.add]
    warnings: Annotated[list[str], operator.add]

    trace: TraceCollector
    report: ReviewResult | None
