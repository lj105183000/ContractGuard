# 真实合同评测（作者标注，非盲测）

本目录只保存**匿名清单、评测脚本说明和可公开的汇总结果**。真实 PDF、完整标签、解析文本和原始 findings 放在 `contractguard-private/real_eval/`，不进入公共仓库。

## 定位

- 数据：当前 3 份真实合同；R1 是唯一 in-scope 货物采购，R3 相邻（IT 采购，扫描件），R2 域外（施工合同）。目标是扩到 10–30 份，新合同只需加入 manifest 与 labels 目录，评测脚本无需改动。
- 标注：作者单人、非法律专业人士、非独立标注、非盲测；标注时未查看系统输出；分歧由作者裁决。
- 这**不是 benchmark**，不能用来声称真实泛化、法务准确率、inter-annotator agreement 或统计显著性。

## 目录

- `contracts.yaml`：匿名 ID、页数、是否有文字层、scope。
- `labels/`：真实标签（本仓库不提交，放在 private 目录）。
- `.work/`：`annotate_real.py dump` 生成的解析文本/条款快照（gitignore）。
- `run_real_eval.py`：复用 `eval/run_eval.py` 的评分函数，输出 `results_real_summary.json` 和 `report_real.md`。
- `scripts/annotate_real.py`：`dump` 导出条款清单，`validate` 校验 quote/clause_id/枚举/重复 ID。

## 运行示例

```bash
cd backend

# 1) 导出条款清单（输出到 private 目录）
uv run python scripts/annotate_real.py dump \
  --pdf "../../contractguard-private/docs/R1.pdf" \
  --out-dir "../contractguard-private/real_eval/work/real_01" \
  --id real_01

# 2) 校验人工标签
uv run python scripts/annotate_real.py validate \
  --labels "../contractguard-private/real_eval/labels/real_01.json" \
  --parsed "../contractguard-private/real_eval/work/real_01/parsed.json"

# 3) rules 模式评测（无需 Key）
uv run python eval/run_real_eval.py \
  --contracts-dir "../../contractguard-private/docs" \
  --manifest "../../contractguard-private/real_eval/manifest.yaml" \
  --labels-dir "../../contractguard-private/real_eval/labels"

# 4) 可选：只对 R1 跑 full live（会产生模型调用）
uv run python eval/run_real_eval.py ... --mode full --limit 1
```

## 当前结果（仅 rules 模式）

- R1：TP/FP/FN = 2/3/2，P 40.0% / R 50.0% / F1 44.4%；证据定位率 100%，法条 ID 可解析率 100%。
- R1 只有 1 份 in-scope 合同，按合同 bootstrap 的 95% CI 退化为点估计（40.0%–40.0% / 50.0%–50.0%）。
- R2/R3：无作者标签，只报告 findings 数/OCR 统计，不与 R1 混合。
- 已知问题：真实 PDF 的 article 切分有金额/编号噪声；R1 的 IP 规则 finding 证据挂到了质保条款，报告如实计入 FP/FN。
- **目前没有第二位独立标注者**，因此结果中的 `inter_annotator_agreement` 为 `null`，不能声称 IAA。

## 双人标注与 IAA（待执行）

1. 第二位标注者只拿到合同原文和标注 schema，不看系统输出、不看作者标签。
2. 按同一 `clause_id`/`quote` 锚点独立标注，保存为 `real_01.annotator_b.json`；作者标签可另存为 `real_01.annotator_a.json`。
3. 运行：

```bash
cd backend
uv run python eval/run_iaa.py \
  --labels-a ../../contractguard-private/real_eval/labels/real_01.annotator_a.json \
  --labels-b ../../contractguard-private/real_eval/labels/real_01.annotator_b.json \
  --out ../../contractguard-private/real_eval/report_iaa.md
```

工具按 `(clause_id 或 clause_article, category, rule_id)` 的二值存在性计算 Cohen's kappa，并列出双方独有的标签；分歧由第三人/作者裁决后再更新评测标签。只有完成这一步，报告才能写 IAA 指标。

## 隐私

- 原始 PDF 只放在 `contractguard-private/docs`；标签/解析文本/原始 findings 放 `contractguard-private/real_eval`。
- 公共报告只含 R1/R2/R3 匿名 ID、页数、OCR 标记、计数和指标；不含合同全文、公司名、联系人、金额明细。
- 运行真实合同前确认 live 模型/embedding/rerank 的数据外发风险；优先 rules-only/本地 OCR。
