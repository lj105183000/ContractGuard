# ContractGuard 代码导读（初学者版）

这份文档假设：你能看懂基础 Python 函数和类，但对 Multi-Agent、RAG、LangGraph、FastAPI、React 还不熟。

目标不是让你背下每一行，而是让你能讲清三件事：

1. 一份合同从上传到报告，经过了哪些步骤；
2. 每一步为什么存在，不用它会怎样；
3. 哪些地方是刻意简化，生产环境要怎么改。

---

## 一、先看全局：一条请求怎么走

```text
浏览器上传文件
  → FastAPI /api/reviews（backend/app/api/routes.py）
  → JobStore 创建任务（backend/app/services/jobs.py）
  → LangGraph 图（backend/app/graph/build.py）
      parse      解析 PDF/DOCX/TXT/图片（扫描件走 OCR）
      extract    切条款 + 抽事实
      rule_engine + 3 个 Agent   （并行）
      aggregate  合并去重
      redline    生成修改建议
      gate       证据/法条门禁
  → 结果写回 JobStore
  → 前端轮询 /api/reviews/{id}
  → 用户采纳/忽略
  → 导出 MD/JSON/DOCX
```

### 建议阅读顺序（约 30–40 分钟）

1. `backend/app/schemas.py`：先看 `Clause`、`ContractFacts`、`Finding`、`ReviewResult`。这些模型就是系统的「数据语言」。
2. `backend/app/parsing/contract_parser.py` + `clause_splitter.py`：文件怎么变成条款。
3. `backend/app/rules/engine.py`：规则怎么求值，为什么有「未知」。
4. `backend/app/rag/retriever.py`：BM25、向量、RRF 怎么融合。
5. `backend/app/graph/build.py` + `graph/nodes/`：多 Agent 图怎么组装。
6. `backend/app/api/routes.py` + `services/jobs.py`：Web 层怎么接。
7. `backend/app/reporting/report.py` + `revised_contract.py`：报告与合同修订稿怎么生成。
8. `frontend/src/App.jsx` + `FindingDetail.jsx`：前端状态和交互。

如果只有 15 分钟：读第 1、3、5、6 步，再跳到「三、模块地图」。

---

## 二、概念速览

### 1. RAG 是什么

RAG = Retrieval-Augmented Generation，先检索资料，再让模型基于资料回答。

本项目的检索对象有两类：

- **法条**：民法典合同编 31 条 + 民诉法 35 条；
- **企业 Playbook**：25 条采购审查规则。

不用 RAG 会怎样？模型只能凭记忆写法条，容易编造条号或把条文写错。RAG 的目标是「把正确的资料递到模型面前」。

### 2. 为什么是 BM25 + 向量

- **BM25**：按关键词匹配，适合「预付款」「违约金」「协议管辖」这种术语；可解释、稳定。
- **向量检索**：按语义相似度，适合「交货时间不明确」和「交付期限约定模糊」这种换了说法的情况。
- **RRF**：两种排名融合。它不看分数绝对值，只看名次，简单且不容易被某一方的分数尺度带偏。

本项目语料只有 57 条，所以没有用 Chroma/FAISS，而是用 numpy 做精确余弦检索。数据量大了再换向量库。

### 3. Multi-Agent 是什么

不是一个「超级 Prompt」，而是把审查拆成几个有明确职责的角色：

- 商务条款审查员：付款、交付、验收、质保；
- 违约解除审查员：违约金、赔偿、解除、不可抗力；
- 争议合规审查员：管辖、仲裁、保密、知识产权。

每个 Agent 有自己的检索范围、Prompt 和输出 Schema。规则引擎是第四个并行分支，负责确定性检查。

### 4. LangGraph 在做什么

`StateGraph` 把每个节点当成一个函数：

```python
state -> node(state) -> 部分 state 更新
```

并行分支的写法是：`extract` 同时连到 4 个节点，4 个节点再连到 `aggregate`。LangGraph 会等 4 个分支都完成，再执行 `aggregate`。

**重要坑**：LangGraph 的「并行」是同一超步的语义；普通的同步 `def` 节点不会自动获得真正的并发。所以本项目用 `async def` 节点 + `AsyncOpenAI`，让 3 个 LLM HTTP 请求真正重叠。

