"""证据定位：把模型引用的原文片段映射回合同条款的字符区间。"""

from __future__ import annotations

import re


def find_quote_span(text: str, quote: str) -> tuple[int, int] | None:
    """返回 quote 在 text 中的 (start, end)。

    先精确查找；失败后用“字符间允许空白”的正则再试一次，
    以容忍模型在复制时增删了空格/换行。
    """
    quote = (quote or "").strip()
    if not quote or not text:
        return None
    index = text.find(quote)
    if index >= 0:
        return index, index + len(quote)
    compact = "".join(char for char in quote if not char.isspace())
    if len(compact) < 2:
        return None
    pattern = r"\s*".join(re.escape(char) for char in compact[:500])
    match = re.search(pattern, text)
    if match:
        return match.start(), match.end()
    return None
