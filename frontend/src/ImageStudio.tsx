import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, BookOpen, Check, Download, ImagePlus, LoaderCircle, RefreshCw, Sparkles, X } from "lucide-react";
import { ApiError, makeQuestion, RagApi } from "./api";
import type { ImageConfig, ImageDraft, ImageJob, ImageSubmission, Paper } from "./types";
import Modal from "./Modal";
import "./image-studio.css";

const labels = { queued: "排队中", running: "正在生成", succeeded: "已完成", failed: "生成失败" };
const pending = (job: ImageJob) => job.status === "queued" || job.status === "running";
const errorMessage = (e: unknown) => e instanceof Error ? e.message : "操作失败，请重试。";

function ImagePreview({ api, job, onUnauthorized }: { api: RagApi; job: ImageJob; onUnauthorized: () => void }) {
  const [url, setUrl] = useState("");
  const [extension, setExtension] = useState("png");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const unauthorized = useRef(onUnauthorized);
  unauthorized.current = onUnauthorized;
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    setUrl("");
    setError("");
    if (job.status !== "succeeded") return;
    void api.imageFile(job.id).then((blob) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob);
      setExtension(blob.type === "image/jpeg" ? "jpg" : "png");
      setUrl(objectUrl);
    }).catch((e: unknown) => {
      if (!active) return;
      if (e instanceof ApiError && e.status === 401) unauthorized.current();
      else setError(errorMessage(e));
    });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [api, job.id, job.status, retry]);
  if (pending(job)) return <div className="image-empty" role="status">
    <LoaderCircle className="spin" size={32} /><h3>{labels[job.status]}</h3>
    <p>Seedream 正在处理这张图片。关闭窗口后，任务仍会继续。</p>
    <small>完成后可从“AI 配图”查看结果。</small>
  </div>;
  if (job.status === "failed") return <div className="image-empty image-failed" role="alert">
    <ImagePlus size={32} /><h3>这次没有生成成功</h3><p>{job.error}</p>
    <small>系统不会自动重新调用模型。再次生成会创建新任务。</small>
  </div>;
  if (error) return <div className="image-empty" role="alert"><p>{error}</p>
    <button className="secondary" onClick={() => setRetry((x) => x + 1)}><RefreshCw size={15} />重新读取图片</button></div>;
  if (!url) return <div className="image-empty" role="status"><LoaderCircle className="spin" size={28} /><p>正在读取已保存的图片…</p></div>;
  return <div className="image-result">
    <img src={url} alt={`AI 概念示意图：${job.prompt.slice(0, 100)}`} onError={() => setError("图片无法显示，请重新读取或检查后端保存的文件。")} />
    <a className="primary image-download" href={url} download={`papertrail-${job.id}.${extension}`}><Download size={16} />下载图片</a>
    <p>AI 概念示意图 · 文字、结构与学术含义请结合论文核对</p>
  </div>;
}