### 5. 为什么 State 里有 `Annotated[list, operator.add]`

并行分支都会往 `findings` 里写结果。如果直接覆盖，后完成的会覆盖先完成的。用 `operator.add` 表示「把新列表拼到旧列表后面」，这就是 LangGraph 的 reducer。

### 6. 规则引擎为什么有「未知」

规则条件的结果有三种：

- `True`：命中；
- `False`：不命中；
- `None`：字段没抽取到，不知道。

例如「预付款比例 ≥ 30%」这条规则，如果合同里根本没提取到 `prepayment_ratio`，返回 `None`，规则跳过。把 `None` 当成 `False` 会漏报；把 `None` 当成 `True` 会误报。三种状态更接近真实情况。

### 7. 引用门禁是什么

普通 RAG 的常见问题是模型自由写法条。本项目反过来：

- 模型只能输出 `legal_basis_ids`（例如 `CC-585`）；
- 法条正文由后端从 YAML 里取；
- 合同证据 `evidence_quote` 必须能在条款原文里逐字找到；
- 找不到证据的高风险会被标记为「需人工确认」，不能静默进入报告；
- 置信分层只信确定性信号：规则实际命中 → `confirmed`，模型有证据和法条 → `advisory`，否则 → `needs_review` 并转 `needs_human`；
- 只有 `confirmed` 的高风险未复核才阻断导出，`advisory` 和 `needs_review` 在报告中披露但不阻断。

这不是「更聪明的模型」，而是「不信任模型的输出，用结构约束它」。

---

## 三、模块地图

| 路径 | 职责 | 关键函数/类 |
|---|---|---|
| `backend/app/config.py` | 路径、环境变量、运行模式 | `settings`、`ensure_dirs` |
| `backend/app/schemas.py` | Pydantic 数据模型 | `Clause`、`ContractFacts`、`Finding` |
| `backend/app/parsing/contract_parser.py` | PDF/DOCX/TXT/图片 → 文本，扫描件 OCR 回退 | `parse_contract`、`_parse_pdf` |
| `backend/app/parsing/ocr.py` | 本地 RapidOCR 懒加载与推理 | `ocr_image`、`prepare_image` |
| `backend/app/parsing/clause_splitter.py` | 文本 → 条款（保留偏移） | `split_clauses`、`infer_clause_type` |
| `backend/app/parsing/facts.py` | 正则抽事实 | `extract_heuristic_facts`、`merge_facts` |
| `backend/app/rules/engine.py` | YAML 规则 DSL | `evaluate_rules`、`evaluate_condition` |
| `backend/app/rag/corpus.py` | 加载法条/规则/示例 | `load_kb` |
| `backend/app/rag/bm25.py` | BM25 检索 | `BM25Index`、`tokenize` |
| `backend/app/rag/embeddings.py` | OpenAI 兼容 embedding + 缓存 | `EmbeddingStore` |
| `backend/app/rag/retriever.py` | 混合检索 + RRF | `HybridRetriever.retrieve` |
| `backend/app/llm/profiles.py` | Provider profiles、按任务路由、可用性判断 | `ProfileRegistry`、`chat_runtime` |
| `backend/app/llm/client.py` | 按 task 的 fallback 链 + JSON 校验 + trace | `LiveLLM`、`MockLLM` |
| `backend/app/llm/prompts.py` | 抽取/Agent/修改建议 Prompt | `AGENTS`、`build_agent_prompt` |
| `backend/app/graph/state.py` | 图的状态和 reducer | `ReviewState` |
| `backend/app/graph/build.py` | 图组装 | `build_graph` |
| `backend/app/graph/nodes/*` | 各节点实现 | `parse_node`、`aggregate_node`… |
| `backend/app/services/confidence.py` | 确定性置信分层 | `classify_finding`、`apply_confidence_tier` |
| `backend/app/services/summary.py` | 风险统计与指标口径 | `build_summary`、`compute_evidence_metrics` |
| `backend/app/graph/runtime.py` | lifespan 持有的 checkpointer/图 | `init_runtime`、`get_graph`、`hitl_active` |
| `backend/app/graph/nodes/human_review.py` | 图内人工复核 interrupt | `human_review_node` |
| `backend/app/services/jobs.py` | SQLite 快照任务 + 执行图 | `JobStore`、`run_review`、`resume_review`、`list_jobs` |
| `backend/app/worker.py` | SQLite 租约 worker 与过期回收 | `WorkerPool`、`_heartbeat`、`_reaper` |
| `backend/app/crypto.py` | 任务快照/checkpoint AES-GCM 加密 | `encrypt_state`、`AesGcmCipher` |
| `backend/app/rag/rerank.py` | 可选 DashScope reranker | `DashScopeReranker.rerank` |
| `backend/app/api/routes.py` | HTTP 接口 | `/api/reviews`、`/report/preview` |
| `backend/app/reporting/report.py` | MD/JSON/DOCX | `build_markdown`、`build_docx` |
| `backend/app/reporting/revised_contract.py` | 按已采纳建议生成合同修订稿 | `build_revised_contract`、`build_revised_docx` |
| `frontend/src/App.jsx` | 页面状态与轮询 | `phase`、`job`、`activeView` |
| `frontend/src/components/FindingDetail.jsx` | 风险详情抽屉 | 证据高亮、置信理由、复核按钮 |
| `frontend/src/components/ContractViewer.jsx` | 合同全文/条款导航 | 条款定位、证据高亮 |
| `frontend/src/components/ReportPreview.jsx` | 报告草稿预览 | `getReportPreview`、门禁提示 |
| `frontend/src/components/RevisedContractView.jsx` | 修订稿预览与下载 | `getRevisedContract`、变更清单 |
| `frontend/src/components/ServerHistoryPanel.jsx` | Web 任务历史 | `listReviews`、恢复任务 |
| `frontend/src/components/BatchPanel.jsx` | 批量审查队列 | 批次进度、切换结果 |
| `frontend/src/styles.css` | 视觉样式 | 米白/墨蓝/琥珀 |
| `desktop/electron/main.js` | Electron 窗口、托盘、通知、IPC | `createWindow`、`registerIpc` |
| `desktop/electron/backend.js` | 端口选择、spawn 后端、健康检查 | `BackendProcess` |
| `desktop/electron/history.js` | 本地审查快照 | `HistoryStore` |
| `desktop/electron/preload.js` | 渲染进程安全桥 | `window.contractguardDesktop` |

