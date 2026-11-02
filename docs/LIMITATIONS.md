# 局限

> ContractGuard 的完整边界与限制清单。返回 [README](../README.md)。

- **合成数据**：8 份合同、51 个标签，由作者与规则一起构造，存在设计偏差；不代表真实泛化。
- **单一合同类型**：只做中文货物采购合同；服务、劳动、租赁、涉外等不支持。
- **法条节选**：32 条，不含司法解释、地方规定；引用相关性未做大样本盲评。
- **多 LLM 回退成本**：主模型失败会按路由尝试备用模型，耗时可达成倍增加；traces 会记录每次尝试，默认每个 profile 最多 2 次。Embedding 不参与聊天路由，未配置 embedding 服务时只有 BM25。
- **条款抽取误差**：复杂表格、跨页版面、手写/低清晰度扫描件识别质量有限；OCR 文本仍需对照原件核对。
- **上传与解析**：默认上限 50MB（`MAX_UPLOAD_MB`），请求体在 multipart 解析前由 ASGI 中间件限制；另有 `MAX_JSON_BODY_MB`、`MAX_TEXT_CHARS`、`MAX_CLAUSES`、`MAX_IMAGE_PIXELS`、`PDF_MAX_PAGES`、`DOCX_MAX_UNCOMPRESSED_MB`/`DOCX_MAX_ENTRIES`/`DOCX_MAX_XML_MB`、`JOB_DEADLINE_S`。条款超过 120 条时 LLM 抽取只处理前 120 条；事实抽取 prompt 的合同正文上限为 `LLM_EXTRACTION_TEXT_CHARS`（默认 12,000 字符），规则与启发式事实仍覆盖全文。
- **语义审查覆盖上限**：每个 LLM Agent 默认只审查前 `LLM_MAX_CLAUSES_PER_AGENT=10` 条相关条款、最多保留 `LLM_MAX_FINDINGS_PER_AGENT=4` 条候选；超出时结果页会显示运行提示，长合同应拆分审查或调高配置。规则/启发式分支不受此上限影响。
- **OCR 限制**：扫描件默认最多 OCR 50 页（`OCR_MAX_PAGES`），只做文字识别，不做版面还原/表格结构化；平均置信度和识别失败页数会写入运行提示。
- **任务存储与 worker**：SQLite 单表快照 + DB 租约 worker；上传完成后入队，多进程用 CAS 领取，心跳续租。进程崩溃后过期租约会被回收：`resuming` 最多重试 3 次，`running` 不自动重放 LLM，标记为失败。仍是单机 SQLite，不是高吞吐/分布式队列；无登录/多租户/权限，**不要直接暴露公网**。
- **Docker demo**：单容器、mock 模式、只绑定 `127.0.0.1`，默认开启 HITL 与 worker；任务 SQLite、checkpoint SQLite 与上传源文件都在 named volume `contractguard-data`，`down -v` 会清空；默认不启用鉴权，可配置单租户 Token，但没有多用户/权限，公网部署必须自行加保护。
- **HITL 限制**：图内 `interrupt` + `AsyncSqliteSaver`；暂停任务可跨重启恢复决策。完成/失败/过期任务的 checkpoint 会被删除；暂停任务的 checkpoint 按 `JOB_RETENTION_DAYS` 保留。`running/queued` 崩溃后不自动重放 LLM。
- **合同修订稿**：修订稿是基于解析后纯文本重新排版的 AI 草稿，不保留原 DOCX/PDF 的表格、页眉页脚、编号与页面样式；不生成 Word 修订痕迹（tracked changes）。MOCK/模板建议只做原位标注，不直接改写原文；同一条款多条建议全部标注；旧任务保守按模板处理。仍需法务逐条复核后才能作为正式合同使用。
- **静态加密边界**：`ENCRYPTION_ENABLED=1` 时加密 `jobs.state_json`、checkpoint、feedback JSONL 与 run trace；桌面历史在 `safeStorage` 可用时加密。上传源文件、任务列表 filename/summary/error 仍是明文，建议磁盘/卷加密。PII 自动脱敏尚未接入 LLM 输入（仅提供 `scan_pii.py` 检测）；主密钥不支持轮换，丢失后旧数据无法解密。详见 [`docs/DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md)。
- **鉴权/配额边界**：`CONTRACTGUARD_API_TOKEN` 是单租户共享 Token，不是账号/角色/多租户；默认只接受本机 Host（`TrustedHostMiddleware`），`CORS_ORIGIN` 不允许 `*`；内网多机访问必须同时设置 Token、`CONTRACTGUARD_REQUIRE_AUTH=1` 和非本机 `CONTRACTGUARD_ALLOWED_HOSTS`，否则拒绝启动。限流和配额是单进程内存实现，多进程部署需在反向代理层补共享限流。`MAX_ACTIVE_JOBS` 包含待复核任务。
- **取消/重试边界**：取消通过 `cancel_requested` 标记 + worker 心跳生效，最长延迟一个心跳周期；上传任务失败后源文件通常已清理，此时重试返回 409，需要重新上传。同步/旧后台任务路径只在图执行结束后检查取消。
- **人工编辑边界**：编辑的是修改建议文本，不是原合同条款；编辑历史暂未单独版本化。修订稿替换 diff 是行级预览，不是正式 Word 修订痕迹。
- **事后复核**：`completed/completed_with_warnings` 任务仍可继续采纳/忽略（HITL 关闭时这是必要的兜底路径），导出报告和修订稿会反映最新决策；目前没有独立的“重新复核”流程或决策版本 UI。
- **Worker 边界**：解析/OCR 通过 `asyncio.to_thread` 执行，条款切分与规则求值也移到线程池；但大文件仍会占用 CPU/GIL 与内存、拉长任务时间，线程无法被强制杀死，靠输入上限和 `JOB_DEADLINE_S`（默认 900s）兜底。多进程部署或大扫描件场景应调大 `LEASE_SECONDS`。已支持取消/重试：取消 running 任务最多延迟一个心跳周期；`running` 失败后源文件通常已清理，需要重新上传。多进程是设计+进程内测试，尚未做真实双进程 CI。
- **批量限制**：批次仍是前端 fan-out（最多 3 份、2 并发），通过 `batch_id` 聚合；后端支持批量复核已核实高风险和批次报告，但没有服务端批次实体、逐 finding 批量意见或批次修订稿。
- **Reranker 可选**：默认关闭；只有配置 `RERANK_ENABLED=1` 与 DashScope Key 才启用，接口失败会自动回退 RRF，且没有做检索相关性大样本评测。
- **Mock 结果**：没有 Key 时只跑规则，不能把 mock 指标当作 LLM 能力。
- **导出门禁**：只有「已核实高风险」（规则命中或规则+模型合并）阻断导出；模型单独提出的高风险标为「模型建议」，会在报告中披露但不阻断。这不是说模型建议可以忽略，仍应由法务复核。
- **合同全文**：`ReviewResult.full_text` 会随轮询和历史快照传输；桌面端对超过 20MB 的快照会降级为不含全文保存，旧快照只能用条款视图。
- **桌面端本地存储**：历史快照保存在 `%APPDATA%\ContractGuard\history`，包含合同文本；Electron `safeStorage` 可用时加密存储，旧明文快照仍可读；`safeStorage` 不可用时降级明文。文件仍是单机本地数据，需配合操作系统账户权限与磁盘加密。
- **不是法律意见**：所有高风险结论必须由具备法律知识的人员复核。
