import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { BookOpen, Check, LoaderCircle } from "lucide-react";
import type { RagApi } from "./api";
import { ApiError } from "./api";
import type { Paper, ReadingProgress, ReadingStatus, ReadingUpdate } from "./types";
import { readingLabels, readingOf } from "./reading";
import Modal from "./Modal";

export default function ReadingCard({ api, paper, onChange, onRead, onUnauthorized }: {
  api: RagApi; paper: Paper; onChange: (progress: ReadingProgress) => void;
  onRead: () => void; onUnauthorized: () => void;
}) {
  const current = readingOf(paper);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [page, setPage] = useState(String(current.last_page || 1));
  const [reset, setReset] = useState(false);
  const alive = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => { if (!editing) setPage(String(current.last_page || 1)); }, [current.last_page, editing]);

  async function save(update: ReadingUpdate) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api.saveReading(paper.document_id, update);
      onChange(result);
      if (alive.current) { setEditing(false); setReset(false); }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      if (alive.current) setError(e instanceof Error ? e.message : "进度未保存，请重试。");
    } finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  function record(event: FormEvent) {
    event.preventDefault();
    const value = Number(page);
    if (!Number.isInteger(value) || value < 1 || value > paper.pages) {
      setError(`请输入 1–${paper.pages} 之间的 PDF 页码。`);
      return;
    }
    void save({ page: value });
  }
  function changeStatus(status: ReadingStatus) {
    if (status === "unread" && current.status !== "unread") setReset(true);
    else void save({ status });
  }

  return <div className="reading-card">
    <div className="reading-card-top">
      <select aria-label={`${paper.filename} 阅读状态`} value={current.status} disabled={busy}
        className={`reading-status ${current.status}`} onChange={(e) => changeStatus(e.target.value as ReadingStatus)}>
        {Object.entries(readingLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <span>{current.last_page ? `上次第 ${current.last_page} 页` : "尚未开始"}</span>
      {busy && <LoaderCircle size={12} className="spin" aria-label="正在保存阅读进度" />}
    </div>
    <progress max={100} value={current.progress_percent} aria-label={`${paper.filename} 阅读进度`}
      title="按最远翻阅页计算，跳页不代表已读完中间页面；已读需手动标记" />
    <div className="reading-card-progress"><span>最远第 {current.furthest_page} / {paper.pages} 页</span><span>{current.progress_percent}%</span></div>
    <div className="reading-card-actions">
      <button className="text-button" disabled={busy} onClick={onRead}><BookOpen size={13} />{current.last_page ? "继续阅读" : "开始阅读"}</button>
      <button className="text-button" disabled={busy} onClick={() => setEditing(!editing)}>{editing ? "收起" : "记录页码"}</button>
    </div>
    {editing && <form className="reading-page-form" onSubmit={record}>
      <input type="number" min={1} max={paper.pages} required value={page} disabled={busy}
        aria-label={`${paper.filename} 记录页码`} onChange={(e) => setPage(e.target.value)} />
      <span>/ {paper.pages} 页</span><button className="text-button" disabled={busy} type="submit"><Check size={13} />保存</button>
    </form>}
    {current.updated_at && <p className="reading-updated">更新于 {new Date(current.updated_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</p>}
    {error && <p className="reading-save-error" role="alert">{error}</p>}
    {reset && <Modal className="confirm-card" labelId={`reading-reset-${paper.document_id}`} busy={busy} onClose={() => setReset(false)}>
      <h2 id={`reading-reset-${paper.document_id}`}>重新设为待读？</h2>
      <p>{paper.filename}</p><p className="field-help">这会清除该论文的上次页码、最远进度和完成时间，论文与译文仍保留。</p>
      {error && <p role="alert" className="error-box">{error}</p>}
      <div className="confirm-actions"><button className="secondary" disabled={busy} onClick={() => setReset(false)}>取消</button>
        <button className="primary" disabled={busy} onClick={() => void save({ status: "unread" })}>{busy ? "正在保存…" : "确认重置"}</button></div>
    </Modal>}
  </div>;
}
