import { ArrowRight, CircleHelp, Quote } from "lucide-react";
import type { Answer, Evidence } from "./types";
export default function EvidencePanel({ view, activeTurn, activeEvidence, openPdf }: { view?: Answer; activeTurn: string; activeEvidence: number | null; openPdf: (e: Evidence) => void }) {
  const citations = view?.citations ?? [];
  return (
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
  );
}
