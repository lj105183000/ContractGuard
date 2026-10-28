from app.schemas import Category, EvalLabel, Severity
from eval.iaa import compare_labels, cohen_kappa, label_key


def _label(label_id: str, category: Category = Category.PAYMENT) -> EvalLabel:
    return EvalLabel(
        id=label_id,
        clause_article="第三条",
        clause_id="cl_003",
        category=category,
        severity=Severity.HIGH,
        rule_id="PAY-001",
    )


def test_identical_annotations_have_kappa_one():
    labels = [_label("a1"), _label("a2", Category.DELIVERY)]
    agreement = compare_labels(labels, labels)
    assert agreement.cohen_kappa == 1.0
    assert agreement.both == 2
    assert agreement.only_a == 0
    assert agreement.only_b == 0


def test_partial_overlap_kappa_and_disagreements():
    labels_a = [_label("a1"), _label("a2", Category.DELIVERY)]
    labels_b = [_label("b1"), _label("b2", Category.DISPUTE)]
    agreement = compare_labels(labels_a, labels_b)
    assert agreement.both == 1
    assert agreement.only_a == 1
    assert agreement.only_b == 1
    assert agreement.cohen_kappa is not None
    assert len(agreement.disagreements) == 2


def test_cohen_kappa_known_two_by_two():
    a = {("cl_003", "payment", "PAY-001"), ("cl_004", "delivery", "DEL-001")}
    b = {("cl_003", "payment", "PAY-001")}
    assert cohen_kappa(a, b) == 0.0
    assert cohen_kappa(set(), set()) is None


def test_label_key_prefers_clause_id():
    label = _label("a1")
    assert label_key(label) == ("cl_003", "payment", "PAY-001")