---

## 四、拿一条风险走完全程

以 `sample_01` 的「预付款比例过高」为例：

1. **解析**：`parse_contract` 把 DOCX 转成文本，保留段落；PDF 没有文字层或上传图片时，用 pypdfium2 渲染页面并调用 RapidOCR，OCR 文本会附带置信度警告。
2. **切分**：`split_clauses` 识别「第X条」，生成 `cl_003`（第三条 付款方式），并记录 `start/end` 偏移。
3. **抽事实**：`extract_heuristic_facts` 用正则识别 `prepayment_ratio=0.5`、`payment_days=30`。live 模式下还会用 LLM 抽取，启发式只补空缺。
4. **规则**：`PAY-001` 条件 `payment.prepayment_ratio >= 0.30` 命中，生成 `Finding`：
   - `rule_id=PAY-001`
   - `clause_id=cl_003`
   - `evidence_quote=第三条 付款方式...50%...`
   - `legal_basis_ids=[CC-509, CC-577, CC-626]`
5. **Agent**：mock 模式下 3 个 Agent 返回空；live 模式下它们会补充语义问题。
6. **聚合**：按 `rule_id + clause_id` 等键去重，按严重度/条款顺序排序。
7. **修改建议**：live 模式调用 LLM 生成替换条款；mock 模式用 Playbook 的推荐语兜底。
8. **门禁**：检查证据能否在 `clause_text` 里定位，检查法条 ID 是否存在；高风险未复核不能导出。
9. **报告**：Markdown/DOCX 里的法条正文来自 `data/legal/*.yaml`，不是模型生成的。

---

## 五、怎么加东西

### 加一条规则

1. 在 `backend/data/playbook.yaml` 末尾加一条，字段参考现有规则：
   ```yaml
   - id: PAY-005
     title: 预付款支付时间过早
     description: ...
     category: payment
     severity: medium
     applies_to: [payment]
     condition:
       type: fact
       field: payment.payment_days
       op: "<"
       value: 3
     recommendation: ...
     fallback: ...
     legal_basis: [CC-626]
   ```
