import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ArrowRight, BookOpen, KeyRound, Library, LoaderCircle, LogOut, ShieldCheck, X, ImagePlus, Languages } from "lucide-react";
import { RagApi } from "./api";
import Modal from "./Modal";
import ImageStudio from "./ImageStudio";
import TranslationStudio from "./TranslationStudio";
import { Workspace } from "./Workspace";
import { messageOf } from "./workspace-shared";
import type { Session } from "./workspace-shared";

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

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [showConnect, setShowConnect] = useState(false);
  const [token, setToken] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");
  const [imageStudio, setImageStudio] = useState<{ instruction: string; documentIds: string[] } | null>(null);
  const [translation, setTranslation] = useState<{ documentId?: string; page?: number } | null>(null);
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
    setImageStudio(null);
    setTranslation(null);
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
          <button className="secondary small translation-open-button" onClick={() => {
            if (session) setTranslation({});
            else setShowConnect(true);
          }}><Languages size={16} />论文翻译</button>
          <button className="secondary small image-open-button" onClick={() => {
            if (session) setImageStudio({ instruction: "", documentIds: [] });
            else setShowConnect(true);
          }}><ImagePlus size={16} />AI 配图</button>
          {session ? (
            <>
              <span className="connection-status">
                <i />
                已连接知识库
              </span>
              <button
                className="icon-button"
                title="断开连接，已保存的问答记录保留"
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
        onIllustrate={(instruction, documentIds) => setImageStudio({ instruction, documentIds })}
        onTranslate={(documentId, page = 1) => setTranslation({ documentId, page })}
      />
      {translation && session && <TranslationStudio api={session.api}
        initialDocumentId={translation.documentId} initialPage={translation.page}
        onClose={() => setTranslation(null)}
        onUnauthorized={() => logout("访问令牌已失效，请重新连接。")}
      />}
      {imageStudio && session && <ImageStudio api={session.api}
        initialInstruction={imageStudio.instruction} initialDocumentIds={imageStudio.documentIds}
        onClose={() => setImageStudio(null)}
        onUnauthorized={() => logout("访问令牌已失效，请重新连接。")}
      />}
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

