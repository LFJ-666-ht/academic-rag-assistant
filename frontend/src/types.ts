export interface Paper {
  document_id: string;
  filename: string;
  pages: number;
  chunk_count: number;
  status: string;
  created_at: string;
  empty_pages?: string | number[];
  duplicate?: boolean;
  reading?: ReadingProgress;
}

export type ReadingStatus = "unread" | "reading" | "completed";
export interface ReadingProgress {
  status: ReadingStatus;
  last_page: number;
  furthest_page: number;
  progress_percent: number;
  updated_at: string | null;
  completed_at: string | null;
}
export type ReadingUpdate = { page: number; status?: never } | { status: ReadingStatus; page?: never };

export interface Evidence {
  id: number;
  document_id: string;
  chunk_id: string;
  filename: string;
  page: number;
  quote: string;
  pdf_url: string;
}

export interface Answer {
  answer: string;
  citations: Evidence[];
  evidence: Evidence[];
  citation_status: "ids_valid" | "missing_citations" | "no_evidence";
  finish_reason?: string;
  incomplete?: boolean;
  warning?: string | null;
  model?: string | null;
  max_tokens?: number | null;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; reasoning_tokens?: number };
}

export interface Turn {
  id: string;
  question: string;
  scope: string;
  answer?: Answer;
  error?: string;
  preview?: string;
  progress?: string;
}

export type ChatEvent =
  | { type: "progress"; message: string }
  | { type: "delta"; text: string }
  | { type: "done"; answer: Answer }
  | { type: "error"; message: string; status?: number };

export interface Question {
  question: string;
  top_k: number;
  document_ids: string[] | null;
}

export interface ImageConfig {
  enabled: boolean;
  model: string;
  size: string;
  watermark: boolean;
}

export interface ImageDraft {
  draft_id: string;
  prompt: string;
  evidence: Evidence[];
}

export interface ImageSubmission {
  request_id: string;
  prompt: string;
  draft_id: string | null;
}

export interface ImageJob extends ImageSubmission {
  id: string;
  model: string;
  size: string;
  status: "queued" | "running" | "succeeded" | "failed";
  error: string | null;
  file_url: string | null;
  evidence: Evidence[];
  created_at: string;
  updated_at: string;
}

export interface TranslationConfig {
  enabled: boolean;
  model: string;
  max_pages: number;
}

export interface TranslationSubmission {
  glossary_version?: string;
  request_id: string;
  document_id: string;
  start_page: number;
  end_page: number;
  target_language: "zh" | "en";
}

export interface TranslationPage {
  page: number;
  original: string;
  translation: string;
  status: "pending" | "running" | "completed" | "skipped";
  reused: number;
}

export interface TranslationJob extends TranslationSubmission {
  glossary_revision?: number; glossary_count?: number; glossary_changed?: boolean;
  id: string;
  filename: string;
  model: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  error: string | null;
  total_pages: number;
  completed_pages: number;
  skipped_pages: number;
  reused_pages: number;
  created_at: string;
  updated_at: string;
}

export interface TranslationDetail extends TranslationJob {
  pages: TranslationPage[];
}
