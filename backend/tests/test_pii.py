from app.pii import redact_text, scan_text


def test_scan_text_counts_common_pii():
    text = (
        "甲方：星海智能装备有限公司\n"
        "联系人邮箱 buyer@example.com，电话 13800138000。\n"
        "身份证 110101199001011234，账号 6222021234567890。"
    )
    counts = scan_text(text)
    assert counts["email"] == 1
    assert counts["phone"] == 1
    assert counts["id_card"] == 1
    assert counts["bank_account"] == 1
    assert counts["company"] >= 1


def test_redact_text_removes_raw_values():
    text = "星海智能装备有限公司 buyer@example.com 13800138000 110101199001011234"
    redacted = redact_text(text)
    for value in ("星海智能装备有限公司", "buyer@example.com", "13800138000", "110101199001011234"):
        assert value not in redacted
    assert "<COMPANY>" in redacted
    assert "<EMAIL>" in redacted
    assert "<PHONE>" in redacted
    assert "<ID_CARD>" in redacted


def test_scan_empty_text():
    assert all(value == 0 for value in scan_text("").values())
    assert redact_text("") == ""
