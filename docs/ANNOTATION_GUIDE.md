# 评测标注与 IAA 指南

本文定义 ContractGuard 的合同风险标注、双人一致性、人工质量评分和 holdout 流程。目标是把“作者自己构造的合成集”升级为可审计的真实合同评测；在完成本文流程前，不要对外声称真实泛化能力、法务准确率或 IAA。

## 1. 角色与数据边界

- **标注者 A / B**：独立标注，互不看对方标签、系统输出、模型 findings 或本指南以外的提示。
- **裁决者**：第三人（优先法务/采购专家），只看两份标签和合同原文，处理分歧。
- **评测维护者**：只负责合并标签、运行评测和报告，不参与标注判断。
- **数据**：原始合同、解析全文、完整标签、IAA 明细放在 `contractguard-private/real_eval/`；公共仓库只放匿名 manifest 和汇总指标。

合成集（`backend/data/samples/`）只用于回归和流程演示，不能与真实集混合计算指标。

## 2. 标签 schema

使用 `backend/app/schemas.py::EvalLabel`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 标注内唯一；A/B 可使用不同 ID |
| `clause_id` | 推荐 | 解析后的稳定条款 ID（`cl_003`） |
| `clause_article` | 可选 | “第三条”等原文标题；无 `clause_id` 时使用 |
| `quote` | 推荐 | 逐字原文片段，≤200 字；用于锚点和证据核验 |
| `category` | 是 | 风险类别，按 `Category` 枚举 |
| `severity` | 是 | high / medium / low |
| `rule_id` | 规则风险必填 | 只有确实是规则可检出风险时才填；语义风险留空 |
| `keywords` | 语义风险推荐 | 用于语义标签匹配的关键词 |
| `scope` | 真实合同推荐 | in_scope / adjacent / out_of_scope |
| `label_layer` | 真实合同推荐 | rule_detectable / semantic / out_of_scope |
| `annotator` | 是 | 标注者代号，不得匿名混同 |
| `annotation_pass` | 是 | 独立标注为 1；裁决/复核为 2 |
| `confidence` | 可选 | 标注者对标签的置信度；不用于模型自评 |
| `notes` | 可选 | 歧义、法条理由、裁决记录 |

锚点规则：优先 `clause_id`；没有时用 `clause_article`；再没有时用 `quote` 在解析文本中定位。`quote` 必须逐字来自解析文本，不能用改写或拼接。

## 3. 标注流程

### 3.1 准备

```bash
cd backend
# 1) 导出条款清单（含全文，放 private）
uv run python scripts/annotate_real.py dump \
  --pdf "../../contractguard-private/docs/real_01.pdf" \
  --out-dir "../../contractguard-private/real_eval/work/real_01" \
  --id real_01

# 2) 生成 IAA 固定 universe（候选键全集）
uv run python scripts/annotate_real.py universe \
  --parsed "../../contractguard-private/real_eval/work/real_01/parsed.json" \
  --out "../../contractguard-private/real_eval/work/real_01/universe.json"
```

universe 是 `(clause_id, category, rule_id)` 的候选全集，作为 kappa 的负例空间；它不是“这些风险都真实存在”的声明。必须用同一个 universe 计算 A/B 的 kappa。

### 3.2 独立标注

- A、B 各自拿到合同原文 + 条款清单 + 本指南，不看系统结果。
- 每条标签写清 `clause_id`、`quote`、`category`、`severity`、`rule_id`（仅规则风险）、`annotator`、`annotation_pass=1`。
- 保存为：
  - `real_01.annotator_a.json`
  - `real_01.annotator_b.json`
- 不要在这一步合并或“商量着标”。

### 3.3 校验

```bash
uv run python scripts/annotate_real.py validate \
  --labels "../../contractguard-private/real_eval/labels/real_01.annotator_a.json" \
  --parsed "../../contractguard-private/real_eval/work/real_01/parsed.json"
```

