# 真实合同外部效度检查（作者标注，非盲测）

## 定位

- **不是 benchmark，不是盲测，不是独立法务标注。**
- 作者单人标注，非法律专业人士；标注时未查看系统输出；对比后由作者裁决分歧。
- 样本只有 3 份真实合同，其中只有 R1 属于货物采购目标场景；R3 是 IT 采购（相邻），R2 是施工合同（域外）。

## 数据

| 合同 | 场景 | 页数 | 文字层/OCR | 说明 |
|---|---|---|---|---|
| R1 | in_scope | 7 | 文字层 | 政府采购框架协议货物采购 |
| R2 | out_of_scope | 36 | 扫描件 OCR | 建设工程施工合同，超出货物采购 Playbook |
| R3 | adjacent | 8 | 扫描件 OCR | IT/智治系统采购，与货物采购相邻 |

原始 PDF 与完整标签放在仓库外 `contractguard-private/`，不随公共仓库分发。公开报告只含匿名 ID、页数、计数和指标。

## 方法与工具

- 标签 schema 扩展了 `clause_id` / `quote` / `scope` / `label_layer` / `annotator` 等字段；真实标签优先按 `clause_id` 或原文短摘录匹配，避免真实 PDF 的 `clause_article` 噪声。
- `backend/scripts/annotate_real.py dump` 导出条款清单；`validate` 校验 quote 逐字存在、clause_id 存在、枚举合法、ID 唯一。
- `backend/eval/run_real_eval.py` 复用合成评测的 `score/aggregate` 逻辑；rules 模式不调用 LLM，full 模式可选。
- 真实运行使用独立 `CONTRACTGUARD_DATA_DIR/DB_PATH/CHECKPOINT_DB_PATH`，避免污染开发数据。

## 当前结果（rules 模式）

| 指标 | R1 |
|---|---:|
| TP / FP / FN | 2 / 3 / 2 |
| Precision | 40.0% |
| Recall | 50.0% |
| F1 | 44.4% |
| 95% CI（按合同 bootstrap） | P 40.0–40.0%；R 50.0–50.0%；F1 44.4–44.4%（n=1，区间退化） |
| 证据定位率（3 条） | 100.0% |
| 法条 ID 可解析率（11 个引用） | 100.0% |

目前没有第二位独立标注者，`inter_annotator_agreement` 为 `null`；IAA 工具与双人标注流程见 [`backend/eval/real/README.md`](../backend/eval/real/README.md)，但尚未用真实第二标注数据运行。

R2/R3 没有作者标签，只报告 findings 数和 OCR 统计，不与 R1 混合计算微平均。

## 解析观察

- 真实 PDF 的条款切分把金额/编号（如 `6459`）识别成 article；真实标签不能依赖 `clause_article` 精确匹配。
- R1 的 IP 规则 finding 证据挂到了质保条款，报告如实计入 FP/FN；这暴露了规则事实与证据绑定的真实问题。
- R2 36 页 OCR、R3 8 页 OCR；OCR 文本有表格/页眉页脚噪声，扫描件结果需对照原件。
- R1 有 59 个 clause，未超过 LLM 抽取上限；R2 有 253 个 clause，full 模式只会抽取前 120 条。

## 不能据此声称

- 真实泛化能力、法务准确率、盲测结果、inter-annotator agreement、统计显著性。
- 与其他系统/律师的可比性；可替代律师；生产级精度。
- 公开报告之外的真实合同文本、公司名、联系人或金额明细。

## 复现

```bash
cd backend
uv run python eval/run_real_eval.py \
  --contracts-dir ../../contractguard-private/docs \
  --manifest ../../contractguard-private/real_eval/manifest.yaml \
  --labels-dir ../../contractguard-private/real_eval/labels
```

详细说明见 [`backend/eval/real/README.md`](../backend/eval/real/README.md)。
