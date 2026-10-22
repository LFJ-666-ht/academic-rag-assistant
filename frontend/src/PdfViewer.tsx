import { useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  LoaderCircle,
  X,
  ZoomIn,
  ZoomOut,
  FileText,
} from "lucide-react";
import { getDocument, GlobalWorkerOptions } from "pdfjs-dist";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { RagApi } from "./api";
import { ApiError } from "./api";

GlobalWorkerOptions.workerSrc = workerUrl;

export interface PdfTarget {
  id: string;
  filename: string;
  page: number;
}
export default function PdfViewer({
  api,
  target,
  onClose,
  onUnauthorized,
}: {
  api: RagApi;
  target: PdfTarget;
  onClose: () => void;
  onUnauthorized: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(target.page);
  const [pageInput, setPageInput] = useState(String(target.page));
  const [scale, setScale] = useState(1);
  const [width, setWidth] = useState(700);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [downloadUrl, setDownloadUrl] = useState("");

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => {
      element.close();
      opener.current?.focus();
    };
  }, []);
  useEffect(() => {
    let alive = true;
    let objectUrl = "";
    let loading: ReturnType<typeof getDocument> | undefined;
    setPdf(null);
    setError("");
    setBusy(true);
    setPage(target.page);
    setPageInput(String(target.page));
    api
      .pdf(target.id)
      .then(async (blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setDownloadUrl(objectUrl);
        const data = new Uint8Array(await blob.arrayBuffer());
        if (!alive) return;
        loading = getDocument({
          data,
          cMapUrl: "/pdf-assets/cmaps/",
          cMapPacked: true,
          standardFontDataUrl: "/pdf-assets/standard_fonts/",
          wasmUrl: "/pdf-assets/wasm/",
        });
        const doc = await loading.promise;
        if (!alive) return;
        setPdf(doc);
        const next = Math.max(1, Math.min(target.page, doc.numPages));
        setPage(next);
        setPageInput(String(next));
      })
      .catch((e) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 401) onUnauthorized();
        setError(
          e instanceof ApiError ? e.message : "PDF 预览失败。可下载原文查看。",
        );
        setBusy(false);
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      void loading?.destroy();
    };
  }, [api, target.id]);

  useEffect(() => {
    if (!viewport.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(220, entry.contentRect.width - 48)),
    );
    observer.observe(viewport.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!pdf || !canvas.current) return;
    let alive = true;
    let render: RenderTask | undefined;
    setBusy(true);
    setError("");
    pdf
      .getPage(page)
      .then((pdfPage) => {
        if (!alive || !canvas.current) return;
        const original = pdfPage.getViewport({ scale: 1 });
        const size = pdfPage.getViewport({
          scale: Math.min(width / original.width, 1.5) * scale,
        });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const el = canvas.current;
        const ctx = el.getContext("2d")!;
        el.width = Math.floor(size.width * ratio);
        el.height = Math.floor(size.height * ratio);
        el.style.width = `${size.width}px`;
        el.style.height = `${size.height}px`;
        render = pdfPage.render({
          canvas: el,
          canvasContext: ctx,
          viewport: size,
          transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
        });
        return render.promise;
      })
      .then(() => {
        if (alive) setBusy(false);
      })
      .catch((e) => {
        if (alive && e?.name !== "RenderingCancelledException") {
          setError("这一页暂时无法显示，请切换页面或下载原文。");
          setBusy(false);
        }
      });
    return () => {
      alive = false;
      render?.cancel();
    };
  }, [pdf, page, scale, width]);

  function go(next: number) {
    if (!pdf) return;
    const p = Number.isFinite(next)
      ? Math.max(1, Math.min(pdf.numPages, Math.trunc(next)))
      : page;
    setPage(p);
    setPageInput(String(p));
  }

  return (
    <dialog
      ref={dialog}
      className="pdf-dialog"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-labelledby="pdf-title"
    >
      <div className="pdf-shell">
        <header className="pdf-header">
          <FileText size={20} />
          <div>
            <h2 id="pdf-title">{target.filename}</h2>
            <p>论文原文 · PDF 物理页码</p>
          </div>
          <button
            className="icon-button"
            aria-label="关闭原文"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        <div className="pdf-toolbar">
          <button
            className="icon-button"
            aria-label="上一页"
            disabled={!pdf || page <= 1}
            onClick={() => go(page - 1)}
          >
            <ChevronLeft size={18} />
          </button>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              go(Number(pageInput));
            }}
          >
            <input
              aria-label="PDF 页码"
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value)}
              onBlur={() => go(Number(pageInput))}
              inputMode="numeric"
              disabled={!pdf}
            />{" "}
            / {pdf?.numPages ?? "—"}
          </form>
          <button
            className="icon-button"
            aria-label="下一页"
            disabled={!pdf || page >= pdf.numPages}
            onClick={() => go(page + 1)}
          >
            <ChevronRight size={18} />
          </button>
          <span className="toolbar-divider" />
          <button
            className="icon-button"
            aria-label="缩小"
            disabled={scale <= 0.6}
            onClick={() => setScale((v) => Math.max(0.6, v - 0.2))}
          >
            <ZoomOut size={18} />
          </button>
          <span>{Math.round(scale * 100)}%</span>
          <button
            className="icon-button"
            aria-label="放大"
            disabled={scale >= 2}
            onClick={() => setScale((v) => Math.min(2, v + 0.2))}
          >
            <ZoomIn size={18} />
          </button>
          {downloadUrl && (
            <a
              className="text-button download-link"
              href={downloadUrl}
              download={target.filename}
            >
              <Download size={16} />
              下载 PDF
            </a>
          )}
        </div>
        <div ref={viewport} className="pdf-stage">
          {busy && (
            <div role="status" className="pdf-loading">
              <LoaderCircle className="spin" size={20} />
              正在读取原文…
            </div>
          )}
          {error && (
            <div role="alert" className="error-box">
              {error}
            </div>
          )}
          <canvas ref={canvas} aria-label={`论文第 ${page} 页`} />
        </div>
      </div>
    </dialog>
  );
}
