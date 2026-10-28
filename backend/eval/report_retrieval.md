# ContractGuard 检索评测报告

- 运行时间：2026-10-28T12:15:34+08:00
- query 数：25；语料：32 条法条
- 口径：作者构造的 25 条 query→relevant law 场景；不是独立法务标注，不能代表大样本泛化。
- 指标：Hit@k / Recall@k / MRR@k / nDCG@k；微平均按 query 等权。

## 总体

| 配置 | 检索模式 | 重排模式 | Hit@5 | Recall@5 | MRR@5 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|---|---|---|---|---|
| bm25 | bm25_only | off | 72.0% | 42.0% | 0.495 | 80.0% | 50.0% | 0.505 | 0.419 |
| hybrid | hybrid | off | 80.0% | 52.7% | 0.633 | 88.0% | 66.0% | 0.645 | 0.556 |
| bm25_rerank | bm25_only | dashscope | 80.0% | 54.0% | 0.660 | 84.0% | 58.0% | 0.664 | 0.532 |
| hybrid_rerank | hybrid | dashscope | 88.0% | 68.7% | 0.687 | 96.0% | 83.3% | 0.698 | 0.663 |

## 95% CI（按 query bootstrap，2000 次，seed=42）

| 配置 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|
| bm25 | 50.0% (37.3%–62.0%) | 50.5% (35.1%–65.9%) | 41.9% (30.1%–53.7%) |
| hybrid | 66.0% (53.3%–78.7%) | 64.5% (48.6%–80.5%) | 55.6% (43.2%–68.0%) |
| bm25_rerank | 58.0% (45.3%–71.3%) | 66.4% (50.7%–81.3%) | 53.2% (41.1%–65.2%) |
| hybrid_rerank | 83.3% (72.0%–93.3%) | 69.8% (55.5%–83.3%) | 66.3% (55.6%–76.8%) |

## 按来源（Recall@10 / MRR@10 / nDCG@10）

| 配置 | 来源 | 数量 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|---|
| bm25 | playbook_rule | 3 | 50.0% | 0.281 | 0.297 |
| bm25 | synthetic_clause | 22 | 50.0% | 0.536 | 0.436 |
| hybrid | playbook_rule | 3 | 72.2% | 0.750 | 0.603 |
| hybrid | synthetic_clause | 22 | 65.1% | 0.631 | 0.549 |
| bm25_rerank | playbook_rule | 3 | 61.1% | 0.481 | 0.472 |
| bm25_rerank | synthetic_clause | 22 | 57.6% | 0.689 | 0.540 |
| hybrid_rerank | playbook_rule | 3 | 66.7% | 0.444 | 0.504 |
| hybrid_rerank | synthetic_clause | 22 | 85.6% | 0.733 | 0.685 |

## 运行提示

- 无

## 局限

- 25 条 query 由作者构造，relevant 集合来自 Playbook/人工判断，存在构造偏差。
- 语料只有 32 条法条，召回空间很小；不能外推到完整法律数据库。
- 95% CI 是按 query bootstrap 的描述性区间；没有独立法务标注、盲测或显著性检验。
- rerank 模型/接口可能随 DashScope 下线而变化；失败时会回退 RRF，报告中的 rerank 行可能等同 BM25/RRF。
