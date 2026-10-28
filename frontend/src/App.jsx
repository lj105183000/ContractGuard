import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelReview,
  decide,
  decideBatch,
  downloadBatchReport,
  downloadReport,
  downloadRevisedContract,
  getBatch,
  getMeta,
  getReview,
  getSamples,
  resumeReview,
  retryReview,
  startReview,
  startSample,
  updateSuggestion,
} from "./api.js";
import AgentTimeline from "./components/AgentTimeline.jsx";
import BatchPanel from "./components/BatchPanel.jsx";
import EmptyState from "./components/EmptyState.jsx";
import FindingDetail from "./components/FindingDetail.jsx";
import HistoryPanel from "./components/HistoryPanel.jsx";
import ProcessingState from "./components/ProcessingState.jsx";
import ResultsView from "./components/ResultsView.jsx";
import ServerHistoryPanel from "./components/ServerHistoryPanel.jsx";
import Toast from "./components/Toast.jsx";
import TokenGate from "./components/TokenGate.jsx";
import UploadPanel from "./components/UploadPanel.jsx";
import {
  AlertIcon,
  HistoryIcon,
  RefreshIcon,
  ShieldIcon,
  UploadIcon,
} from "./components/Icons.jsx";
import { desktopBridge, isDesktop } from "./desktop.js";
import { buildErrorGuidance } from "./errorGuidance.js";
import { DEFAULT_MAX_MB, validateContractFile } from "./fileValidation.js";