2. 确认 `legal_basis` 里的 ID 在 `data/legal/*.yaml` 存在。
3. 运行：
   ```bash
   cd backend
   uv run python scripts/validate_kb.py
   uv run python eval/run_eval.py --mode rules
   ```
4. 如果要进评测，在 `backend/eval/labels/sample_xx.json` 加标签，注意 `rule_id` 和 `clause_article` 要匹配。

> `condition` 支持：`clause_type_absent`、`fact`（`> >= < <= == != is_null not_null contains not_contains`）、`regex`、`all`、`any`、`not`。不要写 Python 代码进 YAML。

### 加一条法条

1. 在 `backend/data/legal/civil_code.yaml` 加一条：`id / law / article / title / text / effective_date / source_url / tags`。
2. 来源必须是官方/政府公开页面，正文逐条核对；不要用模型记忆的法条。
3. 更新 `SOURCES.md`，运行 `validate_kb.py`。
4. 跑评测，观察检索噪音是否变大。

### 加一份示例合同

1. 在 `backend/scripts/generate_demo_data.py` 的 `CONTRACTS` 里加一份（或直接放 DOCX/PDF 到 `data/samples/`）。
2. 在 `data/samples/samples.yaml` 注册。
3. 在 `backend/eval/labels/` 加标签。
4. 运行 `uv run python scripts/generate_demo_data.py` 与 `eval/run_eval.py --mode rules`。

### 任务持久化、批量与 reranker（第二阶段增量）

- **任务持久化**：`JobStore` 把 `JobState` 以 JSON 快照写入 `data/jobs.sqlite3`（`CONTRACTGUARD_DB_PATH` 可改），内存 `_jobs` 只是热缓存。`add_event` 最多每 2 秒落盘一次；`set_result`/`set_failed`/`decide` 强制落盘。启动/定期由租约 reaper 回收：`running` 过期或无租约标记失败；`resuming` 在 `attempt < max_attempts` 时重新入队；`queued` 上传中断超过 1 小时标记失败。
- **图内 HITL**：`main.py` lifespan 创建 `AsyncSqliteSaver`（独立 `checkpoints.sqlite3`）并编译带 `human_review` 节点的图。`gate` 后若有 confirmed high，`interrupt` 暂停，任务 `awaiting_review`；决策写入 JobStore，最后一条已核实高风险处理后 `resume_review` 用 `Command(resume={interrupt_id: {"decisions": ...}})` 恢复；任务短暂 `resuming`，完成后 `completed`。
- **自恢复**：lifespan 扫描 `awaiting_review` 且无 blocking high 的任务自动收尾；也提供 `POST /api/reviews/{job_id}/resume`。暂停任务跨重启可继续决策；`running` 崩溃不自动重放 LLM。
- **刷新恢复**：前端 `localStorage["contractguard.jobId"]` 保存当前任务；挂载时 `getReview` 恢复。Web 端 `ServerHistoryPanel` 调 `GET /api/reviews` 展示历史任务。
- **批量**：`UploadPanel` 支持多选，`App.jsx` 最多 3 份、2 并发调用现有单文件 `startReview`，用 `batch_id` 聚合；`BatchPanel` 轮询每份任务状态。后端有批次视图、批量复核（确认高风险）和批次报告接口，但批次本身仍由前端 fan-out，不是服务端批次实体。
- **reranker**：`app/rag/rerank.py` 调 DashScope text-rerank；`RERANK_ENABLED=1` 才启用，失败回退 RRF。`/api/meta` 暴露 `rerank_mode`/`rerank_model`，不暴露 Key。

### SQLite 租约 worker 与静态加密（第三阶段增量）

