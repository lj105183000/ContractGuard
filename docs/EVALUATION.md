# 评测

> 完整的规则 / live / 检索 / 扰动 / 成本 / 消融评测与口径说明。返回 [README](../README.md)。

规则基线（不需要 API Key，可复现）：

| 指标 | 值 |
|---|---|
| Precision | **100.0%** |
| Recall | **94.1%** |
| F1 | **97.0%** |
| TP / FP / FN | 48 / 0 / 3 |
| 95% CI（按合同 bootstrap） | P 100.0–100.0%；R 86.1–98.4%；F1 92.5–99.2% |
| 证据定位率（43 条） | 100.0% |
| 法条 ID 可解析率（111 个引用） | 100.0% |
| 平均耗时 | 0.03s / 份（仅规则） |

生成命令：

```bash
cd backend
uv run python eval/run_eval.py --mode rules
```

结果在 `backend/eval/results_rules.json` 与 `backend/eval/report_rules.md`。Phase 0A 起结果包含 `provenance`：git commit/dirty、prompt 版本与哈希、KB 版本、dataset/label 哈希、脱敏命令；`--from-results` 只重算 CI，不重新盖版本章，旧结果标 `legacy_missing_provenance`。真实合同支持 `dev|holdout`，默认 `all`，holdout 必须显式 `--allow-holdout`；当前 3 份真实合同全为 dev，尚不足以做 holdout 结论。详见 [`docs/DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md)。

全量 LLM 结果（live，2026-11-02 fresh confirmation：`deepseek-first` 回退链，实际调用 qwen-plus）：

| 指标 | 值 |
|---|---|
| Precision | **64.9%** |
| Recall | **98.0%** |
| F1 | **78.1%** |
| TP / FP / FN | 50 / 27 / 1 |
| 95% CI（按合同 bootstrap） | P 51.4–75.0%；R 93.6–100.0%；F1 67.3–85.7% |
| 高风险 FP | 9 |
| 证据定位率（71 条） | 100.0% |
| 法条 ID 可解析率（181 个引用） | 100.0% |
| 平均耗时 | 77.4s / 份 |

质量代理：已核实层 P **100%**，模型建议层 P 7.1%；高风险 FP 9；模型来源无定位证据 6 / 77（7.8%），其中 5 条是合同级规则+模型合并项（按设计无需条款证据），纯模型无证据 1 条（1.3%）；无法条引用 1 / 77（1.3%）。法条相关性、建议可用性和人工幻觉率需要 `eval/run_quality_eval.py` 的独立评分，当前**尚未评分**。

27 个误报的归因：

- 按来源：27 个 `llm`，**没有纯规则或 rule+llm 误报**；
- 按等级：高 9 / 中 12 / 低 6；
- 按类别：合规 4、解除 4、质保 5、其他 5、验收 3、知识产权 2、违约 2、交付 1、付款 1；
- 按 Agent：合规 12、商务 9、违约解除 6；
- 22 / 27 带 `rule_hint`，但这只是模型自报提示，**不等于规则引擎实际命中**；这也是置信分层只信规则实际命中的原因。

按置信分层：

| 分层 | TP | FP | Precision |
|---|---|---|---|
| 已核实（规则命中/规则+模型合并） | 48 | 0 | **100%** |
| 模型建议（有证据+法条，无规则佐证） | 2 | 26 | 7.1% |
| 待核实 | 0 | 1 | 0.0% |

> 这不是「真实泛化能力」：8 份合同、51 个标注点，其中 48 个是规则可检出点，规则基线接近满分是构造使然。full 模式相比 rules 只多命中 2 个语义点，却多出 27 个误报；本轮通过确定性事实覆盖、合同级缺失去重和矛盾建议过滤，把误报从 38 降到 27，已核实层 P 提升到 100%，但模型建议层仍只有 7.1%。因此系统把模型单独提出的高风险标为「模型建议」，不混进「已核实」层，也没有把它们从评测总数里删掉来美化 Precision。
>
> 法条 ID 可解析率只衡量引用 ID 是否存在于知识库（分母包含被移除的无效 ID），不等于法条相关性准确率；证据定位率的分母只包含提供了原文引用的风险，也不衡量分析质量。
>
> 本次 confirmation run 使用 `deepseek-first` 回退链（实际调用 qwen-plus），48 次调用中 2 次失败、0 次 429，以上为实际落盘结果，未手工修改。默认 `balanced` 路由在此前复跑中仍出现 turbo 限流，部署时建议优先使用 qwen-plus 或配置独立配额。
>
> 95% CI 是按合同 bootstrap（2000 次、seed=42）的描述性区间；8 份合同样本很小，区间很宽，不能解释为统计显著性。

## 精度修复诊断（2026-11-02）

针对上述 38 个误报，追加了三项确定性修复：① 启发式明确看到保护性事实时覆盖 LLM 的否定误抽；② 把模型在任意条款上提出的“缺失类”建议并入同主题的合同级规则锚点，同时保留合同级作用域；③ 过滤与合同明确事实矛盾的 LLM-only 建议。

先用 07:16 的 live 落盘结果做离线回放，再跑 fresh live 确认：

| 指标 | 修复前基线 | 离线回放 | fresh 确认（当前基线） |
|---|---:|---:|---:|
| TP / FP / FN | 50 / 38 / 1 | 50 / 29 / 1 | **50 / 27 / 1** |
| Precision | 56.8% | 63.3% | **64.9%** |
| Recall | 98.0% | 98.0% | **98.0%** |
| F1 | 71.9% | 76.9% | **78.1%** |
| 已核实层 P | 96.0% | 100% | **100%** |
| 高风险 FP | 16 | 12 | **9** |

> 07:16 诊断运行暴露了合同级合并作用域回归；修复后离线回放为 50/29/1，随后 07:35 的 fresh live 确认通过，并已替换 `results_full.json` / `report_full.md`。当前正式基线为 50/27/1。修复细节、恢复的 5 个合同级标签和剩余 27 个 advisory 误报见 [`backend/eval/report_precision_diag.md`](../backend/eval/report_precision_diag.md)。

## 检索评测

25 条作者构造的 query → 相关法条场景，语料 32 条法条，比较 BM25 / hybrid / rerank：

| 配置 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|---:|
| BM25 | 80.0% | 50.0% | 0.505 | 0.419 |
| hybrid | 88.0% | 66.0% | 0.645 | 0.556 |
| BM25 + rerank | 84.0% | 58.0% | 0.664 | 0.532 |
| hybrid + rerank | 96.0% | 83.3% | 0.698 | 0.663 |

| 配置 | Recall@10 95% CI | nDCG@10 95% CI |
|---|---|---|
| BM25 | 37.3–62.0% | 30.1–53.7% |
| hybrid | 53.3–78.7% | 43.2–68.0% |
| BM25 + rerank | 45.3–71.3% | 41.1–65.2% |
| hybrid + rerank | 72.0–93.3% | 55.6–76.8% |

报告：[`backend/eval/report_retrieval.md`](../backend/eval/report_retrieval.md)。query 与相关法条由作者构造，语料只有 32 条；CI 是按 query bootstrap 的描述性区间，不能外推为完整法律检索能力。

## 真实合同外部效度

另外用 3 份真实采购相关 PDF 做作者单人标注检查（非盲测、非独立法务标注）：

- R1（政府采购货物，唯一 in-scope）：rules 模式 TP/FP/FN = 2/3/2，Precision 40.0%、Recall 50.0%；只有 1 份 in-scope 合同，bootstrap CI 退化为点估计。
- R2（36 页施工合同，域外）与 R3（8 页 IT 采购，相邻域）只做定性观察，不与 R1 混合计算。
- 目前没有第二位独立标注者，`inter_annotator_agreement` 为 `null`；`eval/run_iaa.py`、`scripts/annotate_real.py universe`、`eval/run_quality_eval.py` 和双人标注流程已就绪，但尚未有第二份标注数据或人工质量评分。
- 完整标注 schema、固定 universe、裁决、holdout 和质量评分流程见 [`docs/ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md)。

