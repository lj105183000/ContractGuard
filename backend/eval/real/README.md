# 真实合同评测（作者标注，非盲测）

本目录只保存**匿名清单、评测脚本说明和可公开的汇总结果**。真实 PDF、完整标签、解析文本和原始 findings 放在 `contractguard-private/real_eval/`，不进入公共仓库。

## 定位

- 数据：当前 3 份真实合同；R1 是唯一 in-scope 货物采购，R3 相邻（IT 采购，扫描件），R2 域外（施工合同）。目标是扩到 10–30 份，新合同只需加入 manifest 与 labels 目录，评测脚本无需改动。
- 标注：作者单人、非法律专业人士、非独立标注、非盲测；标注时未查看系统输出；分歧由作者裁决。
- 这**不是 benchmark**，不能用来声称真实泛化、法务准确率、inter-annotator agreement 或统计显著性。

## 目录

- `contracts.yaml`：匿名 ID、页数、是否有文字层、scope、split（当前全为 `dev`，holdout 尚未建立）。
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

# 3) rules 模式评测（无需 Key；默认 all，当前全部 dev）
uv run python eval/run_real_eval.py \
  --contracts-dir "../../contractguard-private/docs" \
  --manifest "../../contractguard-private/real_eval/manifest.yaml" \
  --labels-dir "../../contractguard-private/real_eval/labels" \
  --split dev

# 4) 可选：只对 R1 跑 full live（会产生模型调用）
uv run python eval/run_real_eval.py ... --mode full --limit 1 --split dev
```

- holdout 只能用于最终报告：`--split holdout` 或含 holdout 的 `--split all` 必须显式加 `--allow-holdout`。
- 结果会写 git/prompt/KB/dataset/label 版本、manifest/合同/标签哈希和脱敏命令；公共报告不写私有路径、文件名或逐合同哈希。
- 旧版 list（无 `dataset`/`contracts` 包装）仍可运行，但会标记 `split_inferred=true`，迁移到 v2 manifest 前不能用于 holdout。

## 当前结果（仅 rules 模式）

- R1：TP/FP/FN = 2/3/2，P 40.0% / R 50.0% / F1 44.4%；证据定位率 100%，法条 ID 可解析率 100%。
- R1 只有 1 份 in-scope 合同，按合同 bootstrap 的 95% CI 退化为点估计（40.0%–40.0% / 50.0%–50.0%）。
- R2/R3：无作者标签，只报告 findings 数/OCR 统计，不与 R1 混合。
- 已知问题：真实 PDF 的 article 切分有金额/编号噪声；R1 的 IP 规则 finding 证据挂到了质保条款，报告如实计入 FP/FN。
- **目前没有第二位独立标注者**，因此结果中的 `inter_annotator_agreement` 为 `null`，不能声称 IAA。

## 双人标注与 IAA（待执行）

完整流程、标签 schema、裁决和 holdout 规则见 [`docs/ANNOTATION_GUIDE.md`](../../../docs/ANNOTATION_GUIDE.md)。

1. 第二位标注者只拿到合同原文和标注 schema，不看系统输出、不看作者标签。
2. 按同一 `clause_id`/`quote` 锚点独立标注，保存为 `real_01.annotator_b.json`；作者标签可另存为 `real_01.annotator_a.json`。
3. 生成固定 universe（候选键全集）：

```bash
cd backend
uv run python scripts/annotate_real.py universe \
  --parsed ../../contractguard-private/real_eval/work/real_01/parsed.json \
  --out ../../contractguard-private/real_eval/work/real_01/universe.json
```

4. 运行 IAA（必须带 `--universe`；否则 kappa 只是乐观的 union-scope 口径）：

```bash
uv run python eval/run_iaa.py \
  --labels-a ../../contractguard-private/real_eval/labels/real_01.annotator_a.json \
  --labels-b ../../contractguard-private/real_eval/labels/real_01.annotator_b.json \
  --universe ../../contractguard-private/real_eval/work/real_01/universe.json \
  --out ../../contractguard-private/real_eval/real_01.iaa.json \
  --markdown-out ../../contractguard-private/real_eval/real_01.iaa.md \
  --min-kappa 0.60
```

工具按 `(clause_id 或 clause_article, category, rule_id)` 的二值存在性计算 fixed-universe Cohen's kappa，同时输出 Jaccard、positive agreement 和双方独有标签；分歧由第三人裁决后再生成 final 标签。**IAA 不是召回率**：双方共同漏掉的风险仍可能表现为高一致；因此还需用裁决后的 final 标签计算系统 recall，并单独报告共同漏标案例。

## 人工质量评分

法条相关性、证据支持、建议可用性和幻觉率需要人工逐条评分，不能由风险 P/R/F1 代替。评分格式和阈值见 `docs/ANNOTATION_GUIDE.md` 第 5 节；运行：

```bash
uv run python eval/run_quality_eval.py \
  --scores ../../contractguard-private/real_eval/quality_scores.jsonl \
  --min-legal-relevance 3.5 --min-evidence-support 4.0 \
  --min-suggestion-usability 3.0 --max-hallucination-rate 0.10
```


## 隐私

- 原始 PDF 只放在 `contractguard-private/docs`；标签/解析文本/原始 findings 放 `contractguard-private/real_eval`。
- 公共报告只含 R1/R2/R3 匿名 ID、页数、OCR 标记、计数和指标；不含合同全文、公司名、联系人、金额明细。
- 运行真实合同前确认 live 模型/embedding/rerank 的数据外发风险；优先 rules-only/本地 OCR。
