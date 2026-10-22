import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  CircleHelp,
  FileText,
  FolderOpen,
  KeyRound,
  Library,
  LoaderCircle,
  LogOut,
  Plus,
  Quote,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  X,
  Copy,
  AlertCircle,
  CheckCircle2,
  SlidersHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { ApiError, emptyPages, makeQuestion, RagApi } from "./api";
import type { Answer, Evidence, Paper, Turn } from "./types";
import type { PdfTarget } from "./PdfViewer";
import AnswerText from "./AnswerText";
import Modal from "./Modal";

const PdfViewer = lazy(() => import("./PdfViewer"));
let nextUiId = 0;
// These IDs identify page-local rows only; document UUIDs come from the backend.
const uiId = () => `ui-${++nextUiId}`;
const prompts = [
  {
    icon: BookOpen,
    title: "快速读懂一篇论文",
    text: "概括论文的研究问题、采用的方法和主要结论。",
    hint: "研究问题 · 方法 · 结论",
  },
  {
    icon: SlidersHorizontal,
    title: "比较不同研究方法",
    text: "比较这些论文采用的研究方法，说明各自的优势与局限。",
    hint: "多篇对照 · 发现差异",
  },
  {
    icon: Quote,
    title: "找到结论的原文依据",
    text: "论文的主要结论有哪些？请给出对应的原文依据。",
    hint: "追溯证据 · 核对引用",
  },
];

type UploadItem = {
  id: string;
  name: string;
  status: "waiting" | "working" | "done" | "error";
  detail: string;
};
type Session = { api: RagApi; papers: Paper[]; version: string; id: number };

function Logo() {
  return (
    <div className="brand">
      <div className="brand-icon">
        <BookOpen size={23} />
      </div>
      <div>
        <strong>
          研迹 <span>PaperTrail</span>
        </strong>
        <small>学术文献助手</small>
      </div>
    </div>
  );
}
function messageOf(e: unknown) {
  return e instanceof Error ? e.message : "操作失败，请重试。";
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [showConnect, setShowConnect] = useState(false);
  const [token, setToken] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");
  const candidate = useRef<RagApi | null>(null);
  useEffect(() => () => candidate.current?.dispose(), []);
  async function connect(e: FormEvent) {
    e.preventDefault();
    if (token.trim().length < 16) {
      setError("请输入 .env 中至少 16 位的项目访问令牌。");
      return;
    }
    setConnecting(true);
    setError("");
    const api = new RagApi(token.trim());
    candidate.current = api;
    try {
      const [list, health] = await Promise.all([api.documents(), api.health()]);
      setSession({
        api,
        papers: list.documents,
        version: health.version,
        id: Date.now(),
      });
      candidate.current = null;
      setToken("");
      setShowConnect(false);
    } catch (e) {
      api.dispose();
      setError(messageOf(e));
    } finally {
      setConnecting(false);
    }
  }
  function logout(reason = "") {
    session?.api.dispose();
    setSession(null);
    setToken("");
    setError(reason);
    setShowConnect(Boolean(reason));
  }

  return (
    <>
      <header className="topbar">
        <Logo />
        <div className="top-nav">
          <span className="nav-active">
            <Library size={16} />
            文献工作台
          </span>
          <span className="nav-caption">从问题出发，回到原文</span>
        </div>
        <div className="connection-area">
          {session ? (
            <>
              <span className="connection-status">
                <i />
                已连接知识库
              </span>
              <button
                className="icon-button"
                title="断开连接并清空本页记录"
                aria-label="断开连接"
                onClick={() => logout()}
              >
                <LogOut size={18} />
              </button>
            </>
          ) : (
            <button
              className="primary small"
              onClick={() => {
                setError("");
                setShowConnect(true);
              }}
            >
              <KeyRound size={15} />
              连接知识库
            </button>
          )}
        </div>
      </header>
      <Workspace
        key={session?.id ?? "disconnected"}
        session={session}
        onConnect={() => setShowConnect(true)}
        onUnauthorized={() => logout("访问令牌已失效，请重新连接。")}
      />
      {showConnect && (
        <Modal
          className="connect-card"
          labelId="connect-title"
          busy={connecting}
          onClose={() => setShowConnect(false)}
        >
          <button
            className="icon-button modal-close"
            aria-label="关闭连接窗口"
            disabled={connecting}
            onClick={() => setShowConnect(false)}
          >
            <X size={20} />
          </button>
          <div className="connect-icon">
            <KeyRound size={25} />
          </div>
          <h2 id="connect-title">连接你的知识库</h2>
          <p>连接本地后端，开始阅读与提问。</p>
          <form onSubmit={connect}>
            <label htmlFor="access-token">项目访问令牌</label>
            <input
              autoFocus
              id="access-token"
              type="password"
              autoComplete="off"
              placeholder="粘贴 API_TOKENS_JSON 中的令牌"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              disabled={connecting}
            />
            <p className="field-help">
              打开项目根目录的 <code>.env</code>，复制{" "}
              <code>API_TOKENS_JSON</code>{" "}
              中冒号左侧的随机字符串，不带引号。这不是模型平台 API 密钥。
            </p>
            {error && (
              <div className="error-box" role="alert">
                {error}
              </div>
            )}
            <button className="primary connect-submit" disabled={connecting}>
              {connecting ? (
                <LoaderCircle className="spin" size={17} />
              ) : (
                <ArrowRight size={17} />
              )}
              {connecting ? "正在连接…" : "连接并载入文献"}
            </button>
          </form>
          <div className="privacy-note">
            <ShieldCheck size={15} />
            令牌仅保存在当前页面内存，刷新后需重新输入。
          </div>
        </Modal>
      )}
    </>
  );
}

