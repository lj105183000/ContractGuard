import pytest

from app.rag.retriever import get_retriever, reset_retriever


@pytest.fixture(autouse=True)
def fresh_retriever():
    reset_retriever()
    yield
    reset_retriever()


async def test_bm25_law_retrieval():
    retriever = get_retriever()
    results = await retriever.retrieve(
        "预付款比例过高 付款方式 验收后支付 违约金",
        top_k=6,
        doc_types=["law"],
    )
    assert results
    assert all(item.document.doc_type == "law" for item in results)
    ids = {item.document.id for item in results}
    assert ids & {"CC-626", "CC-628", "CC-585", "CC-577", "CC-509"}
    assert retriever.retrieval_mode == "bm25_only"


async def test_rule_retrieval_filtered_by_category():
    retriever = get_retriever()
    results = await retriever.retrieve(
        "预付款 付款期限",
        top_k=5,
        doc_types=["rule"],
        categories=["payment"],
    )
    assert results
    assert all(item.document.doc_type == "rule" for item in results)
    assert all(item.document.metadata["category"] == "payment" for item in results)
