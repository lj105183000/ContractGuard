"""请求体/解析资源上限的回归测试。"""

from __future__ import annotations

import asyncio
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient

from app.api.body_limit import BodyLimitMiddleware
from app.main import _warmup_embeddings
from app.config import settings
from app.parsing.clause_splitter import ClauseSplitError, split_clauses
from app.parsing.contract_parser import (
    ContractParseError,
    ParsedContract,
    _check_docx_archive,
    _render_pdf_page,
    parse_contract,
)
from app.parsing.ocr import OcrError, prepare_image


@contextmanager
def override_setting(name: str, value):
    old = getattr(settings, name)
    object.__setattr__(settings, name, value)
    try:
        yield
    finally:
        object.__setattr__(settings, name, old)


async def _read_all_app(scope, receive, send):  # type: ignore[no-untyped-def]
    total = 0
    while True:
        message = await receive()
        total += len(message.get("body", b""))
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send(
        {
            "type": "http.response.body",
            "body": str(total).encode("ascii"),
            "more_body": False,
        }
    )


async def _run_body_limit(chunks, headers, limit=1000):
    sent = []
    pending = list(chunks)

    async def receive():
        if pending:
            return pending.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/echo",
        "headers": headers,
    }
    middleware = BodyLimitMiddleware(
        _read_all_app,
        max_multipart_bytes=limit,
        max_json_bytes=limit,
    )
    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_body_limit_rejects_declared_content_length():
    sent = await _run_body_limit(
        [{"type": "http.request", "body": b"x" * 20, "more_body": False}],
        [(b"content-type", b"application/json"), (b"content-length", b"2000")],
    )
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_limit_rejects_chunked_without_content_length():
    chunks = [
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
        {"type": "http.request", "body": b"x" * 600, "more_body": False},
    ]
    sent = await _run_body_limit(
        chunks, [(b"content-type", b"application/json")]
    )
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_limit_allows_small_body():
    sent = await _run_body_limit(
        [{"type": "http.request", "body": b"x" * 200, "more_body": False}],
        [(b"content-type", b"application/json")],
    )
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"200"


def test_multipart_body_limit_before_fastapi_parsing():
    test_app = FastAPI()
    test_app.add_middleware(
        BodyLimitMiddleware, max_multipart_bytes=1000, max_json_bytes=1000
    )

    @test_app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        content = await file.read()
        return {"size": len(content)}

    with TestClient(test_app) as client:
        too_large = client.post(
            "/api/upload", files={"file": ("a.txt", b"x" * 2000)}
        )
        assert too_large.status_code == 413
        small = client.post("/api/upload", files={"file": ("a.txt", b"x" * 10)})
        assert small.status_code == 200
        assert small.json()["size"] == 10


def test_txt_text_limit(tmp_path: Path):
    path = tmp_path / "contract.txt"
    path.write_text("这是一份合同。" * 100, encoding="utf-8")
    with override_setting("max_text_chars", 100):
        with pytest.raises(ContractParseError, match="超过"):
            parse_contract(path)


def test_clause_limit():
    text = "\n".join(f"第{i + 1}条 测试条款内容{i}。" for i in range(11))
    parsed = ParsedContract(text=text, source_type="txt")
    with override_setting("max_clauses", 10):
        with pytest.raises(ClauseSplitError):
            split_clauses(parsed)


def test_image_pixel_limit():
    image = Image.new("RGB", (20, 20), "white")
    with override_setting("max_image_pixels", 100):
        with pytest.raises(OcrError):
            prepare_image(image)


def test_docx_actual_stream_limit(tmp_path: Path):
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"0" * 2_000_000)
    with override_setting("max_docx_uncompressed_mb", 1):
        with pytest.raises(ContractParseError, match="超过"):
            _check_docx_archive(path)


def test_docx_xml_stream_limit(tmp_path: Path):
    path = tmp_path / "big-xml.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"0" * 2_000_000)
    with override_setting("max_docx_uncompressed_mb", 10):
        with override_setting("max_docx_xml_mb", 1):
            with pytest.raises(ContractParseError, match="XML"):
                _check_docx_archive(path)


def test_pdf_render_rejects_huge_page():
    class FakePage:
        def get_size(self):
            return (1_000_000.0, 1_000.0)

        def render(self, scale):  # pragma: no cover - must not be called
            raise AssertionError("huge page must be rejected before render")

    class FakePdf:
        def __getitem__(self, index):
            return FakePage()

    with pytest.raises(ContractParseError, match="尺寸"):
        _render_pdf_page(FakePdf(), 0)


def test_parse_image_pixel_limit(tmp_path: Path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (20, 20), "white").save(path)
    with override_setting("max_image_pixels", 100):
        with pytest.raises(ContractParseError, match="像素"):
            parse_contract(path)


@pytest.mark.asyncio
async def test_embedding_warmup_times_out_without_blocking():
    class SlowRetriever:
        last_warning = None

        async def warmup(self):
            await asyncio.sleep(10)

    retriever = SlowRetriever()
    with override_setting("embedding_warmup_timeout_s", 1):
        started = time.monotonic()
        await _warmup_embeddings(retriever)
        elapsed = time.monotonic() - started
    assert elapsed < 3
    assert retriever.last_warning
    assert "预热" in retriever.last_warning