校验通过后再进入 IAA；重复 ID、无效 clause、quote 不逐字匹配都会失败。

### 3.4 计算 IAA

```bash
uv run python eval/run_iaa.py \
  --labels-a "../../contractguard-private/real_eval/labels/real_01.annotator_a.json" \
  --labels-b "../../contractguard-private/real_eval/labels/real_01.annotator_b.json" \
  --universe "../../contractguard-private/real_eval/work/real_01/universe.json" \
  --out "../../contractguard-private/real_eval/real_01.iaa.json" \
  --markdown-out "../../contractguard-private/real_eval/real_01.iaa.md" \
  --min-kappa 0.60
```

输出包含 fixed-universe Cohen's kappa、Jaccard、positive agreement、双方独有标签。**必须传 `--universe`**；不传时脚本会标记 `union_scope_optimistic`，该 kappa 不能写进报告。

κ 解释（描述性，不是法律结论）：<0.40 差；0.40–0.59 中等；0.60–0.79 较好；≥0.80 好。样本小、类别不平衡时 κ 不稳定，应同时报告原始一致数、分歧案例和 CI/样本量。

**IAA 不是召回率**：两位标注者都漏掉同一风险时仍然“一致”。因此还必须用裁决后的参考标签计算系统 recall，并单独报告“标注者共同漏标”的案例；不要把高 κ 当作高覆盖率证明。

### 3.5 裁决与最终标签

1. 裁决者逐条处理 `disagreements`：接受 A、接受 B、改写或新增。
2. 裁决结果写 `annotation_pass=2`，`annotator=adjudicator`，并在 `notes` 记录理由。
3. 生成 `real_01.final.json` 作为评测标签；A/B 原始标签保留，不得覆盖。
4. 只有在 final 标签上计算 P/R/F1；IAA 用 A/B 原始标签。

## 4. 真实合同采集与 holdout

- 目标规模：**20–30 份 in-scope 中文货物采购合同**；相邻/域外合同单独分层，不混入主指标。
- 每份合同匿名化：去掉公司名、联系人、账号、签章图片；保留金额量级、条款结构、OCR 噪声特征（在私有集内）。
- 固定 `split: dev | holdout`（建议 70/30，按合同分层），写在私有 manifest；**holdout 只用于最终报告，不用于调规则/Prompt/阈值**。
- 至少每份合同双人标注；优先全部双标，最低要求随机 30% 双标 + 其余单人 + 抽查。
- 记录 `dataset_version`、`label_version`、`prompt_version`、`model/profile`、`git_commit`；同一报告必须能复现。

## 5. 人工质量评分（法条相关性/幻觉/建议可用性）

风险 P/R/F1 不衡量“有出处但判断错”。另建评分文件（JSONL），由法务/采购评审对系统 finding 逐条打分：

```json
{"contract_id":"real_01","finding_id":"llm-...","reviewer":"legal_a","legal_relevance":4,"evidence_support":5,"suggestion_usability":3,"hallucinated":false,"notes":"..."}
```

- `legal_relevance`：1 无关 … 5 直接相关
- `evidence_support`：1 无依据 … 5 逐字且充分
- `suggestion_usability`：1 不可用 … 5 可直接使用
- `hallucinated`：人工判断是否编造合同事实/法条

汇总：

```bash
uv run python eval/run_quality_eval.py \
  --scores "../../contractguard-private/real_eval/quality_scores.jsonl" \
  --min-legal-relevance 3.5 \
  --min-evidence-support 4.0 \
  --min-suggestion-usability 3.0 \
  --max-hallucination-rate 0.10
```

输出 `results_quality.json` / `report_quality.md`。没有评分数据时，不得声称这些指标。

## 6. 消融与鲁棒性

在真实 holdout 上按以下顺序做消融（每次固定同一批合同、同一模型/温度）：

