"""pytest 公共配置：强制 mock + 同步任务，避免测试访问网络。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["LLM_MODE"] = "mock"
os.environ["SYNC_JOBS"] = "1"
os.environ["HITL_ENABLED"] = "0"
os.environ["WORKER_ENABLED"] = "0"
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="contractguard-tests-"))
os.environ["CONTRACTGUARD_DB_PATH"] = str(_TEST_DB_DIR / "jobs.sqlite3")
os.environ["CONTRACTGUARD_CHECKPOINT_DB_PATH"] = str(
    _TEST_DB_DIR / "checkpoints.sqlite3"
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.jobs import store  # noqa: E402

SAMPLE_TEXT = """设备采购合同

甲方：星海智能装备有限公司
乙方：远航机电有限公司

第一条 合同标的
甲方向乙方采购数控设备一套，具体规格见附件一。

第二条 合同价款
合同总价为人民币100万元（含税）。

第三条 付款方式
合同生效后支付预付款50%，验收合格后30日内支付尾款。

第四条 交付
乙方应于合同生效后30日内交付至甲方工厂（上海市浦东新区）。

第五条 验收
甲方应在收货后3日内完成验收，逾期未提出书面异议的，视为验收合格。

第六条 违约责任
乙方逾期交付的，按日万分之三支付违约金。

第七条 争议解决
因本合同发生争议，由乙方所在地人民法院管辖。
"""


@pytest.fixture(autouse=True)
def clean_jobs():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def contract_text() -> str:
    return SAMPLE_TEXT


@pytest.fixture
def scanned_pdf(tmp_path):
    """一页没有文字层的 PDF，用于 OCR 回退测试。"""
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(image_path)
    pdf_path = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=A4)
    pdf.drawImage(str(image_path), 0, 0, width=A4[0], height=A4[1])
    pdf.showPage()
    pdf.save()
    return pdf_path


@pytest.fixture
def scan_image(tmp_path):
    from PIL import Image

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(image_path)
    return image_path
