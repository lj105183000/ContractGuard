import pytest

from eval.gates import check_max, check_min, enforce


def test_min_and_max_pass():
    assert check_min("precision", 0.9, 0.8) is None
    assert check_max("p95", 90.0, 120.0) is None
    assert check_min("precision", None, None) is None


def test_min_and_max_fail():
    low = check_min("precision", 0.7, 0.8)
    high = check_max("p95", 130.0, 120.0)
    missing = check_min("recall", None, 0.5)
    assert low is not None and "下限" in low.describe()
    assert high is not None and "上限" in high.describe()
    assert missing is not None and "无可用数据" in missing.describe()


def test_enforce_raises_on_failure():
    with pytest.raises(SystemExit):
        enforce([check_min("precision", 0.5, 0.8)])
    enforce([check_min("precision", 0.9, 0.8)])
