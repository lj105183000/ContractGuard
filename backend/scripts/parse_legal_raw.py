"""临时脚本：从政府网页提取法条。

只使用标准库，便于在隔离模式下运行：
    python -I scripts/parse_legal_raw.py <html> <out.json>

切分规则：
- 只把「行首 + 条文号 + 空格」当作条标题；
- 正文里「依据本法第五百一十条」这类行内引用不在行首，不会被误切；
- 同一网页可能重复刊载全文，取正文最长的版本，并在入库前人工抽查。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ARTICLE_LINE_RE = re.compile(
    r"(?m)^(第[一二三四五六七八九十百千零〇\d]+条)[ 　]+"
)

NOISE_LINE_RE = re.compile(
    r"^(责任编辑|来源[:：]|上一篇|下一篇|打印|关闭|扫一扫|返回顶部|主办单位|"
    r"网站标识码|ICP备|地址[:：]|邮编[:：]|联系电话[:：]|版权所有)"
)

UNICODE_SPACES = (" ", " ", " ", " ", "　", " ")


def decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def clean_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(p|div|h\d|li|tr|td)\s*>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    html = (
        html.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = html.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("​", "").replace("﻿", "")
    for space in UNICODE_SPACES:
        text = text.replace(space, " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_body(body: str) -> str:
    lines = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            lines.append("")
            continue
        if NOISE_LINE_RE.match(line):
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_articles(text: str) -> list[dict]:
    matches = list(ARTICLE_LINE_RE.finditer(text))
    candidates: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        article = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = clean_body(text[match.end() : end])
        if not body:
            continue
        candidates.setdefault(article, []).append(body)
    result = []
    for article, bodies in candidates.items():
        body = max(bodies, key=len).strip()
        result.append({"article": article, "text": body, "variants": len(bodies)})
    return result


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: parse_legal_raw.py <html> <out.json>")
        raise SystemExit(2)
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    text = clean_html(decode(source.read_bytes()))
    articles = extract_articles(text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"source={source.name} articles={len(articles)} target={target}")


if __name__ == "__main__":
    main()