报告：[`docs/REAL_CONTRACT_EVAL.md`](REAL_CONTRACT_EVAL.md)。这也不是 benchmark，样本量和标注独立性都不足。

## 坏例回归与扰动稳定性

[`backend/eval/badcases.yaml`](../backend/eval/badcases.yaml) + `tests/test_badcases.py` 固定了 rules 基线的已知坏例：48 TP / 0 FP / 3 已知语义 FN，以及“规则 findings 必须是已核实层”的约束。live 结果的已知误报标题也在 YAML 中披露。

文本级扰动（改名、金额格式、轻 OCR 噪声、条款倒序）结果：

| 版本 | TP | FP | FN | P | R | F1 |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 48 | 0 | 3 | 100.0% | 94.1% | 97.0% |
| party_names | 48 | 0 | 3 | 100.0% | 94.1% | 97.0% |
| amount_formats | 48 | 0 | 3 | 100.0% | 94.1% | 97.0% |
| ocr_noise | 45 | 1 | 6 | 97.8% | 88.2% | 92.8% |
| reordered_clauses | 48 | 0 | 3 | 100.0% | 94.1% | 97.0% |

报告：[`backend/eval/report_perturbation.md`](../backend/eval/report_perturbation.md)。这是规则层的小样本回归信号，不是真实 OCR/排版鲁棒性证明。

