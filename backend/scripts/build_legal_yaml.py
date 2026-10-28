"""把抓取并切分好的法条 JSON 固化为项目使用的 YAML。

用法（在 backend 目录）：
    uv run python scripts/build_legal_yaml.py

源文件是 data/legal/_parsed/*.json，由 parse_legal_raw.py 从政府公开页面生成。
最终产物：data/legal/civil_code.yaml、data/legal/civil_procedure.yaml。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
PARSED_DIR = BACKEND_DIR / "data" / "legal" / "_parsed"
LEGAL_DIR = BACKEND_DIR / "data" / "legal"

CIVIL_CODE_URL = (
    "https://xfj.tongliao.gov.cn/zwgk/zfxxgk/fdzdgknr/flfg/202211/t20221125_1008083.html"
)
SALES_URL = (
    "https://www.shandan.gov.cn/zfxxgk/xzzfxgk/qqz_7336/xzswgk1/ggflfw_7347/"
    "fzxcjy_7349/202204/t20220425_835318.html"
)
CIVIL_PROCEDURE_URL = "https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=98636"


class LiteralStr(str):
    """让 YAML 用 | 块文本输出法条，便于人工审阅。"""


def _literal_representer(dumper, data):  # type: ignore[no-untyped-def]
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(LiteralStr, _literal_representer, Dumper=yaml.SafeDumper)


def chinese_number(text: str) -> int:
    """把「四百七十」这类中文数字转成整数，用于生成稳定 ID。"""
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000}
    total = 0
    section = 0
    number = 0
    for char in text:
        if char in digits:
            number = digits[char]
        elif char in units:
            unit = units[char]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
    return total + section + number


META: dict[str, dict] = {
    "第四百七十条": {
        "title": "合同内容",
        "tags": ["general", "contract_content"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第四百九十六条": {
        "title": "格式条款的提示说明义务",
        "tags": ["compliance", "standard_terms"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第四百九十七条": {
        "title": "格式条款无效",
        "tags": ["compliance", "standard_terms"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百零六条": {
        "title": "免责条款无效",
        "tags": ["liability", "exemption"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百零七条": {
        "title": "争议解决条款的独立性",
        "tags": ["dispute", "validity"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百零九条": {
        "title": "全面履行与诚信履行",
        "tags": ["general", "performance"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百一十条": {
        "title": "约定不明的补充",
        "tags": ["general", "gap_filling"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百一十一条": {
        "title": "约定不明的法定补充",
        "tags": ["general", "gap_filling", "quality", "payment"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百三十三条": {
        "title": "情势变更",
        "tags": ["general", "change_of_circumstances"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百六十三条": {
        "title": "法定解除",
        "tags": ["termination", "breach"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百七十七条": {
        "title": "违约责任",
        "tags": ["liability", "breach"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百八十四条": {
        "title": "损失赔偿范围",
        "tags": ["liability", "damages"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百八十五条": {
        "title": "违约金",
        "tags": ["liability", "liquidated_damages"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百九十条": {
        "title": "不可抗力",
        "tags": ["liability", "force_majeure"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百九十一条": {
        "title": "减损规则",
        "tags": ["liability", "mitigation"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百九十二条": {
        "title": "双方违约与过错相抵",
        "tags": ["liability", "contributory_fault"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百九十三条": {
        "title": "第三人原因违约",
        "tags": ["liability", "third_party"],
        "law": "中华人民共和国民法典",
        "source_url": CIVIL_CODE_URL,
        "effective_date": "2021-01-01",
    },
    "第五百九十五条": {
        "title": "买卖合同定义",
        "tags": ["sales", "definition"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第五百九十六条": {
        "title": "买卖合同内容",
        "tags": ["sales", "contract_content", "quality", "payment", "delivery"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百零三条": {
        "title": "交付地点",
        "tags": ["delivery", "place"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百零四条": {
        "title": "风险负担",
        "tags": ["delivery", "risk"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百一十条": {
        "title": "质量不符的拒收与解除",
        "tags": ["quality", "rejection", "termination"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百一十五条": {
        "title": "质量要求",
        "tags": ["quality", "delivery"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百二十条": {
        "title": "检验期限",
        "tags": ["acceptance", "inspection"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百二十一条": {
        "title": "质量异议通知",
        "tags": ["acceptance", "inspection", "quality"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百二十二条": {
        "title": "检验期限过短",
        "tags": ["acceptance", "inspection"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百二十三条": {
        "title": "签收与推定检验",
        "tags": ["acceptance", "deemed_inspection"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百二十六条": {
        "title": "价款支付",
        "tags": ["payment"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百二十八条": {
        "title": "价款支付时间",
        "tags": ["payment", "timing"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百三十四条": {
        "title": "分期付款与解除",
        "tags": ["payment", "installment", "termination"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
    "第六百四十六条": {
        "title": "其他有偿合同参照适用",
        "tags": ["general", "sales"],
        "law": "中华人民共和国民法典",
        "source_url": SALES_URL,
        "effective_date": "2021-01-01",
    },
}


def load_json(path: Path) -> dict[str, dict]:
    if not path.exists():
        raise SystemExit(f"missing parsed file: {path}")
    records = json.loads(path.read_text(encoding="utf-8"))
    return {record["article"]: record for record in records}


def clean_text(text: str) -> str:
    text = text.replace(" ", " ").replace("　", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_records() -> tuple[list[dict], list[dict]]:
    general = load_json(PARSED_DIR / "general.json")
    sales = load_json(PARSED_DIR / "sales.json")
    procedure = load_json(PARSED_DIR / "civil_procedure.json")
    source_map = {**general, **sales}

    civil_code: list[dict] = []
    missing: list[str] = []
    for article, meta in META.items():
        record = source_map.get(article)
        if not record or not record.get("text", "").strip():
            missing.append(article)
            continue
        number = chinese_number(article.removeprefix("第").removesuffix("条"))
        civil_code.append(
            {
                "id": f"CC-{number}",
                "law": meta["law"],
                "article": article,
                "title": meta["title"],
                "text": LiteralStr(clean_text(record["text"])),
                "effective_date": meta["effective_date"],
                "source_url": meta["source_url"],
                "tags": meta["tags"],
            }
        )
    if missing:
        raise SystemExit(f"missing articles: {missing}")

    cpl_record = procedure.get("第三十五条")
    if not cpl_record or not cpl_record.get("text", "").strip():
        raise SystemExit("missing 民事诉讼法第三十五条")
    civil_procedure = [
        {
            "id": "CPL-35",
            "law": "中华人民共和国民事诉讼法",
            "article": "第三十五条",
            "title": "协议管辖",
            "text": LiteralStr(clean_text(cpl_record["text"])),
            "effective_date": "2024-01-01",
            "source_url": CIVIL_PROCEDURE_URL,
            "tags": ["dispute", "jurisdiction", "agreement_jurisdiction"],
        }
    ]
    return civil_code, civil_procedure


def dump(path: Path, records: list[dict]) -> None:
    payload = yaml.safe_dump(
        records,
        allow_unicode=True,
        sort_keys=False,
        width=120,
        default_flow_style=False,
    )
    path.write_text(payload, encoding="utf-8")
    print(f"wrote {path} ({len(records)} articles)")


def main() -> None:
    civil_code, civil_procedure = build_records()
    LEGAL_DIR.mkdir(parents=True, exist_ok=True)
    dump(LEGAL_DIR / "civil_code.yaml", civil_code)
    dump(LEGAL_DIR / "civil_procedure.yaml", civil_procedure)


if __name__ == "__main__":
    main()