export default function ImageStudio({ api: sessionApi, onClose, onUnauthorized, initialInstruction = "", initialDocumentIds = [] }: {
  api: RagApi; onClose: () => void; onUnauthorized: () => void;
  initialInstruction?: string; initialDocumentIds?: string[];
}) {
  // Own request lifetime: closing this dialog never cancels a paper upload or chat.
  const api = useMemo(() => new RagApi(sessionApi.token, sessionApi.base), [sessionApi]);
  const alive = useRef(true);
  const unauthorized = useRef(onUnauthorized);
  unauthorized.current = onUnauthorized;
  const [config, setConfig] = useState<ImageConfig | null>(null);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [jobs, setJobs] = useState<ImageJob[]>([]);
  const [selectedJob, setSelectedJob] = useState("");
  const [mode, setMode] = useState<"direct" | "paper">(initialInstruction ? "paper" : "direct");
  const [instruction, setInstruction] = useState(initialInstruction);
  const [selected, setSelected] = useState(initialDocumentIds);
  const [scope, setScope] = useState<"all" | "selected">(initialDocumentIds.length ? "selected" : "all");
  const [prompt, setPrompt] = useState(sessionApi.pendingImageSubmission?.prompt ?? "");
  const [draft, setDraft] = useState<ImageDraft | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [sending, setSending] = useState(false);
  const [uncertain, setUncertain] = useState(!!sessionApi.pendingImageSubmission);
  const attempt = useRef<ImageSubmission | null>(sessionApi.pendingImageSubmission);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const activeJob = jobs.find(pending);
  const shownJob = jobs.find((job) => job.id === selectedJob) ?? jobs[0];
  const locked = drafting || sending || uncertain;

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; api.dispose(); };
  }, [api]);

  function report(e: unknown) {
    if (!alive.current) return;
    if (e instanceof ApiError && e.status === 401) unauthorized.current();
    else setError(errorMessage(e));
  }

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    async function refresh(first: boolean) {
      try {
        if (first) {
          const [cfg, list] = await Promise.all([api.imageConfig(), api.documents()]);
          if (!active) return;
          setConfig(cfg); setPapers(list.documents);
        }
        const result = await api.images();
        if (!active) return;
        setJobs(result.images);
        const recovered = result.images.find((job) => job.request_id === attempt.current?.request_id);
        if (recovered) {
          attempt.current = null; sessionApi.pendingImageSubmission = null;
          setUncertain(false); setSelectedJob(recovered.id);
        }
        setPollError(""); failures = 0;
        // While open, refresh also discovers jobs submitted from another tab.
        timer = setTimeout(() => void refresh(false), result.images.some(pending) ? 2000 : 10000);
      } catch (e) {
        if (!active) return;
        if (e instanceof ApiError && e.status === 401) { unauthorized.current(); return; }
        failures++;
        setPollError(`${errorMessage(e)}${failures >= 3 ? " 自动刷新已暂停，请点击刷新。" : ""}`);
        if (failures < 3) timer = setTimeout(() => void refresh(first), 3000);
      } finally { if (active) setLoading(false); }
    }
    setLoading(true);
    void refresh(true);
    return () => { active = false; clearTimeout(timer); };
  }, [api, sessionApi, refreshKey]);

  async function createDraft() {
    setError("");
    let question;
    try { question = makeQuestion(instruction, 5, scope, selected); }
    catch (e) { report(e); return; }
    setDrafting(true);
    try {
      const result = await api.imagePrompt(question);
      if (!alive.current) return;
      setDraft(result); setPrompt(result.prompt);
    } catch (e) { report(e); }
    finally { if (alive.current) setDrafting(false); }
  }

  async function generate() {
    if (sending) return;
    if (!attempt.current) attempt.current = {
      request_id: crypto.randomUUID(), prompt: prompt.trim(), draft_id: draft?.draft_id ?? null,
    };
    sessionApi.pendingImageSubmission = attempt.current;
    const submitted = attempt.current;
    setSending(true); setError("");
    try {
      const job = await api.generateImage(submitted);
      if (!alive.current) return;
      attempt.current = null; sessionApi.pendingImageSubmission = null;
      setUncertain(false); setSelectedJob(job.id);
      setJobs((items) => [job, ...items.filter((item) => item.id !== job.id)].slice(0, 30));
      setRefreshKey((x) => x + 1);
    } catch (e) {
      if (!alive.current) return;
      // Polling may already have confirmed this job while the POST response was lost.
      if (attempt.current?.request_id !== submitted.request_id) return;
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        attempt.current = null; sessionApi.pendingImageSubmission = null; setUncertain(false);
      } else {
        // Reuse this exact request ID and body until its outcome is known.
        setUncertain(true);
      }
      report(e);
    } finally { if (alive.current) setSending(false); }
  }

  return <Modal className="image-studio" labelId="image-studio-title" onClose={onClose} busy={sending}>
    <header className="image-header">
      <div className="image-title"><span className="image-title-icon"><ImagePlus size={23} /></span><div>
        <h2 id="image-studio-title">AI 配图工作室</h2><p>让论文中的想法，成为看得见的画面</p>
      </div></div>
      <button className="icon-button" aria-label="关闭配图窗口" onClick={onClose} disabled={sending}><X size={20} /></button>
    </header>
    <div className="image-model-flow"><span><Sparkles size={14} />DeepSeek · 理解论文</span><ArrowRight size={15} /><span><ImagePlus size={14} />Seedream · 生成配图</span><small>单张 · 2K</small></div>
    <div className="image-columns">
      <section className="image-editor" aria-label="配图设置">
        <div className="image-tabs" role="group" aria-label="绘图描述来源">
          <button aria-pressed={mode === "direct"} disabled={locked} onClick={() => { setMode("direct"); setDraft(null); }}>直接描述</button>
          <button aria-pressed={mode === "paper"} disabled={locked} onClick={() => setMode("paper")}><BookOpen size={15} />根据论文</button>
        </div>
        {mode === "paper" && <div className="image-paper-form">
          <label htmlFor="image-instruction">1. 想说明论文中的哪个概念？</label>
          <textarea id="image-instruction" rows={3} maxLength={2000} disabled={locked} value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="例如：为这篇论文的研究思路制作一张概念示意配图" />
          <label className="image-scope"><input type="checkbox" checked={scope === "all"} disabled={locked} onChange={(e) => setScope(e.target.checked ? "all" : "selected")} />使用全部论文</label>
          {scope === "selected" && <div className="image-paper-list" aria-label="选择参考论文">
            {papers.map((paper) => <label key={paper.document_id}><input type="checkbox" disabled={locked} checked={selected.includes(paper.document_id)} onChange={(e) => setSelected((items) => e.target.checked ? [...items, paper.document_id] : items.filter((id) => id !== paper.document_id))} /><span>{paper.filename}</span></label>)}
            {!papers.length && <p>请先在文献工作台上传论文。</p>}
          </div>}
          <button className="secondary image-draft-button" disabled={locked || !papers.length || loading} onClick={() => void createDraft()}>
            {drafting ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}{drafting ? "DeepSeek 正在整理…" : "用 DeepSeek 整理绘图描述"}
          </button>
          <p className="image-help">仅生成文字草稿；确认下方描述后，再调用 Seedream。</p>
        </div>}
        <label htmlFor="image-prompt">{mode === "paper" ? "2. 检查并修改绘图描述" : "写下你的绘图描述"}</label>
        <textarea id="image-prompt" className="image-prompt" rows={mode === "paper" ? 6 : 10} maxLength={6000} disabled={locked} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="例如：为学术报告绘制一张简洁的科学概念插画，白色背景，蓝紫色配色，主体清晰，留出标题空间…" />
        <div className="image-prompt-meta"><span>简短标签更易辨认</span><span>{prompt.length} / 6000</span></div>
        {draft && <details className="image-evidence"><summary><Check size={14} />草稿参考了 {draft.evidence.length} 段论文原文</summary>
          {draft.evidence.map((item) => <div key={item.chunk_id}><strong>{item.filename} · 第 {item.page} 页</strong><p>{item.quote}</p></div>)}
          <p>这些片段用于整理草稿，不代表模型生成的图片已经通过学术核验。</p>
        </details>}
        {config && !config.enabled && <div className="image-notice">配图服务尚未配置。请在后端 .env 填写 SEEDREAM_API_KEY 等配置并重启后端。</div>}
        {error && <div className="error-box" role="alert">{error}</div>}
        {uncertain && <div className="image-notice" role="status">提交结果尚未确认。请点击“确认提交状态”，将使用同一任务编号查询或提交，不会重复创建该任务。</div>}
        <button className="primary image-generate" disabled={sending || drafting || (!uncertain && (!config?.enabled || !prompt.trim() || !!activeJob || loading))} onClick={() => void generate()}>
          {sending ? <LoaderCircle className="spin" size={17} /> : <ImagePlus size={17} />}
          {sending ? "正在提交…" : uncertain ? "确认提交状态" : activeJob ? "请等待当前图片完成" : "用 Seedream 生成一张配图"}
        </button>
        <p className="image-help">点击生成将调用模型，使用平台额度或产生费用。支持概念配图；精确数据图请使用绘图工具。</p>
      </section>
      <section className="image-output" aria-label="生成结果">
        <div className="image-output-heading"><h3>画面预览</h3><button className="text-button" onClick={() => setRefreshKey((x) => x + 1)} disabled={loading}><RefreshCw size={14} />刷新</button></div>
        {pollError && <div className="error-box" role="alert">{pollError}</div>}
        <div className="image-canvas">
          {shownJob ? <ImagePreview key={shownJob.id} api={api} job={shownJob} onUnauthorized={onUnauthorized} /> : <div className="image-empty">
            {loading ? <LoaderCircle className="spin" size={32} /> : <ImagePlus size={40} />}<h3>{loading ? "正在读取配图记录…" : "把一个想法，变成一张配图"}</h3><p>直接描述画面，或让 DeepSeek 从论文出发整理草稿。</p>
            <div className="image-example">论文概念 · 报告插画 · 研究主题配图</div>
          </div>}
        </div>
        {shownJob && <details className="image-job-detail"><summary>查看这张图的描述与来源</summary><p>{shownJob.prompt}</p>
          <small>{shownJob.model} · {new Date(shownJob.created_at).toLocaleString()}</small>
          {shownJob.evidence.map((item) => <p key={item.chunk_id}><strong>{item.filename} · 第 {item.page} 页</strong><br />{item.quote}</p>)}
        </details>}
        <div className="image-history-heading"><h3>最近生成</h3><span>仅当前用户 · 最近 30 条</span></div>
        <div className="image-history">
          {jobs.map((job) => <button key={job.id} className={shownJob?.id === job.id ? "selected" : ""} onClick={() => setSelectedJob(job.id)} aria-pressed={shownJob?.id === job.id}>
            <span className={`image-status ${job.status}`}>{pending(job) && <LoaderCircle className="spin" size={12} />}{labels[job.status]}</span><span className="image-history-prompt">{job.prompt}</span><small>{new Date(job.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small>
          </button>)}
          {!jobs.length && <p className="image-no-history">生成的图片会保存在后端，重新打开这里即可查看。</p>}
        </div>
      </section>
    </div>
  </Modal>;
}
