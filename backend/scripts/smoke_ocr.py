"""手动验证扫描件 OCR。

用法（backend 目录）：
    uv run python scripts/smoke_ocr.py "C:\\path\\to\\your-scanned-contract.pdf"

真实合同不随仓库分发，请使用自备的扫描件或图片。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parsing.contract_parser import parse_contract  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="ContractGuard OCR smoke test")
    parser.add_argument("path", help="扫描 PDF 或图片路径")
    args = parser.parse_args()

    path = Path(args.path)
    started = time.perf_counter()
    parsed = parse_contract(path)
    elapsed = time.perf_counter() - started

    confidence = (
        f"{parsed.ocr_confidence:.1%}" if parsed.ocr_confidence is not None else "n/a"
    )
    print(
        f"source={parsed.source_type} pages={parsed.page_count} "
        f"used_ocr={parsed.used_ocr} ocr_pages={parsed.ocr_pages} "
        f"failed={parsed.ocr_failed_pages} confidence={confidence} "
        f"chars={len(parsed.text)} elapsed={elapsed:.2f}s"
    )
    print("--- preview ---")
    print("\n".join(parsed.text.splitlines()[:30]))


if __name__ == "__main__":
    main()
