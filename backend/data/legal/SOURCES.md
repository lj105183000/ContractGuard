# 法条语料来源与口径

本目录只收录与「中文货物采购合同初审」直接相关的**法律条文节选**，不是完整法律数据库。

## 来源

| 语料 | 来源页面 | 版本/生效日期 | 抓取日期 |
|---|---|---|---|
| 《中华人民共和国民法典》合同编（31 条） | 通辽市信访局政府信息公开页（转载新华社全文）：<https://xfj.tongliao.gov.cn/zwgk/zfxxgk/fdzdgknr/flfg/202211/t20221125_1008083.html>；买卖合同章另见山丹县人民政府页：<https://www.shandan.gov.cn/zfxxgk/xzzfxgk/qqz_7336/xzswgk1/ggflfw_7347/fzxcjy_7349/202204/t20220425_835318.html> | 2020-05-28 通过，2021-01-01 施行 | 2026-10-26 |
| 《中华人民共和国民事诉讼法》第三十五条（协议管辖） | 商务部政策法规库（2023 修正全文）：<https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=98636> | 2023-09-01 修正，2024-01-01 施行 | 2026-10-26 |

> 权威版本应以国家法律法规数据库（<https://flk.npc.gov.cn/>）为准。本项目为演示用途，只做节选；如用于真实业务，请由法务人员核对条文版本与适用性。

## 生成方式

1. `scripts/parse_legal_raw.py` 从抓取的政府网页中，按「行首 + 条文号 + 空格」切分条文，避免把正文中的「依据本法第五百一十条」误切成新条。
2. `scripts/build_legal_yaml.py` 按白名单挑选 31 条民法典条文 + 民诉法第三十五条，补充 `id / title / tags / source_url`，生成 `civil_code.yaml`、`civil_procedure.yaml`。
3. `scripts/validate_kb.py` 会检查 ID 唯一、正文非空、规则引用的 `legal_basis` 均存在。

## 重建方式

仓库只提交最终 YAML，不提交网页原文和中间 JSON。若要从来源页面重建：

1. 将来源页面保存为 HTML，放到 `backend/data/legal/_raw/`；
2. 运行 `python -I scripts/parse_legal_raw.py data/legal/_raw/<file>.html data/legal/_parsed/<file>.json`；
3. 运行 `uv run python scripts/build_legal_yaml.py`。

## 已知局限

- 只收 31 条，不覆盖合同编全部条文，也不包含司法解释、地方性规定、行业规范。
- 网页排版存在少量段落合并；已人工抽查关键条文，但不保证与官方版本逐字完全一致。
- 法条正文由后端从本目录渲染，LLM 只能引用 `id`，不能生成法条原文；这是防编造法条的结构性措施。