export function Workspace({
  session,
  onConnect,
  onUnauthorized,
}: {
  session: Session | null;
  onConnect: () => void;
  onUnauthorized: () => void;
}) {
  const api = session?.api;
  const [papers, setPapers] = useState<Paper[]>(session?.papers ?? []);
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState<"all" | "selected">("all");
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(5);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [activeTurn, setActiveTurn] = useState("");
  const [activeEvidence, setActiveEvidence] = useState<number | null>(null);
  const [asking, setAsking] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [queue, setQueue] = useState<UploadItem[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [removeTarget, setRemoveTarget] = useState<Paper | null>(null);
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null);
  const [drag, setDrag] = useState(false);
  const [sideOpen, setSideOpen] = useState(false);
  const [copied, setCopied] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const textInput = useRef<HTMLTextAreaElement>(null);
  const alive = useRef(true);
  const busy = asking || uploading || deleting;
  const view = turns.find((t) => t.id === activeTurn)?.answer;
  const citations = view?.citations ?? [];
  const shownPapers = papers.filter((p) =>
    p.filename.toLowerCase().includes(search.toLowerCase()),
  );

  useEffect(
    () => () => {
      alive.current = false;
      api?.dispose();
    },
    [api],
  );
  useEffect(() => {
    if (turns.length)
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, asking]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(t);
  }, [notice]);
  function report(e: unknown) {
    if (!alive.current) return;
    if (e instanceof ApiError && e.status === 401) {
      onUnauthorized();
      return;
    }
    setError(messageOf(e));
  }
  function replacePapers(items: Paper[]) {
    setPapers(items);
    setSelected((s) =>
      s.filter((id) => items.some((p) => p.document_id === id)),
    );
  }
  async function refresh() {
    if (!api) {
      onConnect();
      return;
    }
    setRefreshing(true);
    setError("");
    try {
      const result = await api.documents();
      if (alive.current) replacePapers(result.documents);
    } catch (e) {
      report(e);
    } finally {
      if (alive.current) setRefreshing(false);
    }
  }
  async function upload(files: File[]) {
    if (!api) {
      onConnect();
      return;
    }
    if (busy || !files.length) return;
    setError("");
    setUploading(true);
    const items: UploadItem[] = files.map((f) => ({
      id: uiId(),
      name: f.name,
      status: "waiting",
      detail: "等待上传",
    }));
    setQueue(items);
    const change = (
      id: string,
      status: UploadItem["status"],
      detail: string,
    ) => {
      if (alive.current)
        setQueue((old) =>
          old.map((x) => (x.id === id ? { ...x, status, detail } : x)),
        );
    };
    try {
      for (let i = 0; i < files.length && alive.current; i++) {
        const file = files[i];
        const id = items[i].id;
        if (!file.name.toLowerCase().endsWith(".pdf")) {
          change(id, "error", "仅支持 PDF 文件");
          continue;
        }
        if (file.size > 30 * 1024 * 1024) {
          change(id, "error", "超过 30 MiB 限制");
          continue;
        }
        if (!file.size) {
          change(id, "error", "文件为空");
          continue;
        }
        change(id, "working", "正在上传、解析与建立索引…");
        try {
          const paper = await api.upload(file);
          if (!alive.current) break;
          setPapers((old) => [
            paper,
            ...old.filter((p) => p.document_id !== paper.document_id),
          ]);
          const empty = emptyPages(paper);
          change(
            id,
            "done",
            `${paper.duplicate ? "已存在，未重复入库" : `已入库 · ${paper.chunk_count} 个片段`}${empty.length ? `；${empty.length} 页无文本，尚未 OCR` : ""}`,
          );
        } catch (e) {
          change(id, "error", messageOf(e));
          if (e instanceof ApiError && e.status === 401) {
            onUnauthorized();
            break;
          }
        }
      }
    } finally {
      if (alive.current) setUploading(false);
    }
  }
  async function remove() {
    if (!api || !removeTarget) return;
    setDeleting(true);
    setError("");
    try {
      await api.delete(removeTarget.document_id);
      if (!alive.current) return;
      replacePapers(
        papers.filter((p) => p.document_id !== removeTarget.document_id),
      );
      setNotice("论文及其索引已删除。");
      setRemoveTarget(null);
    } catch (e) {
      report(e);
    } finally {
      if (alive.current) setDeleting(false);
    }
  }
  async function ask(e: FormEvent) {
    e.preventDefault();
    if (!api) {
      onConnect();
      return;
    }
    if (busy) return;
    setError("");
    if (!papers.length) {
      setError("先上传一篇论文，再开始提问。");
      return;
    }
    let body;
    try {
      body = makeQuestion(question, topK, scope, selected);
    } catch (e) {
      report(e);
      return;
    }
    const id = uiId();
    setTurns((old) => [
      ...old,
      {
        id,
        question: body.question,
        scope:
          scope === "all"
            ? `全部论文 · ${papers.length} 篇`
            : `指定论文 · ${selected.length} 篇`,
      },
    ]);
    setQuestion("");
    setAsking(true);
    setActiveTurn(id);
    setActiveEvidence(null);
    try {
      const answer = await api.chat(body);
      if (alive.current)
        setTurns((old) => old.map((t) => (t.id === id ? { ...t, answer } : t)));
    } catch (e) {
      if (alive.current)
        setTurns((old) =>
          old.map((t) => (t.id === id ? { ...t, error: messageOf(e) } : t)),
        );
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
    } finally {
      if (alive.current) setAsking(false);
    }
  }
  function showCitation(turnId: string, evidence: Evidence) {
    setActiveTurn(turnId);
    setActiveEvidence(evidence.id);
    setTimeout(
      () =>
        document
          .getElementById(`source-${turnId}-${evidence.id}`)
          ?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
      0,
    );
  }
  function openPdf(e: Evidence | Paper) {
    setPdfTarget({
      id: e.document_id,
      filename: e.filename,
      page: "page" in e ? e.page : 1,
    });
  }
  async function copyAnswer(id: string, answer: Answer) {
    const refs = answer.citations
      .map((c) => `[${c.id}] ${c.filename}，PDF 第 ${c.page} 页\n${c.quote}`)
      .join("\n\n");
    try {
      await navigator.clipboard.writeText(`${answer.answer}\n\n${refs}`);
      setCopied(id);
      setTimeout(() => setCopied(""), 2000);
    } catch {
      setError("复制失败，请手动选中答案复制。");
    }
  }
  function pickPrompt(text: string) {
    setQuestion(text);
    textInput.current?.focus();
  }

  return (
    <div className="workspace">
      {sideOpen && (
        <button
          className="sidebar-scrim"
          aria-label="收起文献列表"
          onClick={() => setSideOpen(false)}
        />
      )}
      <aside className={`library-panel ${sideOpen ? "mobile-open" : ""}`}>
        <div className="section-heading">
          <h2>
            <Library size={18} />
            我的文献
          </h2>
          <span className="count-badge">{papers.length}</span>
          <button
            className="icon-button refresh-button"
            aria-label="刷新文献列表"
            disabled={refreshing || busy}
            onClick={refresh}
          >
            <RefreshCw size={16} className={refreshing ? "spin" : ""} />
          </button>
          <button
            className="icon-button mobile-only"
            aria-label="收起文献列表"
            onClick={() => setSideOpen(false)}
          >
            <PanelLeftClose size={17} />
          </button>
        </div>
        <div
          className={`upload-drop ${drag ? "dragging" : ""} ${uploading ? "uploading" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            if (!busy) setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            void upload(Array.from(e.dataTransfer.files));
          }}
        >
          <button
            className="upload-button"
            disabled={busy}
            onClick={() => (api ? fileInput.current?.click() : onConnect())}
          >
            {uploading ? (
              <LoaderCircle size={21} className="spin" />
            ) : (
              <Upload size={21} />
            )}
            <strong>{uploading ? "正在处理论文" : "上传论文"}</strong>
            <span>拖入或选择 PDF，支持多篇</span>
            <small>单篇不超过 30 MiB</small>
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,application/pdf"
            multiple
            hidden
            aria-label="选择 PDF 文件"
            onChange={(e) => {
              void upload(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
        </div>
        {queue.length > 0 && (
          <div className="upload-queue" aria-live="polite">
            <div className="queue-title">
              <span>上传记录</span>
              {!uploading && (
                <button
                  className="icon-button"
                  aria-label="收起上传记录"
                  onClick={() => setQueue([])}
                >
                  <X size={14} />
                </button>
              )}
            </div>
            {queue.map((q) => (
              <div className={`queue-item ${q.status}`} key={q.id}>
                {q.status === "working" ? (
                  <LoaderCircle className="spin" size={14} />
                ) : q.status === "done" ? (
                  <CheckCircle2 size={14} />
                ) : q.status === "error" ? (
                  <AlertCircle size={14} />
                ) : (
                  <FileText size={14} />
                )}
                <div>
                  <strong title={q.name}>{q.name}</strong>
                  <span>{q.detail}</span>
                </div>
              </div>
            ))}
          </div>
        )}
        <label className="library-search">
          <Search size={16} />
          <input
            placeholder="搜索文献名称"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="搜索文献名称"
          />
        </label>
        <div className="scope-label">问答范围</div>
        <div className="segmented">
          <button
            className={scope === "all" ? "selected" : ""}
            disabled={busy}
            onClick={() => setScope("all")}
          >
            全部论文
          </button>
          <button
            className={scope === "selected" ? "selected" : ""}
            disabled={busy}
            onClick={() => setScope("selected")}
          >
            指定论文{selected.length ? ` ${selected.length}` : ""}
          </button>
        </div>
        {scope === "selected" && (
          <div className="selection-tools">
            <span>已选 {selected.length} 篇</span>
            <button
              disabled={busy}
              onClick={() =>
                setSelected(papers.slice(0, 100).map((p) => p.document_id))
              }
            >
              全选{papers.length > 100 ? "前 100 篇" : ""}
            </button>
            <button disabled={busy} onClick={() => setSelected([])}>
              清空
            </button>
          </div>
        )}
        <div className="paper-list">
          {shownPapers.map((p) => (
            <article
              className={`paper ${scope === "selected" && selected.includes(p.document_id) ? "paper-selected" : ""}`}
              key={p.document_id}
            >
              <div className="paper-top">
                {scope === "selected" ? (
                  <input
                    type="checkbox"
                    aria-label={`选择 ${p.filename}`}
                    checked={selected.includes(p.document_id)}
                    disabled={busy}
                    onChange={(e) =>
                      setSelected((s) =>
                        e.target.checked
                          ? [...s, p.document_id]
                          : s.filter((id) => id !== p.document_id),
                      )
                    }
                  />
                ) : (
                  <div className="pdf-icon">
                    <FileText size={17} />
                  </div>
                )}
                <button
                  className="paper-name"
                  title={p.filename}
                  onClick={() => openPdf(p)}
                >
                  {p.filename.replace(/\.pdf$/i, "")}
                </button>
              </div>
              <div className="paper-meta">
                <span>{p.pages} 页</span>
                <span>·</span>
                <span>{p.chunk_count} 个片段</span>
                <button
                  className="icon-button delete-paper"
                  aria-label={`删除 ${p.filename}`}
                  disabled={busy}
                  onClick={() => setRemoveTarget(p)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
              {emptyPages(p).length > 0 && (
                <div
                  className="ocr-note"
                  title={`第 ${emptyPages(p).join("、")} 页尚未进行 OCR`}
                >
                  {emptyPages(p).length} 页无文本，尚未 OCR
                </div>
              )}
            </article>
          ))}
          {!shownPapers.length && (
            <div className="library-empty">
              <FolderOpen size={30} />
              <strong>
                {search ? "未找到匹配的文献" : "你的阅读，从这里开始"}
              </strong>
              <p>
                {search
                  ? "试试更短的文件名关键词"
                  : session
                    ? "上传论文后，文献会保存在知识库中"
                    : "连接知识库后查看或上传论文"}
              </p>
            </div>
          )}
        </div>
        <div className="library-footer">
          <ShieldCheck size={16} />
          <div>
            个人知识库<span>论文与索引保存在本地后端</span>
          </div>
        </div>
      </aside>

      <main className="chat-panel">
        <div className="chat-header">
          <div>
            <button
              className="icon-button mobile-only"
              aria-label="展开文献列表"
              onClick={() => setSideOpen(true)}
            >
              <PanelLeftOpen size={18} />
            </button>
            <h1>文献问答</h1>
            <span className="subtle-pill">
              <span className="tiny-dot" />
              基于原文回答
            </span>
          </div>
          <button
            className="text-button"
            disabled={busy || !turns.length}
            onClick={() => {
              setTurns([]);
              setActiveTurn("");
              setActiveEvidence(null);
              setQuestion("");
            }}
          >
            <Plus size={16} />
            新问答
          </button>
        </div>
        <div className="conversation" aria-live="polite">
          {!turns.length && (
            <div className="welcome">
              <div className="eyebrow">
                <span /> YOUR RESEARCH COMPANION
              </div>
              <div className="welcome-symbol">
                <BookOpen size={38} strokeWidth={1.5} />
                <Sparkles size={18} />
              </div>
              <h2>
                让每一次提问，
                <br />
                都有文献可循。
              </h2>
              <p className="welcome-description">
                从一篇论文到一个研究方向，
                <br className="small-break" />
                一起梳理观点、比较方法，追溯原文依据。
              </p>
              <div className="steps">
                <span>
                  <b>1</b>上传文献
                </span>
                <ChevronRight size={13} />
                <span>
                  <b>2</b>提出问题
                </span>
                <ChevronRight size={13} />
                <span>
                  <b>3</b>核对引用
                </span>
              </div>
              <div className="prompt-grid">
                {prompts.map(({ icon: Icon, title, text, hint }) => (
                  <button
                    className="prompt-card"
                    key={title}
                    onClick={() => pickPrompt(text)}
                  >
                    <span className="prompt-icon">
                      <Icon size={20} />
                    </span>
                    <strong>{title}</strong>
                    <span>{hint}</span>
                    <ArrowRight className="prompt-arrow" size={16} />
                  </button>
                ))}
              </div>
              {!session && (
                <button
                  className="text-button welcome-connect"
                  onClick={onConnect}
                >
                  连接知识库，开始你的研究 <ArrowRight size={15} />
                </button>
              )}
            </div>
          )}
          {turns.map((turn) => (
            <section className="turn" key={turn.id}>
              <div className="question-message">
                <div className="question-meta">
                  你<span>{turn.scope}</span>
                </div>
                <p>{turn.question}</p>
              </div>
              <div className="answer-message">
                <div className="answer-heading">
                  <span className="assistant-avatar">
                    <Sparkles size={17} />
                  </span>
                  <strong>研迹</strong>
                  <span>文献助手</span>
                  {turn.answer && (
                    <button
                      className="icon-button copy-button"
                      aria-label="复制回答与引用"
                      onClick={() => void copyAnswer(turn.id, turn.answer!)}
                    >
                      {copied === turn.id ? (
                        <Check size={16} />
                      ) : (
                        <Copy size={16} />
                      )}
                    </button>
                  )}
                </div>
                {turn.answer ? (
                  <>
                    <AnswerText
                      text={turn.answer.answer}
                      citations={turn.answer.citations}
                      onCitation={(c) => showCitation(turn.id, c)}
                    />
                    {turn.answer.citation_status === "missing_citations" && (
                      <div className="answer-warning">
                        <AlertCircle size={15} />
                        本次回答缺少引用编号，请结合检索片段核对。
                      </div>
                    )}
                    {turn.answer.citation_status === "no_evidence" && (
                      <div className="answer-warning">
                        当前没有可用于回答的证据。
                      </div>
                    )}
                    <div className="answer-footer">
                      <button
                        className="source-summary"
                        onClick={() => {
                          setActiveTurn(turn.id);
                          setActiveEvidence(null);
                          document
                            .getElementById("sources-heading")
                            ?.scrollIntoView({
                              behavior: "smooth",
                              block: "nearest",
                            });
                        }}
                      >
                        <Quote size={14} />
                        {turn.answer.citations.length} 条引用 ·{" "}
                        {
                          new Set(
                            turn.answer.citations.map((c) => c.document_id),
                          ).size
                        }{" "}
                        篇文献
                        <ChevronRight size={14} />
                      </button>
                      <span>请结合原文核对结论</span>
                    </div>
                  </>
                ) : turn.error ? (
                  <div className="error-box" role="alert">
                    {turn.error}
                    <button
                      className="text-button"
                      onClick={() => pickPrompt(turn.question)}
                    >
                      将问题放回输入框
                    </button>
                  </div>
                ) : (
                  <div className="thinking" role="status">
                    <LoaderCircle className="spin" size={17} />
                    <span>
                      正在检索文献并生成回答…
                      <small>首次使用需要加载模型，请稍候</small>
                    </span>
                  </div>
                )}
              </div>
            </section>
          ))}
          <div ref={end} />
        </div>
        <div className="composer-area">
          {error && (
            <div className="error-box dismissible" role="alert">
              <AlertCircle size={16} />
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="关闭错误提示"
                onClick={() => setError("")}
              >
                <X size={15} />
              </button>
            </div>
          )}
          {notice && (
            <div className="success-note" role="status">
              <CheckCircle2 size={15} />
              {notice}
            </div>
          )}
          <form className="composer" onSubmit={ask}>
            <div className="composer-context">
              <span>
                <Library size={13} />
                {scope === "all"
                  ? `全部论文 · ${papers.length} 篇`
                  : `已选 ${selected.length} 篇论文`}
              </span>
              <label>
                检索片段{" "}
                <select
                  aria-label="检索片段数量"
                  value={topK}
                  disabled={busy}
                  onChange={(e) => setTopK(Number(e.target.value))}
                >
                  {[3, 5, 8, 10].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <textarea
              ref={textInput}
              value={question}
              maxLength={2000}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="向你的文献提问，例如：这些研究有哪些共同结论？"
              aria-label="输入文献问题"
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  if (!busy) e.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <div className="composer-bottom">
              <span>
                <span className="desktop-only">
                  Enter 发送 · Shift + Enter 换行
                </span>
                <span className="char-count">{question.length}/2000</span>
              </span>
              <button
                className="send-button"
                disabled={busy || !question.trim()}
                type="submit"
              >
                {asking ? (
                  <LoaderCircle className="spin" size={17} />
                ) : (
                  <Send size={17} />
                )}
                <span>{asking ? "生成中" : "发送"}</span>
              </button>
            </div>
          </form>
          <div className="bottom-note">
            <ShieldCheck size={12} />
            回答基于检索到的文献片段，每条问题独立检索，不自动继承上一轮上下文。
          </div>
        </div>
      </main>

      <aside className="sources-panel">
        <div className="section-heading" id="sources-heading">
          <h2>
            <Quote size={18} />
            原文依据
          </h2>
          <span className="count-badge">{citations.length}</span>
        </div>
        <div className="sources-intro">从回答中的引用，回到论文原文。</div>
        {view ? (
          <div className="source-list">
            {citations.map((c) => (
              <article
                id={`source-${activeTurn}-${c.id}`}
                className={`source-card ${activeEvidence === c.id ? "source-active" : ""}`}
                key={`${c.id}-${c.chunk_id}`}
              >
                <div className="source-heading">
                  <span className="source-number">{c.id}</span>
                  <strong title={c.filename}>{c.filename}</strong>
                </div>
                <div className="source-page">PDF 第 {c.page} 页</div>
                <blockquote>{c.quote}</blockquote>
                <button
                  className="text-button source-open"
                  onClick={() => openPdf(c)}
                >
                  查看原文 <ArrowRight size={14} />
                </button>
              </article>
            ))}
            {!citations.length && (
              <div className="source-empty">
                <Quote size={30} />
                <h3>这次回答没有引用</h3>
                <p>
                  {view.citation_status === "no_evidence"
                    ? "请先上传论文或调整检索范围。"
                    : "可以展开下方检索片段，检查已有证据。"}
                </p>
              </div>
            )}
            {view.evidence.length > 0 && (
              <details className="retrieval-details">
                <summary>全部检索片段（{view.evidence.length}）</summary>
                <p>这些片段供模型参考，不一定都被答案引用。</p>
                {view.evidence.map((e) => (
                  <div className="retrieval-item" key={e.chunk_id}>
                    <strong>
                      [{e.id}] {e.filename} · 第 {e.page} 页
                    </strong>
                    <p>{e.quote}</p>
                    <button className="text-button" onClick={() => openPdf(e)}>
                      打开这一页 <ArrowRight size={13} />
                    </button>
                  </div>
                ))}
              </details>
            )}
          </div>
        ) : (
          <div className="source-empty">
            <div className="source-illustration">
              <div className="sheet-back" />
              <div className="sheet-front">
                <Quote size={23} />
                <i />
                <i />
                <i />
                <span>[1]</span>
              </div>
            </div>
            <h3>让结论有据可查</h3>
            <p>
              提问后，这里会展示引用的
              <br />
              论文名称、页码和原文片段。
            </p>
            <div className="example-citation">
              <span>[1]</span>
              <p>
                点击回答中的引用编号
                <br />
                <small>定位证据，再打开对应原文</small>
              </p>
            </div>
          </div>
        )}
        <div className="evidence-note">
          <CircleHelp size={15} />
          <p>引用编号可追溯不等于结论已验证。重要信息请与原文交叉核对。</p>
        </div>
      </aside>
      {removeTarget && (
        <Modal
          className="confirm-card"
          labelId="delete-title"
          busy={deleting}
          onClose={() => setRemoveTarget(null)}
        >
          <div className="delete-icon">
            <Trash2 size={24} />
          </div>
          <h2 id="delete-title">删除这篇论文？</h2>
          <p>{removeTarget.filename}</p>
          <p className="field-help">
            原文件和检索索引会一起删除。已有回答中的引用记录保留，但对应原文将无法打开。
          </p>
          <div className="confirm-actions">
            <button
              className="secondary"
              autoFocus
              disabled={deleting}
              onClick={() => setRemoveTarget(null)}
            >
              取消
            </button>
            <button
              className="danger"
              disabled={deleting}
              onClick={() => void remove()}
            >
              {deleting ? "正在删除…" : "确认删除"}
            </button>
          </div>
        </Modal>
      )}
      {pdfTarget && api && (
        <Suspense
          fallback={
            <div className="modal-backdrop">
              <div className="pdf-loading">
                <LoaderCircle className="spin" />
                正在加载 PDF 阅读器…
              </div>
            </div>
          }
        >
          <PdfViewer
            key={`${pdfTarget.id}:${pdfTarget.page}`}
            api={api}
            target={pdfTarget}
            onClose={() => setPdfTarget(null)}
            onUnauthorized={onUnauthorized}
          />
        </Suspense>
      )}
    </div>
  );
}
