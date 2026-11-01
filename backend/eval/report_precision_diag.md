# full 模式精度诊断与修复（2026-11-02）

- 数据：8 份合成采购合同、51 个标注点（48 个规则点 + 3 个语义点）
- 目的：定位 full 模式 38 个误报，做确定性精度修复，并披露剩余短板
- 结论：修复后离线回放 **TP/FP/FN = 50/29/1，P 63.3% / R 98.0% / F1 76.9%**；随后 07:35 fresh live 确认 **50/27/1，P 64.9% / R 98.0% / F1 78.1%**，已核实层 P 100%，高风险 FP 9、高风险 FN 0
- 状态：fresh 确认已通过并替换默认基线 `results_full.json` / `report_full.md`；本页保留离线回放，用于说明修复是如何被验证的

## 基线、诊断运行、回放与 fresh 确认

| 版本 | TP | FP | FN | P | R | F1 | 已核实层 P | 高风险 FP |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 修复前基线（2026-11-02 00:42） | 50 | 38 | 1 | 56.8% | 98.0% | 71.9% | 96.0% | 16 |
| live 诊断运行（07:16，未含最终 scope 修复） | 45 | 34 | 6 | 57.0% | 88.2% | 69.2% | 89.6% | 17 |
| 修复后离线回放 | 50 | 29 | 1 | 63.3% | 98.0% | 76.9% | 100% | 12 |
| **fresh live 确认（07:35，当前基线）** | **50** | **27** | **1** | **64.9%** | **98.0%** | **78.1%** | **100%** | **9** |

修复后 95% CI（按合同 bootstrap，2000 次，seed=42）：P 50.7–72.7%，R 93.6–100%，F1 66.1–84.2%。样本仅 8 份，区间很宽。

诊断运行本身召回下降，主要因为最终 scope 修复前，合同级规则项被错挂到模型条款上，导致 5 个规则标签匹配失败；其余差异包含 live 运行波动，不能全部归因于代码改动。

## 修复内容

### 1. 确定性事实覆盖 LLM 误抽

`app/parsing/facts.py:merge_facts` 原本是“LLM 非空值一律优先”。当 LLM 把“甲方所在地法院”抽成 `unfavorable_venue=true`、把合同里已有的买方解除权抽成 `buyer_right=false` 时，`DIS-001` / `TER-001` 会误触发并进入 confirmed 层。

现在：

- 启发式明确看到保护性事实（买方解除权、保密条款、质量救济等）时，正向覆盖 LLM；
- 启发式明确看到“无风险”值（管辖对买方有利、解除权非单向）时，负向覆盖 LLM；
- 不反向覆盖：启发式没有证据时仍由 LLM 补空缺，避免把真实风险抹掉。

### 2. 合同级缺失去重

模型常把“保密义务缺失”“争议解决条款缺失”挂到首部或生效条款，而规则引擎在合同级已经命中同一主题。旧逻辑要求同 `clause_id` 才合并，导致同一风险被计两次。

现在 `aggregate.py` 会把“模型提出的缺失类建议”并入同类别、同主题的合同级规则锚点；同时**保留规则锚点的合同级作用域**，不把模型挂的条款复制到规则项上。

这一步曾引入一个回归：合并后规则项被改成“第八条”，导致 5 个合同级规则标签匹配失败。最终修复是 `_merge_into` / `_merge_findings` 在合同级规则锚点上不再采纳 incoming 的 clause/evidence。

### 3. 与合同事实矛盾的模型建议直接过滤

只丢弃 **LLM-only** 建议，规则锚定项永不丢弃：

- 合同明确约定买方所在地法院，仍报“管辖法院对买方不利”；
- 合同明确赋予买方解除权，仍报“买方解除权缺失/不对等/受限”；
- 合同已有保密条款，仍报“保密义务缺失”；
- 合同已约定知识产权归属，仍报“知识产权归属缺失”；
- 交付条款不模糊，仍报“交付期限模糊”。

被过滤的条数会写入结果 `warnings`，便于审计。

## 修复带来的变化

- 恢复 5 个合同级规则标签：`sample_01-L07`、`sample_02-L07`、`sample_03-L07`、`sample_06-L03`（COM-001）、`sample_05-L09`（DIS-003）。
- 在基线 38 个误报中，可确定性消除 9 个：
  - 4 个合同级“保密/争议解决缺失”重复项并入规则项；
  - 4 个“管辖对买方不利”与合同事实矛盾；
  - 1 个“买方解除权不足”与合同事实矛盾；
  - 其中 2 个原本是 confirmed 层规则误报（`sample_02` 的 DIS-001、`sample_08` 的 TER-001）。

## 剩余 27 个误报（fresh 确认）

| 维度 | 分布 |
|---|---|
| 来源 | 全部为 `llm`（advisory / needs_review 层） |
| 等级 | high 9 / medium 12 / low 6 |
| 类别 | warranty 5、general 5、termination 4、compliance 4、acceptance 3、ip 2、liability 2、delivery 1、payment 1 |
| Agent | compliance 12、commercial 9、liability 6 |
| 证据/法条 | 证据定位 26/27，有法条引用 26/27 |

这些主要是“质保期起算点”“合同生效签章细节”“验收不合格处理”“保密范围/期限”“知识产权归属”等**合同起草建议**；当前 51 个标签没有把它们计为正例，因此按本评测口径算误报。它们全部在 advisory / needs_review 层，不阻断导出，也不会被当成已核实风险。

## 局限与下一步

- 8 份合成合同、51 个标签，标签集不是穷尽的；不能据此声称真实泛化或法务准确率。
- 07:35 fresh live 已确认修复并替换基线；离线回放 29 FP 与 fresh 确认 27 FP 的差异属于 live 运行波动。
- no-RAG 已用修复后代码重跑：P 59.5% / R 98.0% / F1 74.1%，34 FP，高风险 FP 12，无法条引用率 42.9%；full 相对 no-RAG 为 P +5.4pp / F1 +4.0pp，recall 不变。`results_ablation.json` / `report_ablation.md` 已更新。
- 剩余 advisory 误报的进一步压缩需要更完整的标签集或人工质量评分，不能只靠继续加关键词过滤。

## 复现

```bash
cd backend
export LLM_MODE=live
# 诊断运行（修复前）
uv run python eval/run_eval.py --mode full --route deepseek-first --output-suffix full_diag
# fresh 确认（已执行并替换基线）
uv run python eval/run_eval.py --mode full --route deepseek-first --output-suffix full_confirm
# no-RAG 消融
uv run python eval/run_eval.py --mode full --route deepseek-first \
  --disable-rag --output-suffix full_no_rag
```

原始诊断运行结果见 `results_full_diag.json` / `report_full_diag.md`；修复后回放见 `results_precision_diag.json`，其中 `corrections` 字段逐条列出被恢复的 5 个标签；fresh 确认产物已替换 `results_full.json` / `report_full.md`。
