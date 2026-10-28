import { useRef, useState } from "react";
import { ACCEPT, DEFAULT_MAX_MB, validateContractFile } from "../fileValidation.js";
import { FileIcon, UploadIcon } from "./Icons.jsx";

const TASK_LABELS = {
  extract: "抽取",
  agent_commercial: "商务",
  agent_liability: "违约",
  agent_compliance: "争议",
  redline: "建议",
};

function summarizeModels(models = {}) {
  return Object.entries(TASK_LABELS)
    .map(([task, label]) => {
      const chain = models?.[task] || [];
      return chain.length ? `${label} ${chain[0]}` : null;
    })
    .filter(Boolean)
    .join(" · ");
}

function fullModelChain(models = {}) {
  return Object.entries(TASK_LABELS)
    .map(([task, label]) => {
      const chain = models?.[task] || [];
      return chain.length ? `${label}：${chain.join(" → ")}` : null;
    })
    .filter(Boolean)
    .join("\n");
}

export default function UploadPanel({
  samples = [],
  disabled = false,
  maxMb = DEFAULT_MAX_MB,
  ocrMaxPages = 50,
  maxBatch = 3,
  routes = [],
  routeId = "",
  onRouteChange,
  mode = "mock",
  onFile,
  onFiles,
  onSample,
}) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [selectedSample, setSelectedSample] = useState("");
  const selectedRoute = routes.find((route) => route.id === routeId) || null;
  const routeDisabled = disabled || mode !== "live" || routes.length === 0;
  const routeSummary = selectedRoute ? summarizeModels(selectedRoute.models) : "";
  const routeChain = selectedRoute ? fullModelChain(selectedRoute.models) : "";

  const acceptFiles = (files) => {
    const list = Array.from(files || []).filter(Boolean);
    if (!list.length) return;
    if (onFiles) {
      onFiles(list);
      return;
    }
    const result = validateContractFile(list[0], maxMb);
    if (!result.ok) {
      onFile?.(null, result.message);
      return;
    }
    onFile?.(list[0]);
  };

  return (
    <section className="card upload-card">
      <div className="card-heading">
        <div>
          <p className="eyebrow">STEP 01</p>
          <h2>上传采购合同</h2>
        </div>
        <span className="mini-badge">货物采购 · 可批量</span>
      </div>

      {routes.length > 0 ? (
        <div className="route-row">
          <label htmlFor="llm-route">审查模型</label>
          <select
            id="llm-route"
            className="select"
            value={routeId}
            disabled={routeDisabled}
            title={selectedRoute?.description || ""}
            onChange={(event) => onRouteChange?.(event.target.value)}
          >
            {routes.map((route) => (
              <option
                key={route.id}
                value={route.id}
                disabled={mode === "live" && !route.available}
              >
                {route.label}
                {mode === "live" && !route.available ? "（不可用）" : ""}
              </option>
            ))}
          </select>
          {mode !== "live" ? (
            <p className="route-summary">
              MOCK 模式不调用外部模型；配置任一 Provider 的 API Key 后可选择路由。
            </p>
          ) : selectedRoute && !selectedRoute.available ? (
            <p className="route-reason">{selectedRoute.reason}</p>
          ) : (
            <>
              <p className="route-summary" title={routeChain}>
                {routeSummary || "该路由未配置任务模型"}
              </p>
              {routeChain ? (
                <details className="route-details">
                  <summary>查看各任务回退链</summary>
                  <pre>{routeChain}</pre>
                </details>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      <div
        className={`dropzone ${dragging ? "is-dragging" : ""} ${disabled ? "is-disabled" : ""}`}
        onClick={() => !disabled && inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!disabled) acceptFiles(event.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (!disabled) inputRef.current?.click();
          }
        }}
        aria-label="上传合同文件"
      >
        <span className="dropzone-icon">
          <UploadIcon size={26} />
        </span>
        <strong>拖拽合同到这里</strong>
        <span>或点击选择文件</span>
        <small>
          支持 PDF / DOCX / TXT / JPG / PNG · 最大 {maxMb}MB · 一次最多 {maxBatch} 份
        </small>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          disabled={disabled}
          onChange={(event) => {
            acceptFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </div>
      <p className="hint">
        扫描件自动 OCR（最多 {ocrMaxPages} 页），识别结果需人工核对。
      </p>

      {samples.length > 0 && (
        <>
          <div className="or-divider">
            <span>或使用内置示例</span>
          </div>
          <div className="sample-row">
            <select
              className="select"
              value={selectedSample}
              disabled={disabled}
              onChange={(event) => setSelectedSample(event.target.value)}
              aria-label="选择示例合同"
            >
              <option value="">选择一份示例合同…</option>
              {samples.map((sample) => (
                <option key={sample.id} value={sample.id}>
                  {sample.title}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={disabled || !selectedSample}
              onClick={() => onSample(selectedSample)}
            >
              <FileIcon size={17} />
              审查示例
            </button>
          </div>
          <p className="hint">
            示例合同全部为合成数据，用于演示与评测，不构成真实合同。
          </p>
        </>
      )}
    </section>
  );
}
