# ContractGuard 上线验收记录

- 验收日期：2026-11-02
- 验收范围：本地/单机或受控内网演示；合成/示例合同；人工复核兜底
- 目标环境：Windows 11、Python 3.12（uv）、Node 24 / Vite 7、Docker Desktop
- 说明：本记录只覆盖本轮 P0 修复与演示验收，不代表公网/多用户/真实合同生产能力。

## 已通过

### 后端离线回归

```bash
cd backend
export LLM_MODE=mock
uv run pytest -q
```

结果：`213 passed`（含新增的请求体/解析上限、Host/Token、默认 Worker+HITL E2E、任务超时、embedding 预热超时、评测口径/IAA/质量评分/消融/鲁棒性测试）。后续精度修复、删除 API、双进程 claim 与 Phase 0 数据治理新增 29 个回归测试，当前为 `242 passed`，覆盖率 82%。

```bash
uv run python scripts/validate_kb.py
uv run python scripts/check_samples.py
uv run python scripts/smoke_api.py
uv run python scripts/test_live_offline.py
uv run python eval/run_eval.py --mode rules
uv run python eval/run_perturbation_eval.py
```

结果：

- 知识库校验通过（32 条法条、25 条规则、8 份示例合同）。
- `smoke_api` 使用临时 SQLite/上传目录，完成上传、规则审查、HITL 门禁、MD/DOCX/JSON 报告与修订稿导出，不触碰真实 `data/`。
- `test_live_offline` 使用本地假 OpenAI 服务，验证 live JSON、fallback、embedding、图执行与报告，不需要真实 Key。
- rules 评测：TP/FP/FN = 48/0/3，P 100.0% / R 94.1% / F1 97.0%，证据定位率 100%；与历史报告一致。
- 扰动评测：baseline 与 party_names/amount_formats/reordered_clauses 稳定，ocr_noise F1 92.8%。

### 评测能力补强

