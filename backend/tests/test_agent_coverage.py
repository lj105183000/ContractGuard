from app.graph.nodes.agents import _select_clauses
from app.llm.prompts import AGENTS
from app.schemas import Clause, ClauseType


def test_agent_clause_cap_emits_warning():
    clauses = [
        Clause(
            id=f"cl_{index:03d}",
            index=index,
            article=f"第{index + 1}条",
            text=f"第{index + 1}条 付款条款 {index}",
            start=index * 20,
            end=index * 20 + 10,
            clause_type=ClauseType.PAYMENT,
        )
        for index in range(11)
    ]
    selected, warning = _select_clauses(AGENTS[0], clauses, limit=10)
    assert len(selected) == 10
    assert warning is not None
    assert "仅审查前 10 条" in warning
