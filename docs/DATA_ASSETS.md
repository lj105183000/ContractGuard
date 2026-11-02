# ContractGuard 数据资产目录

- 适用版本：Phase 0A（2026-11-02）
- 目的：列出项目中的持久化数据资产、敏感级、保留/删除、备份与可重建性。
- 边界：这是**数据资产清单**，不是数据中台/数仓；项目当前没有 DuckDB/Parquet/Kafka/Airflow/dbt/DataHub/MinIO/Postgres/BI 等平台组件。

## 1. 运行时数据

| 资产 | 路径 | 格式/schema | 敏感级 | 保留 | 删除 | 备份 | 可重建 |
|---|---|---|---|---|---|---|---|
| 任务快照 | `backend/data/jobs.sqlite3` | SQLite 单表 `jobs`；`state_json` 为 `JobState`（`backend/app/schemas.py`），含 `ReviewResult.full_text` | confidential（真实合同即 PII） | `JOB_RETENTION_DAYS`（默认 7 天） | `DELETE /api/reviews/{id}`（仅已结束任务）或 janitor | `scripts/backup_db.py` | 否 |
| HITL checkpoint | `backend/data/checkpoints.sqlite3` | LangGraph `checkpoints`/`writes`，含 `ReviewState`/`report` | confidential | 完成/删除/过期时清理 | janitor + `delete_checkpoint` | `backup_db.py` | 否 |
| 上传源文件 | `backend/data/uploads/<job_id>/<filename>` | PDF/DOCX/TXT/JPG/PNG | confidential/PII | 默认审查后删除 | 审查完成、`DELETE`、janitor | `--include-uploads` | 否（原件） |
| 人工反馈 | `backend/data/feedback/feedback.jsonl` | JSONL；Phase 0A 起 `schema_version=2`，含合同 SHA/模式/路由/模型/prompt/KB 版本，不含全文/文件名 | internal，`note` 可能含 PII | 随任务过期删除 | `DELETE` / janitor | `--include-feedback` | 否 |
| 运行 trace | `backend/data/runs/<job_id>.json` | 完整 `ReviewResult`（含 `full_text`），仅 `SAVE_TRACES=1` | confidential | 随任务过期删除 | `DELETE` / janitor | `--include-traces` | 否 |
| 向量缓存 | `backend/data/index_cache/embeddings.npz` + `embeddings_meta.json` | NumPy 向量 + 指纹 | derived | 可随时重建 | 删除缓存 | 不必备份 | 是 |
| 桌面本地历史 | `%APPDATA%\ContractGuard\history` | 每任务一个明文 JSON 快照 | confidential/PII | 30 条/20 MB 单条/200 MB 总量 | 桌面历史删除/清空 | 不自动备份 | 否 |

## 2. 知识库与评测数据

| 资产 | 路径 | 格式 | 敏感级 | 版本/保留 | 备份 | 说明 |
|---|---|---|---|---|---|---|
| 法条 | `backend/data/legal/*.yaml` | YAML（32 条） | public | Git | Git | 来源见 `data/legal/SOURCES.md` |
| Playbook | `backend/data/playbook.yaml` | YAML（25 条规则） | public | Git | Git | 规则 DSL |
| 合成合同 | `backend/data/samples/*` + `samples.yaml` | DOCX/PDF + YAML（8 份） | public（合成） | Git | Git | 明确标注合成 |
| 合成标签 | `backend/eval/labels/*.json` | JSON（51 标签） | public | Git；内容哈希写入 eval provenance | Git | 单作者构造，非独立标注 |
| 评测结果/报告 | `backend/eval/results_*.json`、`report_*.md` | JSON/Markdown | public（聚合计） | Git 快照 | `--include-eval` | live 结果可能缺 provenance，文档标 legacy |
| 真实合同公共清单 | `backend/eval/real/contracts.yaml` | YAML，匿名 ID/scope/split | public（匿名） | Git | Git | 不含文件名/路径/哈希 |
| 真实合同私有数据 | `../contractguard-private/real_eval/` | PDF/标签/raw findings | restricted/PII | 项目自定 | 仅加密离线备份 | 不随仓库分发 |
| 检索评测 | `backend/eval/retrieval/queries.yaml` | YAML（25 query） | public | Git | Git | 作者构造 |

## 3. 重复与单一事实源

合同全文目前至少出现在：

1. `ReviewResult.full_text`（`JobState.result`）
2. `Clauses[].text` 与 `Finding.clause_text/evidence_quote`
3. LangGraph checkpoint
4. 可选 `runs/<job_id>.json`
5. JSON 报告导出
6. 桌面历史快照

Phase 0 只增加哈希/清单（`contract_sha256`、dataset/label/KB/prompt 版本），**不删除 `full_text`**；单一事实源重构属于 Phase 1。删除/保留必须以任务为单位覆盖上述副本。

## 4. 不存在的数据平台组件

仓库依赖与代码中没有：DuckDB、Parquet/PyArrow、Kafka、Airflow/Prefect、dbt、DataHub/OpenMetadata、MinIO/S3、Postgres、ClickHouse、Snowflake/BigQuery、Spark/Flink、Redis/Celery、SQLAlchemy、向量数据库、BI 工具。现有“ETL”仅为手工 CLI 脚本。

## 5. 相关文档

- 治理策略：[`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md)
- 标注/holdout：[`ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md)
- 真实合同评测：[`REAL_CONTRACT_EVAL.md`](REAL_CONTRACT_EVAL.md)
- 上线验收与 P1 缺口：[`LAUNCH_ACCEPTANCE.md`](LAUNCH_ACCEPTANCE.md)
