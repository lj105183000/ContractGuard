# ContractGuard 数据治理（Phase 0）

- 状态：Phase 0A（2026-11-02）
- 目标：在**不引入数据中台/数仓/湖仓**的前提下，让合同数据可追溯、评测可复现、反馈可用、真实数据边界明确。
- 资产清单：[`DATA_ASSETS.md`](DATA_ASSETS.md)
- Phase 1/2 的触发条件见文末；未命中触发条件前不建设完整中台。

## 1. 数据分类

| 级别 | 典型数据 | 要求 |
|---|---|---|
| public | 法条、Playbook、合成合同、公开评测汇总 | 可随仓库分发；不得含真实合同/PII |
| internal | 聚合指标、匿名 ID、反馈统计 | 仅项目成员；导出时默认去 note/filename |
| confidential | 任务快照、checkpoint、上传源文件、run trace、桌面历史 | 真实合同即 PII；限制访问、加密或磁盘加密、按保留期删除 |
| restricted | `contractguard-private/` 真实合同、完整标签、raw findings | 不随仓库分发；仅私有环境；加密离线备份 |

## 2. 访问控制

- 当前只有单租户共享 `CONTRACTGUARD_API_TOKEN`，**不是 RBAC/多租户**；默认只绑定 localhost，公网/多机访问必须配置 Token、Host 与反代 TLS。
- 数据目录、备份、导出文件与桌面历史不得放在公开静态目录；备份按 confidential 处理。
- Phase 1 才引入用户/角色/租户字段与对象级授权；SaaS/多租户是完整数据平台的硬门槛之一。

## 3. 加密边界

| 数据 | 当前 | Phase 0B/后续 |
|---|---|---|
| `jobs.state_json` | 可选 AES-GCM（`ENCRYPTION_ENABLED=1`） | — |
| LangGraph checkpoint | 可选 AES-GCM | — |
| 上传源文件 | 明文；审查后默认删除 | Phase 0B 评估加密/磁盘加密指引 |
| feedback JSONL | 明文 | Phase 0B 评估逐行加密 |
| run trace | 明文（仅 `SAVE_TRACES=1`） | Phase 0B 整文件加密 |
| 桌面历史 | 明文 JSON | Phase 0B 评估 Electron `safeStorage` |

- 启动时若 `ENCRYPTION_ENABLED=0`，后端会记录警告：jobs/checkpoint 之外的合同数据为明文。
- 主密钥不支持在线轮换；丢失后旧数据不可解密。备份必须单独保管密钥。
- 真实合同建议：`ENCRYPTION_ENABLED=1` + `CONTRACTGUARD_MASTER_KEY`，并对磁盘/卷做全盘加密；不要只依赖应用层加密。

## 4. 保留与删除

| 数据 | 策略 |
|---|---|
| 任务/checkpoint/上传 | `JOB_RETENTION_DAYS`（默认 7 天）；janitor 清理；`DELETE /api/reviews/{id}` 仅删除已结束任务 |
| feedback/run trace | Phase 0A 起随任务过期删除；显式删除任务时同步清理 |
| 桌面历史 | 30 条/20 MB 单条/200 MB 总量；用户可在本地历史中删除 |
| 真实合同私有数据 | 由私有环境策略管理；不进入公共仓库/CI |
| 备份 | 手工脚本；恢复前停服务；加密库恢复必须提供原主密钥 |

- 删除是硬删除，无法律保留/墓碑/审计只读日志；这些属于 Phase 0B/P1。
- 删除任务不会自动删除桌面本地历史（两套存储独立）。

## 5. 备份与恢复

```bash
cd backend
uv run python scripts/backup_db.py --output-dir backups --include-uploads \
  --include-feedback --include-traces --include-eval
uv run python scripts/restore_db.py --backup-dir backups/<timestamp> --yes \
  --include-uploads --include-feedback --include-traces --include-eval
```

- SQLite 使用在线 backup API；JSONL 是文件复制，建议低活跃期执行。
- `manifest.json` 记录文件列表、feedback SHA-256、加密状态与 `master_key_required`；不记录密钥/API Key。
- 恢复会覆盖 live 数据，必须显式 `--yes` 且停服务。

## 6. 评测与标注数据

- 合成集：8 份合同/51 标签，**regression_only**；不创建 synthetic holdout，不用于声称真实泛化。
- 真实集：3 份合同、仅 1 份 in-scope、作者单人标注；无第二标注者/IAA/质量评分。holdout 机制已支持，但当前全为 `dev`，尚不可用于正式 holdout 报告。
- 每次评测结果记录：git commit/dirty、prompt 版本与文件哈希、KB 版本、dataset/label 哈希、运行命令（路径脱敏）、`--limit` 子集标记。
- `--from-results` 只重算 CI，不重新盖 git/prompt 版本章；旧结果标 `legacy_missing_provenance`。
- 标注规范、双人盲标、裁决、holdout 与质量评分见 [`ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md)。

## 7. PII 与外部模型 egress

- live 模式会把合同文本发送给外部 LLM/embedding/rerank Provider；真实合同必须先确认数据出境/保密要求。
- 当前没有自动 PII 检测/脱敏；`feedback.note` 是自由文本，导出默认不包含 note 与 filename。
- 真实评测的私有路径/文件名不得写入公共结果；`run_real_eval.py` 对命令行路径做脱敏，公共报告只含匿名 ID/计数。

## 8. 反馈闭环

- Phase 0A 起 `feedback.jsonl` 记录 `schema_version=2`：合同 SHA（解析后文本）、模式、路由、模型、prompt/KB 版本、决策范围、finding 元数据；**不记录全文、证据原文、文件名或绝对路径**。
- 导出：

```bash
cd backend
uv run python scripts/export_feedback.py --out-dir ../exports/feedback \
  --formats jsonl,csv,sqlite
```

- 导出报告 `orphans.json` 给出总数、malformed、legacy、可回连与孤儿数量；孤儿记录不得当作可训练金标。
- 旧记录保持可读，导出时标 `legacy=true`。

## 9. 复盘与升级触发

- 每季度复核：数据量、来源数、消费方、租户/合规、标注吞吐、反馈可追溯率、数据事故。
- Phase 1（Postgres/对象存储/轻量分析）触发：20–30 份真实合同 pilot、第二标注者、真实 PII、≥3 个固定消费方、单机 SQLite 出现运维瓶颈。
- Phase 2（完整中台/湖仓）触发：≥2 租户、≥5 稳定数据源、≥5 消费方、审计/等保/数据驻留、>100 GB 或 >10⁵ jobs。
- 未命中触发器时，不引入 Kafka/Spark/湖仓/BI/外部目录；优先补真实数据、标注质量和访问控制。

## 10. 相关文档

- [`DATA_ASSETS.md`](DATA_ASSETS.md)：资产清单
- [`ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md)：标注/IAA/holdout
- [`REAL_CONTRACT_EVAL.md`](REAL_CONTRACT_EVAL.md)：真实合同外部效度
- [`LAUNCH_ACCEPTANCE.md`](LAUNCH_ACCEPTANCE.md)：P1 缺口
- [`../README.md`](../README.md)：部署、加密、备份、限制
