import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Check, Copy, Download, FileText, Languages, LoaderCircle, Plus, RefreshCw, Square, X } from "lucide-react";
import { ApiError, RagApi } from "./api";
import type { Paper, TranslationConfig, TranslationDetail, TranslationJob, TranslationSubmission } from "./types";
import GlossaryEditor from "./GlossaryEditor";
import "./research.css";
import Modal from "./Modal";
import "./translation-studio.css";

const active = (job?: TranslationJob | null) => job?.status === "queued" || job?.status === "running";
const labels = { queued: "排队中", running: "翻译中", succeeded: "已完成", failed: "未完成", cancelled: "已停止" };
const languageName = { zh: "简体中文", en: "英语" };

// getRandomValues also works on HTTP demo sites where randomUUID may be unavailable.
function requestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((n) => n.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export default function TranslationStudio({ api: sessionApi, initialDocumentId = "", initialPage = 1, onClose, onUnauthorized }: {
  api: RagApi; initialDocumentId?: string; initialPage?: number; onClose: () => void; onUnauthorized: () => void;
}) {
  const [api] = useState(() => new RagApi(sessionApi.token, sessionApi.base));
  const alive = useRef(true);
  const unauthorized = useRef(onUnauthorized);
  unauthorized.current = onUnauthorized;
  const [config, setConfig] = useState<TranslationConfig | null>(null);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [jobs, setJobs] = useState<TranslationJob[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<TranslationDetail | null>(null);
  const [documentId, setDocumentId] = useState(initialDocumentId);
  const [start, setStart] = useState(initialPage);
  const [end, setEnd] = useState(initialPage);
  const [language, setLanguage] = useState<"zh" | "en">("zh");
  const [glossaryVersion, setGlossaryVersion] = useState<string | null>(null);
  const [shownPage, setShownPage] = useState(initialPage);
  const [preview, setPreview] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [busyAction, setBusyAction] = useState(false);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [copied, setCopied] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [uncertain, setUncertain] = useState(Boolean(sessionApi.pendingTranslationSubmission));
  const attempt = useRef(sessionApi.pendingTranslationSubmission);
  const paper = papers.find((p) => p.document_id === documentId);
  const activeJob = jobs.find(active);
  const page = detail?.pages.find((p) => p.page === shownPage) ?? detail?.pages[0];
  const locked = loading || sending || uncertain;

  function report(e: unknown) {
    if (!alive.current) return;
    if (e instanceof ApiError && e.status === 401) unauthorized.current();
    setError(e instanceof Error ? e.message : "操作失败，请重试。");
  }
  function choose(job: TranslationJob) {
    setSelectedId(job.id);
    setShownPage(job.start_page);
    setCopied(false);
    setError("");
  }
  function newTask(job?: TranslationJob) {
    if (job) {
      setDocumentId(job.document_id);
      setStart(job.start_page);
      setEnd(job.end_page);
      setLanguage(job.target_language);
    }
    setSelectedId("");
    setDetail(null);
    setError("");
  }

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; api.dispose(); };
  }, [api]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    Promise.all([api.translationConfig(), api.documents()]).then(([cfg, docs]) => {
      if (stopped) return;
      setConfig(cfg);
      setPapers(docs.documents);
      setDocumentId((id) => docs.documents.some((p) => p.document_id === id) ? id : docs.documents[0]?.document_id ?? "");
    }).catch(report).finally(() => { if (!stopped) setLoading(false); });
    async function history() {
      try {
        const result = await api.translations();
        if (stopped) return;
        setJobs(result.translations);
        const recovered = result.translations.find((job) => job.request_id === attempt.current?.request_id);
        if (recovered) {
          attempt.current = null;
          sessionApi.pendingTranslationSubmission = null;
          setUncertain(false);
          choose(recovered);
        }
      } catch (e) { if (!stopped) report(e); }
      finally { if (!stopped) timer = setTimeout(history, 3000); }
    }
    void history();
    return () => { stopped = true; clearTimeout(timer); };
  }, [api, refresh, sessionApi]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    setDetail(null);
    setReadError("");
    if (!selectedId) return;
    async function read() {
      try {
        const result = await api.translation(selectedId);
        if (stopped) return;
        setDetail(result);
        setReadError("");
        if (active(result)) timer = setTimeout(read, 1500);
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && e.status === 401) unauthorized.current();
        setReadError(e instanceof Error ? e.message : "读取失败");
        if (!(e instanceof ApiError && (e.status === 401 || e.status === 404))) timer = setTimeout(read, 4000);
      }
    }
    void read();
    return () => { stopped = true; clearTimeout(timer); };
  }, [api, selectedId, refresh]);

  useEffect(() => {
    let stopped = false;
    setPreview("");
    if (selectedId || !documentId || !Number.isInteger(start) || start < 1 || (paper && start > paper.pages)) return;
    setPreviewLoading(true);
    api.sourcePage(documentId, start).then((source) => {
      if (!stopped) setPreview(source.original || "此页没有可提取的文本。扫描页请先进行 OCR。");
    }).catch((e) => { if (!stopped) report(e); }).finally(() => { if (!stopped) setPreviewLoading(false); });
    return () => { stopped = true; };
  }, [api, documentId, start, selectedId, paper]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (sending) return;
    if (!attempt.current && !glossaryVersion) { setError("请等待术语表加载，或先保存术语修改"); return; }
    const max = config?.max_pages ?? 50;
    if (!attempt.current && (!paper || !Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end > paper.pages || end - start + 1 > max)) {
      setError(`请选择有效的 PDF 页码范围，单次最多 ${max} 页。`);
      return;
    }
    const body: TranslationSubmission = attempt.current ?? {
      request_id: requestId(), document_id: documentId, start_page: start, end_page: end, target_language: language, glossary_version: glossaryVersion!,
    };
    attempt.current = body;
    sessionApi.pendingTranslationSubmission = body;
    setSending(true);
    setError("");
    try {
      const job = await api.translate(body);
      if (!alive.current || attempt.current?.request_id !== body.request_id) return;
      attempt.current = null;
      sessionApi.pendingTranslationSubmission = null;
      setUncertain(false);
      setJobs((old) => [job, ...old.filter((j) => j.id !== job.id)]);
      choose(job);
    } catch (e) {
      if (!alive.current || attempt.current?.request_id !== body.request_id) return;
      // A lost acknowledgement is retried with the SAME UUID, never a new paid job.
      const unknown = !(e instanceof ApiError) || e.status === 0 || e.status >= 500;
      setUncertain(unknown);
      if (!unknown) { attempt.current = null; sessionApi.pendingTranslationSubmission = null; }
      report(e);
    } finally { if (alive.current) setSending(false); }
  }

  async function cancel() {
    if (!detail) return;
    setBusyAction(true);
    try {
      const result = await api.cancelTranslation(detail.id);
      if (alive.current) { setDetail(result); setJobs((old) => old.map((j) => j.id === result.id ? result : j)); }
    } catch (e) { report(e); }
    finally { if (alive.current) setBusyAction(false); }
  }
  async function download() {
    if (!detail) return;
    setBusyAction(true);
    try {
      const blob = await api.translationFile(detail.id);
      if (!alive.current) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${detail.filename.replace(/\.pdf$/i, "")}-${detail.start_page}-${detail.end_page}-${detail.target_language}.txt`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { report(e); }
    finally { if (alive.current) setBusyAction(false); }
  }
  async function copy() {
    if (!page?.translation) return;
    try {
      await navigator.clipboard.writeText(page.translation);
      if (alive.current) setCopied(true);
    } catch { setError("浏览器未允许复制，请选中译文复制，或使用导出功能。"); }
  }

  return <Modal className="translation-studio" labelId="translation-title" onClose={onClose}>
    <header className="translation-header">
      <div className="translation-heading"><span className="translation-icon"><Languages size={24} /></span><div>
        <h2 id="translation-title">论文翻译</h2><p>逐页对照，读懂研究细节</p>
      </div></div>
      <button className="icon-button" aria-label="关闭论文翻译" onClick={onClose}><X size={20} /></button>
    </header>
    <div className="translation-layout">
      <aside className="translation-sidebar">
        <div className="translation-section-title"><strong>选择翻译内容</strong><button className="text-button" disabled={locked} onClick={() => newTask()}><Plus size={14} />新建</button></div>
        <form onSubmit={(e) => void submit(e)}>
          <label htmlFor="translation-paper">论文</label>
          <select id="translation-paper" value={documentId} disabled={locked} onChange={(e) => { setDocumentId(e.target.value); setStart(1); setEnd(1); newTask(); }}>
            {!papers.length && <option value="">请先上传 PDF 论文</option>}
            {papers.map((p) => <option key={p.document_id} value={p.document_id}>{p.filename}</option>)}
          </select>
          <label htmlFor="translation-language">目标语言</label>
          <select id="translation-language" value={language} disabled={locked} onChange={(e) => { if (glossaryVersion === null && !window.confirm("切换语言会放弃尚未保存的术语修改，继续？")) return; setLanguage(e.target.value as "zh" | "en"); newTask(); }}>
            <option value="zh">简体中文</option><option value="en">英语</option>
          </select>
          <GlossaryEditor key={language} api={api} language={language} disabled={locked} onVersion={setGlossaryVersion} />
          <div className="translation-range">
            <div><label htmlFor="translation-start">起始页</label><input id="translation-start" type="number" min={1} max={paper?.pages ?? 1} value={start || ""} disabled={locked} onChange={(e) => { setStart(Number(e.target.value)); newTask(); }} required /></div>
            <span>—</span>
            <div><label htmlFor="translation-end">结束页</label><input id="translation-end" type="number" min={start || 1} max={paper?.pages ?? 1} value={end || ""} disabled={locked} onChange={(e) => { setEnd(Number(e.target.value)); newTask(); }} required /></div>
          </div>
          <p className="translation-hint">PDF 共 {paper?.pages ?? "—"} 页 · 按物理页码选择<br />单次最多 {config?.max_pages ?? 50} 页，长论文可分批翻译。</p>
          <button type="submit" className="primary translation-submit" disabled={locked || !config?.enabled || !paper || !!activeJob || !glossaryVersion}>
            {sending ? <LoaderCircle className="spin" size={16} /> : <Languages size={16} />}{sending ? "正在提交…" : "开始翻译"}
          </button>
        </form>
        {uncertain && <div className="translation-notice">尚未确认提交结果。重新确认会使用同一个请求编号。<button className="secondary small" disabled={sending} onClick={() => void submit()}>重新确认提交</button></div>}
        {!loading && !config?.enabled && <p className="translation-notice">翻译服务未启用，请由管理员配置模型服务后重启后端。</p>}
        {activeJob && <button className="translation-active" onClick={() => choose(activeJob)}><LoaderCircle className="spin" size={14} />查看正在进行的任务</button>}
        <p className="translation-hint">译文自动保存。关闭窗口后任务继续；相同页面、语言、模型和术语表可复用已完成译文，新增翻译会调用模型。</p>
        <div className="translation-section-title translation-history-title"><strong>翻译记录</strong><button className="icon-button" aria-label="刷新翻译记录" onClick={() => setRefresh((n) => n + 1)}><RefreshCw size={15} /></button></div>
        <div className="translation-history">
          {jobs.length === 0 && <p className="translation-hint">还没有翻译记录。先选一页试读。</p>}
          {jobs.map((job) => <button key={job.id} className={selectedId === job.id ? "selected" : ""} onClick={() => choose(job)}>
            <span className="translation-job-name">{job.filename}</span>
            <span className="translation-job-meta">第 {job.start_page}—{job.end_page} 页 · {languageName[job.target_language]}<em className={job.status}>{labels[job.status]}</em></span>
          </button>)}
        </div>
      </aside>
      <main className="translation-reader">
        {error && <div className="error-box" role="alert">{error}<button className="text-button" onClick={() => setError("")}>收起</button></div>}
        {readError && <div className="error-box" role="alert">{readError}<button className="text-button" onClick={() => setRefresh((n) => n + 1)}>重新读取</button></div>}
        {selectedId && !detail ? <div className="translation-empty" role="status"><LoaderCircle className="spin" />正在读取翻译记录…</div> : <>
          <div className="translation-reader-title"><div><h3>{detail?.filename ?? paper?.filename ?? "从一篇论文开始"}</h3><p>{detail ? `${languageName[detail.target_language]} · ${labels[detail.status]} · 已翻译 ${detail.completed_pages}/${detail.total_pages} 页` : "开始前可预览起始页原文"}</p></div>
            {detail && <div className="translation-actions">
              {active(detail) ? <button className="secondary small" disabled={busyAction} onClick={() => void cancel()}><Square size={13} />停止后续翻译</button> : <button className="secondary small" disabled={busyAction || !detail.completed_pages} onClick={() => void download()}><Download size={14} />导出双语 TXT</button>}
            </div>}
          </div>
          {detail && <>
            <p className="translation-hint">本任务使用术语表版本 {detail.glossary_revision ?? 0}（{detail.glossary_count ?? 0} 个术语）{detail.glossary_changed ? "；当前术语表已更新，此记录保留原译文。" : ""}</p>
            <progress aria-label="翻译进度" max={detail.total_pages} value={detail.completed_pages + detail.skipped_pages} />
            <div className="translation-progress-text">已完成 {detail.completed_pages} 页 · 跳过无文本 {detail.skipped_pages} 页 · 复用 {detail.reused_pages} 页</div>
            {detail.error && <div className="translation-notice">{detail.error}{!active(detail) && detail.status !== "succeeded" && <button className="text-button" disabled={locked} onClick={() => newTask(detail)}>重新选择此范围</button>}</div>}
            <nav className="translation-pages" aria-label="译文页码">{detail.pages.map((p) => <button key={p.page} className={page?.page === p.page ? "selected" : ""} aria-pressed={page?.page === p.page} onClick={() => { setShownPage(p.page); setCopied(false); }}>
              第 {p.page} 页 {p.status === "completed" ? <Check size={12} /> : p.status === "running" && active(detail) ? <LoaderCircle size={12} className="spin" /> : p.status === "skipped" ? "· 无文本" : ""}
            </button>)}</nav>
          </>}
          <div className="translation-comparison">
            <section><div className="translation-pane-title"><span><FileText size={15} />原文 · 第 {page?.page ?? (start || 1)} 页</span></div>
              <div className="translation-text" lang="und">{page ? page.original || "本页没有可提取文本。" : previewLoading ? "正在读取页面文本…" : preview || "上传并选择一篇 PDF 论文，即可预览原文。"}</div>
            </section>
            <section><div className="translation-pane-title"><span><Languages size={15} />{languageName[detail?.target_language ?? language]}译文</span>{page?.status === "completed" && <button className="text-button" onClick={() => void copy()}><Copy size={13} />{copied ? "已复制" : "复制"}</button>}</div>
              {page?.status === "completed" ? <div className="translation-text" lang={detail?.target_language}>{page.translation}</div> : <div className="translation-empty">
                {active(detail) && page?.status !== "skipped" ? <><LoaderCircle className="spin" size={26} /><p>正在按页翻译，请稍候…</p></> : <><Languages size={32} /><p>{page?.status === "skipped" ? "本页没有可提取文本，已跳过。" : detail ? "本页尚未完成翻译。" : "选择页码范围，开始对照阅读"}</p></>}
                <small>{detail ? "仅完整完成的页面会保存为译文。" : "保留原文，支持中文与英文目标语言"}</small>
              </div>}
            </section>
          </div>
          <p className="translation-footnote">AI 翻译用于辅助阅读，请核对专业术语、公式与数字。多栏与表格可能影响文本顺序；本功能不含 OCR，不重建原 PDF 排版。</p>
        </>}
      </main>
    </div>
  </Modal>;
}