- **领取**：`worker.py` 的 `WorkerPool` 在 lifespan 启动。`JobStore.claim` 用一条 `UPDATE ... RETURNING` 原子地把 `queued`（或 `resume_requested=1` 的 `awaiting_review`）改成 `running`/`resuming`，并写入 `lease_token` 与 `lease_expires_at`；多进程下只有一个 claim 能成功。
- **续租与回收**：`WorkerPool._heartbeat` 每 `HEARTBEAT_SECONDS` 续租；`_reaper` 每 10 秒回收过期租约。`resuming` 最多重试 3 次；`running` 失败不重放 LLM。带租约的写入都带 `WHERE lease_token = ?`，失去租约的 worker 无法覆盖新 owner。
- **状态一致性**：`claim`/`mark_ready`/`request_resume` 只改列、不重写大 JSON，所以 `_load` 会用 DB 列覆盖 `state_json` 中的状态字段；`decide` 用 `revision` CAS（`WHERE revision = ? AND status <> 'resuming'`）避免多进程丢决策。
- **静态加密**：`crypto.py` 把主密钥经 HKDF 派生 job-state/checkpoint 两把 key；`jobs.state_json` 用 `enc:v1:` 前缀的 AES-GCM，checkpoint 通过 LangGraph `EncryptedSerializer` + `AesGcmCipher` 接入。旧明文记录仍可读；关闭加密不会解密旧数据。

### 合同修订稿（第三阶段增量）

- `reporting/revised_contract.py` 从 `ReviewResult` 按需生成修订稿，不写回 `JobState`；只应用 `status=accepted` 的 findings。
- 条款替换基于 `full_text[clause.start:clause.end]`，按 start **降序**应用，避免偏移漂移；替换前校验切片与 `clause.text` 一致。替换时保留“第X条 …”标题。
- 模板/ MOCK 建议不替换原文，而是在条款末尾追加 `【修订建议】`；同一条款多条建议全部标注，不静默选一条。
- 合同级或定位失败的 accepted 建议追加到文末“补充条款（AI 建议，需人工确认）”。
- `Finding.suggested_text_source`（`llm`/`template`/`human`）记录建议来源；旧任务默认 `template`，走保守标注；人工编辑后即使 mock 模式也可替换。
- 新接口 `GET /api/reviews/{job_id}/revised-contract?format=json|md|docx`，门禁与报告导出一致；前端 `RevisedContractView.jsx` 展示变更清单和全文并下载 DOCX。

### 任务生命周期与产品化（M1/M2）

- **取消**：`JobStore.request_cancel` 对 queued/awaiting 直接置 `cancelled`；对 running/resuming 只写 `cancel_requested=1`，worker 心跳读到后调用 `set_cancelled` 并取消 asyncio task。`run_review`/`resume_review` 在图返回后也检查该标记，覆盖同步/后台路径。
- **重试**：`JobStore.retry` 只接受 failed/cancelled 且源文件仍存在的任务，重置 result/events/attempt/lease 后重新入队；上传任务失败通常会清理源文件，此时返回 409。
- **配额/防护**：`MAX_ACTIVE_JOBS` 在创建路由前检查；`RATE_LIMIT_PER_MINUTE` 是单进程滑动窗口（`app/api/limits.py`）；上传校验 PDF/DOCX/PNG/JPEG 文件头；解析器限制 PDF 页数与 DOCX 解压后大小。
- **鉴权**：`CONTRACTGUARD_API_TOKEN` 通过中间件保护 `/api/*`（health 除外）；前端 `api.js` 从 localStorage 读 Token，`TokenGate` 负责输入。
- **可观测性**：`X-Request-ID` 响应头 + 请求日志 + `/api/metrics`（`app/observability.py`）；health 返回 DB/checkpoint/worker 状态。
- **人工编辑/对比**：`PATCH /findings/{id}/suggestion` 把 `suggested_text_source` 置为 `human`；`FindingDetail` 可编辑；`RevisedContractView` 的“并排对比”使用 `frontend/src/diff.js` 的行级 LCS。
- **运维脚本**：`scripts/backup_db.py` / `restore_db.py` 用 SQLite backup API；恢复前必须停服务。Docker 以非 root 用户运行，checkpoint 连接设置 `busy_timeout`。

---

## 六、调试手册

### 先跑这些

```bash
cd backend
uv run pytest -q
uv run python scripts/validate_kb.py
uv run python scripts/check_samples.py
uv run python scripts/smoke_api.py
uv run python scripts/test_live_offline.py
uv run python scripts/smoke_ocr.py "C:\path\to\your-scanned-contract.pdf"
```

