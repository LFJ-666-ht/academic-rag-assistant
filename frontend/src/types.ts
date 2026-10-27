export interface Paper {
  document_id: string;
  filename: string;
  pages: number;
  chunk_count: number;
  status: string;
  created_at: string;
  empty_pages?: string | number[];
  duplicate?: boolean;
}

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
