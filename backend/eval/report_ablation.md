# ContractGuard 消融对比

- 生成时间：2026-11-02T00:55:35+08:00

## 风险识别

| 变体 | P | R | F1 | TP/FP/FN | 高风险 FP | 无证据率 | 无法条引用率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 规则 only | 100.0% | 94.1% | 97.0% | 48/0/3 | 0 | 0.0% | 0.0% |
| 规则 + Agent + RAG | 56.8% | 98.0% | 71.9% | 50/38/1 | 16 | 0.0% | 2.3% |
| 规则 + Agent（无 RAG 上下文） | 56.2% | 98.0% | 71.4% | 50/39/1 | 18 | 2.2% | 43.8% |

## Delta

- full - rules：`{'precision': -0.4318, 'recall': 0.0392, 'f1': -0.2503, 'high_risk_fp': 16, 'unsupported_rate': 0.0, 'uncited_rate': 0.0227}`
- no-RAG - full：`{'precision': -0.0064, 'recall': 0.0, 'f1': -0.0051, 'high_risk_fp': 2, 'unsupported_rate': 0.0225, 'uncited_rate': 0.4155}`

## 检索

| 配置 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|---:|
| bm25 | 80.0% | 50.0% | 0.505 | 0.419 |
| hybrid | 88.0% | 66.0% | 0.645 | 0.556 |
| bm25_rerank | 84.0% | 58.0% | 0.664 | 0.532 |
| hybrid_rerank | 96.0% | 83.3% | 0.698 | 0.663 |

## 说明

- rules_only 不需要 LLM；full_rag 使用当前 graph 的 LLM + RAG。
- full_no_rag 需要先运行 --disable-rag --output-suffix full_no_rag；它仍调用 LLM，但不提供检索到的法条/Playbook。
- rerank 对比来自 run_retrieval_eval 的各配置；需要配置 Key 才有 rerank 行。
- HITL 不改变 findings，因此不作为 P/R 消融项；它只影响复核/导出流程。