- `pytest`：177 个离线测试；
- `validate_kb`：法条/规则引用是否一致；
- `check_samples`：每份合同切出多少条款、规则命中/漏报/误报；
- `eval/stats.py`：按合同/query 的 bootstrap CI、nDCG；
- `eval/badcases.yaml` + `tests/test_badcases.py`：已知 FP/FN 与置信分层回归；
- `eval/run_perturbation_eval.py`：改名/金额/OCR 噪声/条款倒序稳定性；
- `eval/run_cost_report.py`：从 full 结果汇总 token、延迟、429 与假设单价费用；
- `eval/run_iaa.py`：两位标注者的 Cohen's kappa（需要第二份标签数据）；
- `smoke_api`：完整 API + 报告导出（mock）；
- `test_live_offline`：启动本地假 OpenAI 服务，验证 live 的 JSON 模式、Pydantic、embedding、图、报告，以及“主 Provider 401 → 备用 Provider 成功”的 fallback，不需要真实 Key/不产生费用；
- `smoke_ocr`：真实扫描件 OCR（8 页约 20 秒），用于验证模型和页面进度。

### 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | 不在 backend 目录运行 | `cd backend`；脚本已自动加 sys.path |
| 用的是 Python 3.14，装包失败 | 依赖兼容性 | 本项目已用 `.python-version=3.12` + `uv sync` |
| 前端 404 / API 连不上 | 后端没启动或端口不对 | 先启动 8000，再启动 5173；Vite 已配置 `/api` 代理 |
| live 模式一直降级 mock | `LLM_MODE` 不是 live，或 `llm_profiles.yaml` 里没有可用路由 | 检查 `backend/.env` 与 `data/llm_profiles.yaml`；`/api/meta` 会返回每个路由的 available/reason |
| 选择的路由不可用 | 首选 Provider 缺少 Key 或本地模型未 enabled | 看 UI 的不可用原因；补齐 Key 或在 YAML 启用本地 Provider 后重启 |
| 模型调用变慢/费用变高 | 主模型失败后按回退链继续尝试 | 看 `usage.traces` 的 profile/model/error；调整路由或 `LLM_DEADLINE_S` |
| 向量检索不可用 | 没 Key、网络问题、embedding 限流 | 看 `/api/meta` 的 `embedding_mode`；可只用 BM25 |
| PDF/图片 OCR 失败 | 扫描件模糊、倾斜、手写，或模型未加载 | 换更清晰的扫描件/图片；看 `OCR_MIN_SCORE`、`OCR_MAX_PAGES`；`uv sync` 安装 OCR 依赖 |
| 扫描件报「超过 OCR 上限」 | 需要 OCR 的页数超过 `OCR_MAX_PAGES`（默认 50） | 拆分文件，或调高 `OCR_MAX_PAGES` 后重启 |
| OCR 结果有错字 | 本地 OCR 只做文字识别 | 结果会带「需人工核对」提示；以原件为准 |
| 中文乱码 | Windows 控制台编码 | 用启动脚本（已设 `PYTHONUTF8=1`），文件读写显式 UTF-8 |
| 报告导出 409 | 还有高风险未复核 | 在 UI 里逐条采纳/忽略，或调用 decision API |
| 改了规则/法条但页面没变 | 后端进程缓存了 KB | 重启 uvicorn（`load_kb` 有 lru_cache） |

### 看一次完整 trace

`backend/app/llm/trace.py` 收集每次 LLM 调用的耗时和 token；接口结果里的 `usage.traces` 可以看到每次调用的名称、profile、模型、耗时、token 和错误，`usage.llm_route`/`llm_models` 汇总实际路由与成功模型。前端的「审查进程」时间线来自 `graph/progress.py` 的事件。

### 多 LLM 路由怎么读

