const BASE = import.meta.env.VITE_API_BASE || "/api";

function authHeaders() {
  try {
    const token = window.localStorage.getItem("contractguard.apiToken");
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

function safeString(value) {
  const text = JSON.stringify(value) || "";
  return text.length > 300 ? `${text.slice(0, 300)}…` : text;
}

function detailMessage(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
          const text = item.msg || item.message || safeString(item);
          return location ? `${location}：${text}` : text;
        }
        return String(item);
      })
      .filter(Boolean)
      .join("；");
  }
  if (detail && typeof detail === "object") {
    const text = detail.message || detail.msg;
    return typeof text === "string" ? text : safeString(detail);
  }
  return "";
}

async function parseError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const message =
    detailMessage(payload?.detail) ||
    payload?.message ||
    `请求失败（HTTP ${response.status}）`;
  return new ApiError(message, response.status, payload);
}

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { ...(options.headers || {}), ...authHeaders() },
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return response.json();
}

export async function getMeta() {
  return request("/meta");
}

export async function getSamples() {
  return request("/samples");
}

function routeQuery(llmRoute, batchId) {
  const params = new URLSearchParams();
  if (llmRoute) params.set("llm_route", llmRoute);
  if (batchId) params.set("batch_id", batchId);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export async function startReview(file, llmRoute = "", batchId = "") {
  const form = new FormData();
  form.append("file", file);
  return request(`/reviews${routeQuery(llmRoute, batchId)}`, {
    method: "POST",
    body: form,
  });
}

export async function startSample(sampleId, llmRoute = "", batchId = "") {
  return request(
    `/reviews/samples/${encodeURIComponent(sampleId)}${routeQuery(llmRoute, batchId)}`,
    { method: "POST" },
  );
}

export async function getReview(jobId) {
  return request(`/reviews/${encodeURIComponent(jobId)}`);
}

export async function listReviews({ status, limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return request(`/reviews?${params.toString()}`);
}

export async function updateSuggestion(jobId, findingId, suggestedText) {
  return request(
    `/reviews/${encodeURIComponent(jobId)}/findings/${encodeURIComponent(findingId)}/suggestion`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ suggested_text: suggestedText }),
    },
  );
}

export async function decide(jobId, findingId, decision, note = "") {
  return request(
    `/reviews/${encodeURIComponent(jobId)}/findings/${encodeURIComponent(findingId)}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, note }),
    },
  );
}

export async function resumeReview(jobId) {
  return request(`/reviews/${encodeURIComponent(jobId)}/resume`, {
    method: "POST",
  });
}

export async function cancelReview(jobId) {
  return request(`/reviews/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
  });
}

export async function retryReview(jobId) {
  return request(`/reviews/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
  });
}

export async function getReportPreview(jobId) {
  return request(`/reviews/${encodeURIComponent(jobId)}/report/preview`);
}

export async function getBatch(batchId) {
  return request(`/batches/${encodeURIComponent(batchId)}`);
}

export async function decideBatch(batchId, { decision, note = "", findingIds } = {}) {
  return request(`/batches/${encodeURIComponent(batchId)}/decisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      decision,
      note,
      finding_ids: findingIds || null,
    }),
  });
}

export async function getBatchReportPreview(batchId) {
  return request(`/batches/${encodeURIComponent(batchId)}/report/preview`);
}

async function downloadBlob(url, fallbackName) {
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) {
    throw await parseError(response);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const matched = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const filename = matched ? decodeURIComponent(matched[1]) : fallbackName;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Electron 接管下载时立即 revoke 可能取消下载，延迟释放更稳。
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  return filename;
}

export async function downloadReport(jobId, format) {
  return downloadBlob(
    `${BASE}/reviews/${encodeURIComponent(jobId)}/report?format=${format}`,
    `ContractGuard_report.${format}`,
  );
}

export async function getRevisedContract(jobId) {
  return request(
    `/reviews/${encodeURIComponent(jobId)}/revised-contract?format=json`,
  );
}

export async function downloadRevisedContract(jobId, format = "docx") {
  return downloadBlob(
    `${BASE}/reviews/${encodeURIComponent(jobId)}/revised-contract?format=${format}`,
    `ContractGuard_revised.${format}`,
  );
}

export async function downloadBatchReport(batchId, format) {
  return downloadBlob(
    `${BASE}/batches/${encodeURIComponent(batchId)}/report?format=${format}`,
    `ContractGuard_batch.${format}`,
  );
}