export default function App() {
  const [meta, setMeta] = useState(null);
  const [samples, setSamples] = useState([]);
  const [phase, setPhase] = useState("idle");
  const [job, setJob] = useState(null);
  const [batch, setBatch] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [filter, setFilter] = useState("all");
  const [activeView, setActiveView] = useState("findings");
  const [locateTarget, setLocateTarget] = useState(null);
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [serverHistoryOpen, setServerHistoryOpen] = useState(false);
  const [historyMode, setHistoryMode] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [desktopDragging, setDesktopDragging] = useState(false);
  const [llmRoute, setLlmRoute] = useState("");
  const notifiedJobs = useRef(new Set());
  const historySaveWarned = useRef(new Set());
  const batchActive = Boolean(
    batch?.items?.some((item) =>
      ["queued", "uploading", "running", "awaiting_review", "resuming"].includes(
        item.status,
      ),
    ),
  );
  const isWorking =
    phase === "uploading" || phase === "processing" || batchActive;
  const showProcessing = phase === "uploading" || phase === "processing";
  const maxUploadMb = meta?.max_upload_mb ?? DEFAULT_MAX_MB;
  const ocrMaxPages = meta?.ocr_max_pages ?? 50;

  const showToast = useCallback((message, type = "success", detail = "") => {
    setToast({ message, type, detail, id: Date.now() });
  }, []);
  const closeToast = useCallback(() => setToast(null), []);

  const applyRestoredJob = useCallback((next) => {
    setJob(next);
    setErrorInfo(null);
    setSelectedId(null);
    setFilter("all");
    setActiveView("findings");
    setLocateTarget(null);
    setHistoryMode(false);
    if (
      [
        "completed",
        "completed_with_warnings",
        "awaiting_review",
        "resuming",
      ].includes(next.status)
    ) {
      setPhase("done");
    } else if (["queued", "running"].includes(next.status)) {
      setPhase("processing");
    } else if (next.status === "failed") {
      setPhase("error");
      setErrorInfo({
        message: next.error || "审查失败，请查看运行提示。",
        source: "job",
        status: null,
      });
    } else {
      setPhase("idle");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getMeta(), getSamples()]).then(([metaResult, sampleResult]) => {
      if (cancelled) return;
      if (metaResult.status === "fulfilled") {
        setMeta(metaResult.value);
      }
      if (sampleResult.status === "fulfilled") {
        setSamples(sampleResult.value);
      }
      if (
        (metaResult.status === "rejected" &&
          metaResult.reason?.status === 401) ||
        (sampleResult.status === "rejected" &&
          sampleResult.reason?.status === 401)
      ) {
        setAuthRequired(true);
        return;
      }
      if (metaResult.status === "rejected" || sampleResult.status === "rejected") {
        const error =
          metaResult.status === "rejected" ? metaResult.reason : sampleResult.reason;
        showToast("无法连接后端", "error", error?.message || "请检查后端服务。");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [showToast]);

  useEffect(() => {
    if (window.localStorage.getItem("contractguard.batch")) return undefined;
    const saved = window.localStorage.getItem("contractguard.jobId");
    if (!saved) return undefined;
    let cancelled = false;
    getReview(saved)
      .then((next) => {
        if (!cancelled) applyRestoredJob(next);
      })
      .catch(() => {
        if (!cancelled) window.localStorage.removeItem("contractguard.jobId");
      });
    return () => {
      cancelled = true;
    };
  }, [applyRestoredJob]);

  useEffect(() => {
    const raw = window.localStorage.getItem("contractguard.batch");
    if (!raw) return undefined;
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      window.localStorage.removeItem("contractguard.batch");
      return undefined;
    }
    const savedItems = Array.isArray(parsed)
      ? parsed
      : Array.isArray(parsed?.items)
        ? parsed.items
        : [];
    const savedBatchId = Array.isArray(parsed) ? null : parsed?.id || null;
    if (!savedItems.length) {
      window.localStorage.removeItem("contractguard.batch");
      return undefined;
    }
    const items = savedItems.map((item, index) => ({
      key: item.key || `restored-${index}`,
      filename: item.filename || "未命名合同",
      status: item.jobId ? item.status || "queued" : "failed",
      jobId: item.jobId || null,
      job: null,
      error: item.error || (item.jobId ? null : "任务未启动，请重新上传。"),
    }));
    setBatch({ id: savedBatchId, items });
    setPhase("batch");
    setJob(null);

    const withJobIds = items.filter((item) => item.jobId);
    if (!withJobIds.length) return undefined;
    let cancelled = false;
    Promise.all(
      withJobIds.map(async (item) => {
        try {
          const next = await getReview(item.jobId);
          return {
            key: item.key,
            job: next,
            status: next.status,
            error: next.error || null,
          };
        } catch (err) {
          return {
            key: item.key,
            status: "failed",
            error: err.message || "任务不存在",
          };
        }
      }),
    ).then((updates) => {
      if (cancelled) return;
      setBatch((previous) => {
        if (!previous) return previous;
        const byKey = new Map(updates.map((update) => [update.key, update]));
        return {
          ...previous,
          items: previous.items.map((item) =>
            byKey.has(item.key) ? { ...item, ...byKey.get(item.key) } : item,
          ),
        };
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!meta) return;
    const routes = meta.llm_routes || [];
    if (!routes.length) return;
    const current = routes.find((route) => route.id === llmRoute);
    const preferAvailable = meta.mode === "live";
    if (!current || (preferAvailable && !current.available)) {
      const fallback =
        routes.find(
          (route) =>
            route.id === meta.llm_route &&
            (!preferAvailable || route.available),
        ) ||
        (preferAvailable ? routes.find((route) => route.available) : null) ||
        routes.find((route) => route.id === meta.llm_route) ||
        routes[0];
      setLlmRoute(fallback?.id || "");
    }
  }, [meta, llmRoute]);

  useEffect(() => {
    if (
      !job?.job_id ||
      !["queued", "running", "resuming", "awaiting_review"].includes(job.status)
    ) {
      return undefined;
    }
    let cancelled = false;
    let stopped = false;
    let inFlight = false;
    let timer = null;
    const tick = async () => {
      if (cancelled || stopped || inFlight) return;
      inFlight = true;
      try {
        const next = await getReview(job.job_id);
        if (cancelled) return;
        setJob(next);
        if (["completed", "completed_with_warnings"].includes(next.status)) {
          setPhase("done");
          setSelectedId(null);
          if (desktopBridge && !notifiedJobs.current.has(next.job_id)) {
            notifiedJobs.current.add(next.job_id);
            const summary = next.result?.summary;
            desktopBridge.notifyReviewCompleted({
              filename: next.filename || next.result?.contract?.filename || "合同",
              total: summary?.total ?? 0,
              high: summary?.high ?? 0,
            });
          }
        } else if (next.status === "awaiting_review") {
          setPhase("done");
        } else if (next.status === "resuming") {
          setPhase("done");
        } else if (next.status === "failed") {
          setPhase("error");
          setErrorInfo({
            message: next.error || "审查失败，请查看运行提示。",
            source: "job",
            status: null,
          });
        } else if (next.status === "cancelled") {
          setPhase("error");
          setErrorInfo({
            message: next.error || "任务已取消。",
            source: "job",
            status: null,
          });
        } else {
          // 从一次瞬时网络错误中恢复后，回到审查中状态。
          setPhase("processing");
        }
      } catch (err) {
        if (!cancelled) {
          const status = typeof err?.status === "number" ? err.status : null;
          setPhase("error");
          setErrorInfo({
            message: err.message || "无法获取任务状态。",
            source: status === null ? "network" : "job",
            status,
          });
          if (status === 404) {
            stopped = true;
            if (timer) window.clearInterval(timer);
          }
        }
      } finally {
        inFlight = false;
      }
    };
    tick();
    // awaiting_review 很少变化，降频避免反复拉取完整合同。
    const interval = job.status === "awaiting_review" ? 5000 : 900;
    if (!stopped) timer = window.setInterval(tick, interval);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [job?.job_id, job?.status]);

  const batchPollKey = batch
    ? batch.items
        .map((item) => `${item.key}:${item.status}:${item.jobId || ""}`)
        .join("|")
    : "";

  useEffect(() => {
    if (!batch) return undefined;
    const active = batch.items.filter(
      (item) =>
        item.jobId &&
        ["queued", "running", "resuming", "awaiting_review"].includes(
          item.status,
        ),
    );
    if (!active.length) return undefined;
    let cancelled = false;
    let inFlight = false;
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const updates = await Promise.all(
          active.map(async (item) => {
            try {
              const next = await getReview(item.jobId);
              return {
                key: item.key,
                job: next,
                status: next.status,
                error: next.error || null,
              };
            } catch (err) {
              const status =
                typeof err?.status === "number" ? err.status : null;
              return {
                key: item.key,
                // 404 说明任务已被清理；其余网络/5xx 保留原状态下轮重试。
                status: status === 404 ? "failed" : item.status,
                error: err.message || "获取任务失败",
              };
            }
          }),
        );
        if (cancelled) return;
        setBatch((previous) => {
          if (!previous) return previous;
          const byKey = new Map(updates.map((update) => [update.key, update]));
          return {
            ...previous,
            items: previous.items.map((item) =>
              byKey.has(item.key) ? { ...item, ...byKey.get(item.key) } : item,
            ),
          };
        });
      } finally {
        inFlight = false;
      }
    };
    tick();
    const timer = window.setInterval(tick, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [batchPollKey]);

  useEffect(() => {
    if (!batch) return;
    const compact = batch.items.map(({ key, filename, jobId, status }) => ({
      key,
      filename,
      jobId,
      status,
    }));
    window.localStorage.setItem(
      "contractguard.batch",
      JSON.stringify({ id: batch.id || null, items: compact }),
    );
  }, [batch]);

  const begin = async (starter, filename) => {
    setPhase("uploading");
    setErrorInfo(null);
    setSelectedId(null);
    setActiveView("findings");
    setLocateTarget(null);
    setHistoryMode(false);
    setHistoryOpen(false);
    setBatch(null);
    window.localStorage.removeItem("contractguard.batch");
    setJob({
      job_id: null,
      status: "uploading",
      progress: 0,
      events: [],
      filename,
      warnings: [],
    });
    try {
      const response = await starter();
      window.localStorage.setItem("contractguard.jobId", response.job_id);
      setJob({
        job_id: response.job_id,
        status: "queued",
        progress: 0,
        events: [],
        filename,
        warnings: [],
      });
      setPhase("processing");
    } catch (err) {
      setPhase("error");
      setErrorInfo({
        message: err.message || "启动审查失败。",
        source: "start",
        status: typeof err?.status === "number" ? err.status : null,
      });
      showToast("启动审查失败", "error", err.message);
    }
  };

  const startBatch = async (files) => {
    const batchId =
      globalThis.crypto?.randomUUID?.() ||
      `b-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const items = files.map((file, index) => ({
      key: `${Date.now()}-${index}-${file.name}`,
      filename: file.name,
      status: "queued",
      jobId: null,
      job: null,
      error: null,
    }));
    setBatch({ id: batchId, items });
    setPhase("batch");
    setErrorInfo(null);
    setSelectedId(null);
    setActiveView("findings");
    setLocateTarget(null);
    setHistoryMode(false);
    setHistoryOpen(false);
    setJob(null);
    window.localStorage.removeItem("contractguard.jobId");
    window.localStorage.setItem(
      "contractguard.batch",
      JSON.stringify({
        id: batchId,
        items: items.map(({ key, filename, status, jobId }) => ({
          key,
          filename,
          status,
          jobId,
        })),
      }),
    );

    let cursor = 0;
    const worker = async () => {
      while (cursor < files.length) {
        const index = cursor;
        cursor += 1;
        const file = files[index];
        const key = items[index].key;
        try {
          const response = await startReview(file, llmRoute, batchId);
          setBatch((previous) =>
            previous
              ? {
                  ...previous,
                  items: previous.items.map((item) =>
                    item.key === key
                      ? { ...item, jobId: response.job_id, status: "queued" }
                      : item,
                  ),
                }
              : previous,
          );
        } catch (err) {
          setBatch((previous) =>
            previous
              ? {
                  ...previous,
                  items: previous.items.map((item) =>
                    item.key === key
                      ? {
                          ...item,
                          status: "failed",
                          error: err.message || "启动审查失败。",
                        }
                      : item,
                  ),
                }
              : previous,
          );
        }
      }
    };
    await Promise.all([worker(), worker()]);
  };

  const handleFiles = async (files) => {
    const list = Array.from(files || []).filter(Boolean);
    if (!list.length) return;
    const valid = [];
    const errors = [];
    for (const file of list) {
      const result = validateContractFile(file, maxUploadMb);
      if (result.ok) valid.push(file);
      else errors.push(`${file.name}：${result.message}`);
    }
    if (errors.length) {
      showToast("部分文件未通过校验", "error", errors.join("；"));
    }
    if (!valid.length) return;
    const selected = valid.slice(0, 3);
    if (valid.length > 3) {
      showToast("批量上限", "error", "一次最多 3 份，已取前 3 份。");
    }
    if (selected.length === 1) {
      await begin(() => startReview(selected[0], llmRoute), selected[0].name);
      return;
    }
    await startBatch(selected);
  };

  const handleSample = async (sampleId) => {
    const sample = samples.find((item) => item.id === sampleId);
    await begin(
      () => startSample(sampleId, llmRoute),
      sample?.filename || "示例合同",
    );
  };

  // 桌面端：审查完成后把完整快照写入 userData/history，决策更新时覆盖同一条。
  // 大结果在连续复核时只落盘一次；保存失败时每个 job 只提示一次。
  useEffect(() => {
    if (!desktopBridge || historyMode) return undefined;
    if (
      window.localStorage.getItem("contractguard.autoSaveHistory") === "0"
    ) {
      return undefined;
    }
    if (!job?.job_id || !job?.result) return undefined;
    if (!["completed", "completed_with_warnings"].includes(job.status)) return undefined;
    const jobId = job.job_id;
    const timer = window.setTimeout(() => {
      const snapshot = {
        jobId,
        filename: job.filename || job.result?.contract?.filename || "未命名合同",
        savedAt: new Date().toISOString(),
        mode: job.result?.mode || meta?.mode || "mock",
        job,
      };
      desktopBridge.history
        .save(snapshot)
        .catch((error) => {
          // 合同全文可能让单条快照超过 20MB；剥离 full_text 后重试一次。
          if (!job.result?.full_text) throw error;
          return desktopBridge.history.save({
            ...snapshot,
            job: {
              ...job,
              result: { ...job.result, full_text: "" },
            },
          });
        })
        .catch((error) => {
          if (historySaveWarned.current.has(jobId)) return;
          historySaveWarned.current.add(jobId);
          showToast(
            "本地历史未保存",
            "error",
            error?.message || "结果可能超过 20MB，或磁盘写入失败。",
          );
        });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [job, meta?.mode, historyMode, showToast]);

  const handleFilesRef = useRef(handleFiles);
  handleFilesRef.current = handleFiles;

  // 桌面端：整窗拖拽。面板内拖拽仍由 UploadPanel 处理，这里通过 defaultPrevented 去重。
  useEffect(() => {
    if (!isDesktop) return undefined;
    let depth = 0;
    const hasFiles = (event) =>
      Array.from(event.dataTransfer?.types || []).includes("Files");
    const reset = () => {
      depth = 0;
      setDesktopDragging(false);
    };
    const onDragEnter = (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth += 1;
      setDesktopDragging(true);
    };
    const onDragOver = (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    };
    const onDragLeave = (event) => {
      if (!hasFiles(event) && depth === 0) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDesktopDragging(false);
    };
    const onDrop = (event) => {
      const alreadyHandled = event.defaultPrevented;
      event.preventDefault();
      reset();
      if (alreadyHandled) return;
      const files = Array.from(event.dataTransfer?.files || []);
      if (!files.length) return;
      if (isWorking) {
        showToast("审查进行中", "error", "请等待当前任务完成后再上传新合同。");
        return;
      }
      handleFilesRef.current(files);
    };
    document.addEventListener("dragenter", onDragEnter);
    document.addEventListener("dragover", onDragOver);
    document.addEventListener("dragleave", onDragLeave);
    document.addEventListener("drop", onDrop);
    window.addEventListener("dragend", reset);
    window.addEventListener("blur", reset);
    return () => {
      document.removeEventListener("dragenter", onDragEnter);
      document.removeEventListener("dragover", onDragOver);
      document.removeEventListener("dragleave", onDragLeave);
      document.removeEventListener("drop", onDrop);
      window.removeEventListener("dragend", reset);
      window.removeEventListener("blur", reset);
    };
  }, [isDesktop, isWorking, showToast, maxUploadMb]);

  const handleHistoryOpen = (entry) => {
    if (
      batchActive &&
      !window.confirm("批次任务仍在处理中，打开历史会离开当前批次，继续吗？")
    ) {
      return;
    }
    setHistoryOpen(false);
    setHistoryMode(true);
    setBatch(null);
    setJob(entry.job);
    setPhase("done");
    setErrorInfo(null);
    setSelectedId(null);
    setFilter("all");
    setActiveView("findings");
    setLocateTarget(null);
    window.localStorage.removeItem("contractguard.jobId");
    window.localStorage.removeItem("contractguard.batch");
    showToast("已打开本地历史快照", "success", entry.filename || "");
  };

  const handleServerJobOpen = (next) => {
    if (
      batchActive &&
      !window.confirm("批次任务仍在处理中，打开历史会离开当前批次，继续吗？")
    ) {
      return;
    }
    setServerHistoryOpen(false);
    setBatch(null);
    window.localStorage.removeItem("contractguard.batch");
    applyRestoredJob(next);
    if (next?.job_id) {
      window.localStorage.setItem("contractguard.jobId", next.job_id);
    }
    if (next?.status === "failed") {
      showToast("该任务已中断", "error", next.error || "请重新审查。");
    } else {
      showToast("已打开历史任务", "success", next?.filename || "");
    }
  };

  const openBatchItem = async (item) => {
    if (!item?.jobId) return;
    try {
      const next = await getReview(item.jobId);
      applyRestoredJob(next);
      setBatch((previous) =>
        previous
          ? {
              ...previous,
              items: previous.items.map((entry) =>
                entry.key === item.key
                  ? {
                      ...entry,
                      job: next,
                      status: next.status,
                      error: next.error || null,
                    }
                  : entry,
              ),
            }
          : previous,
      );
      window.localStorage.setItem("contractguard.jobId", item.jobId);
    } catch (err) {
      showToast("打开批次任务失败", "error", err.message);
    }
  };

  const clearBatch = () => {
    if (
      batchActive &&
      !window.confirm("批次任务仍在处理中，清空会失去这些任务的跟踪，继续吗？")
    ) {
      return;
    }
    setBatch(null);
    setPhase("idle");
    setJob(null);
    setErrorInfo(null);
    setSelectedId(null);
    window.localStorage.removeItem("contractguard.batch");
    window.localStorage.removeItem("contractguard.jobId");
  };

  const refreshBatch = async () => {
    if (!batch?.id) return;
    try {
      const data = await getBatch(batch.id);
      setBatch((previous) => {
        if (!previous) return previous;
        const byId = new Map(
          (data.items || []).map((item) => [item.job_id, item]),
        );
        return {
          ...previous,
          items: previous.items.map((item) => {
            const fresh = byId.get(item.jobId);
            if (!fresh) return item;
            return {
              ...item,
              status: fresh.status,
              progress: fresh.progress,
              summary: fresh.summary,
              error: fresh.error,
            };
          }),
        };
      });
    } catch {
      // 刷新失败不打断当前页面；下一次轮询会再试。
    }
  };

  const handleBatchDecision = async (decision) => {
    if (!batch?.id) return;
    const label = decision === "accepted" ? "采纳" : "忽略";
    if (
      !window.confirm(
        `确定要${label}批次内所有「已核实高风险」吗？此操作会写入复核记录。`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const response = await decideBatch(batch.id, {
        decision,
        note: `批次${label}`,
      });
      await refreshBatch();
      showToast(
        "批量复核完成",
        "success",
        `已处理 ${response.decided} 条；跳过 ${response.skipped} 条`,
      );
    } catch (err) {
      showToast("批量复核失败", "error", err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleBatchExport = async (format) => {
    if (!batch?.id) return;
    setBusy(true);
    try {
      const filename = await downloadBatchReport(batch.id, format);
      showToast("批次报告已开始下载", "success", filename);
    } catch (err) {
      if (err.status === 409) {
        const blocked = err.payload?.blocked_items?.length || 0;
        showToast(
          `还有 ${blocked} 份合同未完成复核`,
          "error",
          err.message,
        );
        await refreshBatch();
      } else {
        showToast("批次报告导出失败", "error", err.message);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleUpdateSuggestion = async (finding, suggestedText) => {
    if (historyMode) {
      showToast("历史快照为只读", "error", "请重新上传合同后再编辑建议。");
      return;
    }
    if (!job?.job_id) return;
    setBusy(true);
    try {
      const updated = await updateSuggestion(
        job.job_id,
        finding.id,
        suggestedText,
      );
      setJob((previous) => {
        if (!previous?.result) return previous;
        return {
          ...previous,
          result: {
            ...previous.result,
            findings: previous.result.findings.map((item) =>
              item.id === updated.id ? updated : item,
            ),
          },
        };
      });
      showToast("修改建议已保存", "success", "生成修订稿时会使用人工编辑文本");
    } catch (err) {
      showToast("保存修改建议失败", "error", err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDecision = async (finding, decision, note) => {
    if (historyMode) {
      showToast("历史快照为只读", "error", "请重新上传合同后继续复核。");
      return;
    }
    if (!job?.job_id) return;
    setBusy(true);
    try {
      const response = await decide(job.job_id, finding.id, decision, note);
      const allBlockingResolved =
        (response.summary?.blocking_high_pending ?? 0) === 0;
      setJob((previous) => {
        if (!previous?.result) return previous;
        return {
          ...previous,
          status:
            allBlockingResolved && previous.status === "awaiting_review"
              ? "resuming"
              : previous.status,
          result: {
            ...previous.result,
            findings: previous.result.findings.map((item) =>
              item.id === response.finding.id ? response.finding : item,
            ),
            summary: response.summary,
          },
        };
      });
      setBatch((previous) =>
        previous
          ? {
              ...previous,
              items: previous.items.map((item) => {
                if (item.jobId !== job.job_id) return item;
                const nextStatus =
                  allBlockingResolved && item.status === "awaiting_review"
                    ? "resuming"
                    : item.status;
                const nextJob =
                  item.job?.result
                    ? {
                        ...item.job,
                        status: nextStatus,
                        result: {
                          ...item.job.result,
                          findings: item.job.result.findings.map((entry) =>
                            entry.id === response.finding.id
                              ? response.finding
                              : entry,
                          ),
                          summary: response.summary,
                        },
                      }
                    : item.job;
                return {
                  ...item,
                  status: nextStatus,
                  summary: response.summary,
                  job: nextJob,
                  error: null,
                };
              }),
            }
          : previous,
      );
      if (batch?.id) refreshBatch();
      showToast(
        decision === "accepted" ? "已采纳该建议" : "已忽略该风险",
        "success",
        finding.title,
      );
    } catch (err) {
      showToast("复核失败", "error", err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleExport = async (format) => {
    if (historyMode) {
      showToast("历史快照为只读", "error", "请重新上传合同后再导出报告。");
      return;
    }
    if (!job?.job_id) return;
    setBusy(true);
    try {
      const filename = await downloadReport(job.job_id, format);
      showToast("报告已开始下载", "success", filename);
    } catch (err) {
      if (err.status === 409 && err.payload?.pending_findings?.length) {
        const pending = err.payload.pending_findings.length;
        setFilter("high");
        showToast(`还有 ${pending} 条高风险未复核`, "error", err.message);
      } else if (err.status === 409) {
        showToast("暂不能导出", "error", err.message);
      } else {
        showToast("导出失败", "error", err.message);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleRevisedExport = async (format) => {
    if (historyMode) {
      showToast("历史快照为只读", "error", "请重新上传合同后再生成修订稿。");
      return;
    }
    if (!job?.job_id) return;
    setBusy(true);
    try {
      const filename = await downloadRevisedContract(job.job_id, format);
      showToast("修订稿已开始下载", "success", filename);
    } catch (err) {
      if (err.status === 409 && err.payload?.pending_findings?.length) {
        const pending = err.payload.pending_findings.length;
        setFilter("high");
        showToast(`还有 ${pending} 条高风险未复核`, "error", err.message);
      } else if (err.status === 409) {
        showToast("暂不能生成修订稿", "error", err.message);
      } else {
        showToast("修订稿导出失败", "error", err.message);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async (jobId = job?.job_id) => {
    if (!jobId) return;
    if (!window.confirm("确定取消这个审查任务吗？已上传的源文件会被清理。")) {
      return;
    }
    setBusy(true);
    try {
      const response = await cancelReview(jobId);
      if (response.status === "cancelled") {
        setJob((previous) =>
          previous
            ? { ...previous, status: "cancelled", error: "用户取消。" }
            : previous,
        );
        setPhase("error");
        setErrorInfo({ message: "任务已取消。", source: "job", status: null });
      } else {
        showToast("已请求取消", "success", "正在停止当前任务…");
      }
    } catch (err) {
      showToast("取消失败", "error", err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleRetry = async () => {
    if (!job?.job_id) return;
    setBusy(true);
    try {
      await retryReview(job.job_id);
      setJob((previous) =>
        previous
          ? {
              ...previous,
              status: "queued",
              progress: 0,
              error: null,
              result: null,
              events: [],
              warnings: [],
              interrupt_id: null,
              pending_review_ids: [],
            }
          : previous,
      );
      setErrorInfo(null);
      setPhase("processing");
      showToast("已重新入队", "success", "任务将重新审查");
    } catch (err) {
      showToast("重试失败", "error", err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleResume = async (jobId) => {
    if (!jobId) return;
    setBusy(true);
    try {
      await resumeReview(jobId);
      setJob((previous) =>
        previous ? { ...previous, status: "resuming" } : previous,
      );
      showToast("正在完成人工复核", "success");
    } catch (err) {
      showToast("恢复复核失败", "error", err.message);
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setJob(null);
    setBatch(null);
    setPhase("idle");
    setErrorInfo(null);
    setSelectedId(null);
    setFilter("all");
    setActiveView("findings");
    setLocateTarget(null);
    setHistoryMode(false);
    window.localStorage.removeItem("contractguard.jobId");
    window.localStorage.removeItem("contractguard.batch");
  };

  const findings = job?.result?.findings || [];
  const selectedFinding = useMemo(
    () => findings.find((finding) => finding.id === selectedId) || null,
    [findings, selectedId],
  );
  const errorGuidance = useMemo(
    () => buildErrorGuidance(errorInfo, { maxUploadMb, ocrMaxPages }),
    [errorInfo, maxUploadMb, ocrMaxPages],
  );
  const handleSelectFinding = (findingId) => {
    setSelectedId(findingId);
    setLocateTarget(findingId);
  };

  const handleLocate = (finding) => {
    setActiveView("contract");
    setLocateTarget(finding.id);
    setSelectedId(null);
  };

  const showHigh = () => {
    setActiveView("findings");
    setFilter("high");
    window.setTimeout(() => {
      document.querySelector(".findings-section")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }, 50);
  };

  if (authRequired) {
    return <TokenGate onAuthenticated={() => window.location.reload()} />;
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">
            <ShieldIcon size={22} />
          </span>
          <div className="brand-text">
            <strong>合同哨兵</strong>
            <span>ContractGuard</span>
          </div>
        </div>
        <div className="header-right">
          {isDesktop ? (
            <button
              type="button"
              className="header-history"
              onClick={() => setHistoryOpen(true)}
            >
              <HistoryIcon size={16} />
              本地历史
            </button>
          ) : null}
          <button
            type="button"
            className="header-history"
            onClick={() => setServerHistoryOpen(true)}
          >
            <HistoryIcon size={16} />
            任务历史
          </button>
          <span
            className={`mode-badge ${meta?.mode === "live" ? "is-live" : "is-mock"}`}
            title={meta?.mode_reason || ""}
          >
            {meta?.mode === "live" ? "LIVE" : "MOCK"}
          </span>
          <span className="header-stat">
            {meta
              ? `${meta.legal_count} 法条 · ${meta.rule_count} 规则 · ${meta.sample_count} 示例`
              : "正在连接后端…"}
          </span>
          {meta?.llm_model ? (
            <span className="header-model">{meta.llm_model}</span>
          ) : null}
        </div>
      </header>

      {meta?.mode === "mock" ? (
        <div className="global-notice">
          <span>离线演示模式</span>
          <p>{meta.mode_reason}。规则引擎、报告与人工复核仍可完整运行。</p>
        </div>
      ) : null}

      <main className="app-main">
        <aside className="sidebar">
          <UploadPanel
            samples={samples}
            disabled={isWorking}
            maxMb={maxUploadMb}
            ocrMaxPages={ocrMaxPages}
            maxBatch={3}
            routes={meta?.llm_routes || []}
            routeId={llmRoute}
            onRouteChange={setLlmRoute}
            mode={meta?.mode || "mock"}
            onFiles={handleFiles}
            onSample={handleSample}
          />
          {job ? (
            <AgentTimeline job={job} />
          ) : (
            <section className="side-note card">
              <p className="eyebrow">HOW IT WORKS</p>
              <h2>规则 + 模型，双轨审查</h2>
              <p>
                规则引擎负责确定性、高召回；3 个 LLM Agent 负责语义与上下文。两类结果合并后，
                经过证据与法条门禁，才进入报告。
              </p>
              <ul>
                <li>25 条采购审查规则</li>
                <li>BM25 + 向量混合检索</li>
                <li>法条只允许 ID 引用</li>
                <li>高风险必须人工复核</li>
              </ul>
            </section>
          )}
        </aside>

        <section className="workspace">
          {historyMode ? (
            <div className="history-banner">
              <HistoryIcon size={16} />
              <span>正在查看本地历史快照（只读）。</span>
              <button type="button" className="text-button" onClick={reset}>
                返回新审查
              </button>
            </div>
          ) : null}
          {batch && phase === "done" ? (
            <div className="batch-banner">
              <span>正在查看批次中的一份合同。</span>
              <button
                type="button"
                className="text-button"
                onClick={() => setPhase("batch")}
              >
                返回批次列表
              </button>
            </div>
          ) : null}
          {phase === "batch" && batch ? (
            <BatchPanel
              batch={batch}
              busy={busy}
              onOpen={openBatchItem}
              onClear={clearBatch}
              onDecide={handleBatchDecision}
              onExport={handleBatchExport}
              onRefresh={refreshBatch}
            />
          ) : null}
          {phase === "idle" ? <EmptyState meta={meta} /> : null}
          {showProcessing ? (
            <ProcessingState job={job} onCancel={handleCancel} busy={busy} />
          ) : null}
          {phase === "error" ? (
            <div className="error-state card" role="alert">
              <span className="error-icon">
                <AlertIcon size={28} />
              </span>
              <div>
                <p className="eyebrow">REVIEW FAILED</p>
                <h1>{errorGuidance.title}</h1>
                {errorInfo?.message ? (
                  <p className="error-message">{errorInfo.message}</p>
                ) : null}
                {errorGuidance.cause &&
                errorGuidance.cause !== errorInfo?.message ? (
                  <p className="error-cause">{errorGuidance.cause}</p>
                ) : null}
                {errorGuidance.actions.length ? (
                  <ul className="error-guidance">
                    {errorGuidance.actions.map((action) => (
                      <li key={action}>{action}</li>
                    ))}
                  </ul>
                ) : null}
                <div className="error-actions">
                  {job?.job_id &&
                  ["failed", "cancelled"].includes(job.status) ? (
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={busy}
                      onClick={handleRetry}
                    >
                      <RefreshIcon size={17} />
                      重试任务
                    </button>
                  ) : null}
                  <button type="button" className="btn btn-secondary" onClick={reset}>
                    <RefreshIcon size={17} />
                    重新开始
                  </button>
                </div>
                {errorGuidance.showGenericHint ? (
                  <p className="hint">请重试；若持续失败，请查看后端日志。</p>
                ) : null}
              </div>
            </div>
          ) : null}
          {phase === "done" && job?.result ? (
            <ResultsView
              job={job}
              filter={filter}
              onFilter={setFilter}
              selectedId={selectedId}
              onSelect={handleSelectFinding}
              onExport={handleExport}
              onExportRevised={handleRevisedExport}
              onShowHigh={showHigh}
              onReset={reset}
              onResume={handleResume}
              onCancel={handleCancel}
              activeView={activeView}
              onViewChange={setActiveView}
              locateTarget={locateTarget}
              busy={busy}
              readOnly={historyMode}
            />
          ) : null}
        </section>
      </main>

      {selectedFinding && job?.result ? (
        <FindingDetail
          finding={selectedFinding}
          result={job.result}
          onClose={() => setSelectedId(null)}
          onDecide={handleDecision}
          onUpdateSuggestion={handleUpdateSuggestion}
          onLocate={handleLocate}
          busy={busy}
          readOnly={historyMode}
        />
      ) : null}

      {historyOpen ? (
        <HistoryPanel onClose={() => setHistoryOpen(false)} onOpen={handleHistoryOpen} />
      ) : null}

      {serverHistoryOpen ? (
        <ServerHistoryPanel
          onClose={() => setServerHistoryOpen(false)}
          onOpen={handleServerJobOpen}
        />
      ) : null}

      {isDesktop && desktopDragging ? (
        <div className="desktop-drop-overlay" aria-hidden="true">
          <div className="desktop-drop-card">
            <span className="desktop-drop-icon">
              <UploadIcon size={30} />
            </span>
            <strong>松开以审查这些合同</strong>
            <span>支持 PDF / DOCX / TXT / JPG / PNG · 最多 3 份 · 最大 {maxUploadMb}MB</span>
          </div>
        </div>
      ) : null}

      <Toast toast={toast} onClose={closeToast} />
    </div>
  );
}