- `data/llm_profiles.yaml`：providers 定义 base_url/model/api_key_env；routes 的 `default` 是回退链，`tasks` 是各任务的优先前缀。实际链 = `tasks[task] + default`，去重后过滤未配置/未启用的 Provider。
- `ProfileRegistry`：计算每个路由的 available/reason 和每个任务的 profile 链；只有默认链里至少一个 Provider 可用，路由才可用。
- `get_llm(trace, task, route_id)`：mock 返回 `MockLLM`；live 按 task 取 profile 链，`LiveLLM` 逐个尝试，每个 profile 最多 2 次，失败写 trace 后换下一个。
- 节点任务名：`extract`、`agent_commercial`、`agent_liability`、`agent_compliance`、`redline`。
- Embedding 不参与聊天路由；`EMBEDDING_API_KEY`/`EMBEDDING_BASE_URL` 独立配置，未配置时 BM25。

### Docker 怎么调试

- `docker-compose up --build` 会先构建前端，再把 `frontend/dist` 和 backend 一起打进镜像。
- 容器内 `CONTRACTGUARD_DATA_DIR=/app/backend/data`、`CONTRACTGUARD_FRONTEND_DIST=/app/frontend/dist`，默认 `LLM_MODE=mock`。
- 只绑 `127.0.0.1:8000`；如果端口被占用，用 `CONTRACTGUARD_PORT=8001 docker-compose up --build`。
- 查看日志：`docker-compose logs -f contractguard`；健康检查：`curl http://127.0.0.1:8000/api/health`。
- 任务 SQLite 和 checkpoint SQLite 挂在 named volume `contractguard-data` 的 `/app/data/` 下；`docker-compose down/up` 不丢暂停/已完成任务，`docker-compose down -v` 会清空。

---

## 七、前端怎么读

- `App.jsx` 是唯一的状态中心：`phase`（idle/uploading/processing/error/done）、`job`、`selectedId`、`filter`、`toast`。
- 上传后调用 `startReview` / `startSample`；随后 `useEffect` 每 900ms 轮询 `getReview`，直到 completed/failed。
- `ResultsView` 组合摘要、风险列表、导出；`FindingDetail` 是右侧抽屉，负责证据高亮和复核。
- 样式全在 `styles.css`：CSS 变量控制米白/墨蓝/琥珀金、风险色、响应式断点。没有 Tailwind/组件库，方便初学者读懂。
- 上传上限来自 `/api/meta` 的 `max_upload_mb`，由后端 `MAX_UPLOAD_MB` 唯一控制；`fileValidation.js` 使用 meta 值做客户端校验，Web 和 Electron 表现一致。
- 扫描页数上限来自 `/api/meta` 的 `ocr_max_pages`（默认 50）；解析节点在 OCR 时发送「扫描件 OCR：x/n 页」进度事件，完成后把 OCR 警告写入 `warnings`，前端和报告直接复用现有展示。
- 模型路由来自 `/api/meta` 的 `llm_routes`/`llm_route`；上传/示例请求通过 `?llm_route=` 传给后端，结果头的「路由/模型」来自 `result.usage.llm_route_label`/`llm_models`。历史快照包含这些字段，旧快照缺失时前端使用可选链。
- 审查失败页由 `frontend/src/errorGuidance.js` 根据真实错误 `{message, source, status}` 映射标题、原因和操作建议；已知错误不再显示固定「常见原因」列表，只有未知错误才显示兜底提示。修改后端错误文案时同步更新该映射，并运行 `node frontend/scripts/check_error_guidance.mjs` 回归。
- 如果要做成生产前端：加 Router、请求库、错误边界、鉴权、SSE，拆分 `App.jsx` 的状态。

### 桌面端增量

- `desktop/electron/backend.js` 先选端口，再 spawn `uv run uvicorn`（开发）或 PyInstaller 产物（打包），轮询 `/api/health` 就绪后返回 URL。
- Electron 窗口加载的是后端同源托管的 `http://127.0.0.1:<port>/`，所以前端 `/api`、报告下载、CORS 都不需要改。
- `desktop/electron/history.js` 把完整 `GET /api/reviews/{id}` 快照写到 `%APPDATA%\ContractGuard\history`；历史记录只读。Web 端则通过后端 SQLite 任务快照恢复，`ServerHistoryPanel` 调 `GET /api/reviews` 列表。
- 前端通过 `frontend/src/desktop.js` 检测 `window.contractguardDesktop`。Web 模式下它是 `null`，历史按钮、整窗拖拽和完成通知都不会启用。

