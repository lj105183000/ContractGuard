"use strict";

const fs = require("node:fs");

// 只接受后端 Settings 认识的键，避免把任意环境变量注入子进程。
const ALLOWED_KEYS = new Set([
  "LLM_MODE",
  "LLM_ROUTE",
  "LLM_TIMEOUT_S",
  "LLM_DEADLINE_S",
  "CONTRACTGUARD_LLM_PROFILES",
  "DASHSCOPE_API_KEY",
  "DASHSCOPE_BASE_URL",
  "LLM_MODEL",
  "EMBEDDING_API_KEY",
  "EMBEDDING_BASE_URL",
  "EMBEDDING_MODEL",
  "EMBEDDING_DIM",
  "RERANK_ENABLED",
  "RERANK_MODEL",
  "RERANK_BASE_URL",
  "RERANK_TOP_N",
  "RERANK_TIMEOUT_S",
  "JOB_RETENTION_DAYS",
  "CONTRACTGUARD_DB_PATH",
  "HITL_ENABLED",
  "CONTRACTGUARD_CHECKPOINT_DB_PATH",
  "HITL_RESUME_ON_STARTUP",
  "WORKER_ENABLED",
  "WORKER_CONCURRENCY",
  "WORKER_POLL_INTERVAL_S",
  "LEASE_SECONDS",
  "HEARTBEAT_SECONDS",
  "ENCRYPTION_ENABLED",
  "CONTRACTGUARD_MASTER_KEY",
  "CONTRACTGUARD_UPLOAD_DIR",
  "CONTRACTGUARD_API_TOKEN",
  "MAX_ACTIVE_JOBS",
  "RATE_LIMIT_PER_MINUTE",
  "PDF_MAX_PAGES",
  "DOCX_MAX_UNCOMPRESSED_MB",
  "DEEPSEEK_API_KEY",
  "OPENAI_API_KEY",
  "MOONSHOT_API_KEY",
  "OLLAMA_API_KEY",
  "MAX_UPLOAD_MB",
  "OCR_MAX_PAGES",
  "OCR_RENDER_SCALE",
  "OCR_MIN_SCORE",
  "OCR_MIN_PAGE_CHARS",
  "OCR_MAX_RENDER_SIDE",
  "SAVE_TRACES",
  "SYNC_JOBS",
]);

function stripQuotes(value) {
  if (value.length >= 2) {
    const first = value[0];
    const last = value[value.length - 1];
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return value.slice(1, -1);
    }
  }
  return value;
}

function readEnvFile(filePath) {
  const result = {};
  try {
    if (!fs.existsSync(filePath)) return result;
    let text = fs.readFileSync(filePath, "utf8");
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const equal = line.indexOf("=");
      if (equal <= 0) continue;
      const key = line.slice(0, equal).trim();
      // 允许任意 *_API_KEY，方便 llm_profiles.yaml 自定义 Provider。
      if (!ALLOWED_KEYS.has(key) && !key.endsWith("_API_KEY")) continue;
      result[key] = stripQuotes(line.slice(equal + 1).trim());
    }
  } catch (error) {
    console.warn(`[env] 读取 ${filePath} 失败：${error.message}`);
  }
  return result;
}

module.exports = { readEnvFile, ALLOWED_KEYS };