```bash
# 1) 规则 only（无 LLM）
uv run python eval/run_eval.py --mode rules

# 2) 规则 + Agent + RAG（live）
uv run python eval/run_eval.py --mode full --route deepseek-first

# 3) 规则 + Agent，但去掉检索上下文
uv run python eval/run_eval.py --mode full --route deepseek-first \
  --disable-rag --output-suffix full_no_rag

# 4) 检索层消融
uv run python eval/run_retrieval_eval.py

# 5) 汇总已有结果
uv run python eval/run_ablation.py
```

HITL 不改变 finding 集合，只改变复核/导出流程，因此不作为 P/R 消融项；其流程覆盖在 `tests/test_e2e_worker_hitl.py`。

鲁棒性至少覆盖：长合同/多条款、复杂表格、扫描件 OCR、提示注入、超大/畸形文件、并发 worker、429/超时/重启。已有 `tests/test_robustness.py`、`test_limits.py` 和 worker/smoke 测试作为回归起点；真实 OCR/表格/并发压测仍需在私有集上补。

## 7. CI 阈值

离线 CI 当前门禁（数值是当前合成集基线，真实集接入后需重设）：

```bash
uv run python eval/run_eval.py --mode rules \
  --min-precision 0.99 --min-recall 0.93 --min-f1 0.96
uv run python eval/run_retrieval_eval.py --no-rerank \
  --threshold-config bm25 --min-recall-at-10 0.45 --min-ndcg-at-10 0.35
uv run python eval/run_perturbation_eval.py \
  --min-baseline-f1 0.95 --max-f1-drop-pp 5
uv run python eval/run_cost_report.py \
  --max-cost-cny 1.0 --max-p95-s 120 \
  --max-failed-calls 10 --max-rate-limited-calls 10
```

live full、真实合同、IAA 和人工质量评分不放在普通 CI（需要 Key/人力/数据），应在发布前手工执行并归档报告。

### 7.1 Provenance 与 holdout 运行

Phase 0A 起评测结果会写入：

- `git.commit/dirty`、prompt 版本与文件哈希、KB 版本、dataset/label 哈希；
- 脱敏后的运行命令（私有路径替换为 `<redacted>`）；
- `--limit` 时标记 `limited=true`，不把子集当成完整数据集。

真实评测：

```bash
cd backend
# 默认 all；dev 只跑 dev split
uv run python eval/run_real_eval.py ... --split dev --output-suffix dev
# holdout 只能用于最终报告，必须显式允许
uv run python eval/run_real_eval.py ... --split holdout --allow-holdout --output-suffix holdout
```

- 当前 3 份真实合同全部为 `dev`，holdout 尚未建立；不要用它们声称 holdout 泛化。
- `--from-results` 只重算 bootstrap CI，不重新盖 git/prompt/KB 版本；旧结果标 `legacy_missing_provenance`。
- 反馈记录 `schema_version=2` 含合同 SHA/模式/路由/模型/prompt/KB 版本，不含全文/文件名；导出与孤儿报告见 `scripts/export_feedback.py`。
- 评测资产校验：`uv run python scripts/validate_eval_assets.py`。

## 8. 报告口径

必须分开报告：

1. **规则回归**：合成集 rules P/R/F1、已知坏例。
2. **真实集风险识别**：final 标签上的 P/R/F1、按严重度/类别/来源/置信层，bootstrap CI（按合同）。
3. **IAA**：fixed-universe κ、一致数、分歧数和裁决记录；同时报告共同漏标案例。
4. **人工质量**：法条相关性、证据支持、建议可用性、幻觉率。
5. **检索**：Hit/Recall/MRR/nDCG@10 + query CI；注明语料规模。
6. **成本/延迟/可靠性**：调用数、失败/429、p50/p95、估算费用。
7. **消融**：rules vs full vs no-RAG vs retrieval/rerank；注明哪些是同一批数据。

只有 1–7 都有数据，才可以把“评测能力”称为上线级证据；只有 1 和合成集，只能称为工程回归。