- **规则匹配口径**：规则标签只认规则引擎实际 `rule_id`，不再把模型自报 `rule_hint` 当作规则命中；新增回归测试。
- **CI 门禁**：rules P/R/F1、BM25 检索 Recall/nDCG、扰动最大 F1 下降、成本/延迟/失败调用都设置阈值，任一不达标 CI 失败。
- **IAA**：`run_iaa.py` 支持固定 universe；不传 universe 会明确标记为乐观口径；新增 Jaccard/positive agreement、JSON+Markdown 输出和 `--min-kappa`。
- **标注规范**：新增 `docs/ANNOTATION_GUIDE.md`，覆盖双人盲标、裁决、holdout、标签 schema、universe 生成和隐私边界。
- **人工质量**：新增 `run_quality_eval.py`，汇总法条相关性、证据支持、建议可用性、人工幻觉率；示例和测试已加。
- **消融**：新增 `run_ablation.py`；`run_eval.py --disable-rag --output-suffix full_no_rag` 可隔离 RAG 贡献；HITL 明确不作为 P/R 消融项。
- **鲁棒性**：新增提示注入和双 worker 并发测试；长文件/畸形文件/OCR/429 仍有回归测试或手工 smoke。
- **消融实测（fresh 确认后）**：rules-only P 100% / R 94.1% / F1 97.0%（高风险 FP 0）；full P 64.9% / R 98.0% / F1 78.1%（高风险 FP 9）；no-RAG P 59.5% / R 98.0% / F1 74.1%（高风险 FP 12，无法条引用率 42.9% vs full 1.3%）。RAG 在这批合成集上提升精度与引用质量，不改变 recall。报告见 `backend/eval/report_ablation.md`。
- **尚未完成**：仍没有 20–30 份真实合同、第二位独立标注者或人工质量评分数据，因此不能声称真实泛化/法务准确率/IAA。完整流程见 [`docs/ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md)。

### 前端

```bash
cd frontend
npm run build
npm run test:diff
node scripts/check_error_guidance.mjs
```

结果：Vite 构建成功；diff 测试通过；错误引导 29 个用例通过（新增请求超时、请求体上限、条款/DOCX/PDF/图片上限）。

### Docker

```bash
docker-compose build
CONTRACTGUARD_PORT=8010 docker-compose up -d
```

结果：

- 镜像构建成功；`/api/health` 返回 200，worker 运行中、checkpoint 正常。
- 示例 `sample_01` 走默认 Worker+HITL：`awaiting_review` → 处理 4 条已核实高风险 → `completed`，报告导出 200。
- `docker-compose restart` 后任务仍可读取。
- `Host: attacker.example` 返回 400（DNS rebinding 防护生效）。

### 桌面端

```bash
cd desktop
npm run dist
CONTRACTGUARD_SMOKE_EXE=release/win-unpacked/ContractGuard.exe node scripts/smoke.mjs
```

结果：

- 安装包构建成功（未做代码签名，SmartScreen 属预期）。
- 打包版 smoke 通过：窗口标题、本地历史、任务历史、只读快照横幅与导出禁用均正常。
- `resources` 中未发现 `.env`、`jobs.sqlite3`、`checkpoints.sqlite3`。
- 备注：为纳入最后的小改动而进行的第二次 `npm run dist` 在会话环境因内存不足被系统停止，上述安装包来自本轮 P0 主体代码的第一次构建；需要最终产物时可在内存充足时重跑 `npm run dist`，无需改代码。

## Live 评测

- 默认 `balanced` 路由复跑时 `dashscope-turbo` 持续返回 429，采样受阻；已停止该次尝试。
- 初始基线（修复前，已被下文 fresh 确认替换）：改用 `deepseek-first` 回退链（实际调用 qwen-plus）完成 8 份全量复跑（已修正 `rule_hint` 匹配口径）：TP/FP/FN = 50/38/1，P 56.8% / R 98.0% / F1 71.9%；证据定位率 100%（83 条），法条 ID 可解析率 100%（190 个引用）；平均 83.7s/份。
- 初始质量代理：已核实层 P 96.0%，模型建议层 P 5.6%，高风险 FP 16，模型来源无定位证据 0，无法条引用 2/88；法条相关性/建议可用性/人工幻觉率尚未评分。
- 初始成本：51 次 LLM 调用（失败 3，429=0），输入 117,380 / 输出 40,156 tokens，按脚本假设单价估算 ¥0.1742（8 份）。
- 结果已落盘到 `backend/eval/results_full.json`、`report_full.md`、`results_cost.json`、`report_cost.md`。
- 精度诊断（2026-11-02 07:16，deepseek-first/qwen-plus）：原始结果 TP/FP/FN = 45/34/6；定位并修复确定性事实覆盖、合同级缺失去重与矛盾建议过滤后，对同一次落盘结果离线回放为 50/29/1，P 63.3% / R 98.0% / F1 76.9%，已核实层 P 100%，高风险 FP 12。详见 `backend/eval/report_precision_diag.md`。
- fresh 确认（2026-11-02 07:35，同一路由）：TP/FP/FN = **50/27/1，P 64.9% / R 98.0% / F1 78.1%**；已核实层 P 100%，模型建议层 P 7.1%，高风险 FP 9、高风险 FN 0；48 次 LLM 调用、失败 2（均为 Pydantic 校验失败）、429 = 0；输入 110,971 / 输出 37,532 tokens，估算 ¥0.1638。已替换 `results_full.json` / `report_full.md`，修复前基线保留在 git 历史。
- no-RAG 重跑（07:48）：TP/FP/FN = 50/34/1，P 59.5% / R 98.0% / F1 74.1%，高风险 FP 12，无法条引用率 42.9%；51 次调用、失败 4、估算 ¥0.1402。RAG 相对 no-RAG 为 P +5.4pp / F1 +4.0pp，recall 不变。

## 已知未完成（P1）

- 公网/多用户：无 RBAC/对象级授权、TLS、共享限流、租户隔离；当前只有单租户共享 Token。
- 真实合同数据：默认明文存储；上传源文件、桌面历史、feedback/trace 未默认加密。已支持主动删除已结束任务（快照、源文件、feedback、run trace 与 checkpoint）；运行中/待复核任务仍需先取消或完成。
- 数据治理 Phase 0A 已完成：`docs/DATA_ASSETS.md`、`docs/DATA_GOVERNANCE.md`；评测 provenance（git/prompt/KB/dataset/label 哈希与脱敏命令）；feedback v2（合同哈希/模式/路由/模型/prompt/KB 版本）；`scripts/export_feedback.py` 孤儿报告；feedback/trace 随任务过期清理；备份覆盖 feedback/traces/eval；`validate_eval_assets.py` 进 CI。0B（feedback/trace/上传/桌面历史加密、PII 脱敏、删除墓碑）未做。
- 桌面端：未签名、无自动更新；随机端口 + localStorage 导致 Token/偏好跨启动丢失；本地后端信任仅校验 `status:ok`。
- CI：后端 eval 阈值、80% 覆盖率门槛、pip-audit（OSV）、npm audit（high）、评测资产校验（`validate_eval_assets.py`）、Docker 构建 + HTTP smoke、Windows 桌面打包 smoke 已加入 workflow；前端浏览器 E2E（审查 + 删除历史）已加入。
- 评测：框架已补（口径/IAA/质量评分/消融/阈值），但仍只有 8 份合成合同、作者单标注、无真实双人 IAA 和人工质量评分；fresh 确认 full P 64.9%、已核实层 P 100%、模型建议层 P 7.1%，仍不足以支撑自动审查。
- 多进程并发/性能/soak/故障注入未做真实双进程 CI。