## 成本与延迟

从已落盘的 live full 结果统计（8 份合同）：

| 指标 | 值 |
|---|---|
| LLM 调用 | 48 次（失败 2，其中 429 = 0；失败原因为 2 次 Pydantic 校验失败） |
| Token | 输入 110,971 / 输出 37,532 |
| 延迟 | mean 77.36s；p50 77.34s；p95 92.6s；max 94.92s |
| 估算费用 | ¥0.1638（8 份，约 ¥0.0205/份） |

费用按**假设单价** ¥0.0008/1K input、¥0.002/1K output 计算，不是百炼账单，不含 embedding/rerank/重试；正式引用前用 `eval/run_cost_report.py --input-price-per-1k ... --output-price-per-1k ...` 按当期价格重算。报告：[`backend/eval/report_cost.md`](../backend/eval/report_cost.md)。

## 评测门禁与短板

- **离线 CI 门禁**：rules P/R/F1 阈值、BM25 检索 Recall/nDCG 阈值、扰动最大 F1 下降、成本/延迟/失败调用阈值；指标下降会让 CI 失败。命令见 `docs/ANNOTATION_GUIDE.md` 第 7 节。后端测试覆盖率门槛 80%，`pip-audit`（OSV）与 `npm audit --audit-level=high` 也在 CI 中阻断。
- **口径修正**：规则标签只认规则引擎实际 `rule_id`，不再把模型自报的 `rule_hint` 当作规则命中。
- **可报告 IAA**：`eval/run_iaa.py` 支持 `--universe` 固定候选集；不传时只给出 union-scope 乐观 kappa，不能写进正式报告。κ 不是召回率，共同漏标需单独裁决/报告。
- **人工质量评分**：`eval/run_quality_eval.py` 汇总法条相关性、证据支持、建议可用性和人工幻觉率；这些数据必须由法务/采购评审产生。
- **消融**：`eval/run_ablation.py` 汇总 rules-only、full、no-RAG 和检索配置对比；`run_eval.py --disable-rag --output-suffix full_no_rag` 用于隔离 RAG 贡献。HITL 只影响复核/导出流程，不作为 P/R 消融项。本次实测（8 份合成合同）：
  - full vs rules：P 64.9% vs 100%，R 98.0% vs 94.1%，F1 78.1% vs 97.0%，高风险 FP 9 vs 0；LLM 主要提升语义召回，但仍引入 27 个 advisory 误报。
  - no-RAG vs full：P 59.5% vs 64.9%，F1 74.1% vs 78.1%，高风险 FP 12 vs 9，无法条引用率 42.9% vs 1.3%；RAG 在这批合成数据上提升精度与引用质量，不改变 recall。
  - no-RAG 成本 51 次调用 / 失败 4 / ¥0.1402 / p95 137.7s；full 48 次 / 失败 2 / ¥0.1638 / p95 92.6s。
  - 报告：[`backend/eval/report_ablation.md`](../backend/eval/report_ablation.md)。
- **真实数据仍未接入**：当前只有 3 份真实合同、1 份 in-scope、单人标注、无 IAA；20–30 份双人盲标 + holdout 完成前，不能声称真实泛化或法务准确率。完整流程见 [`docs/ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md)。
