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
}

export interface Turn {
  id: string;
  question: string;
  scope: string;
  answer?: Answer;
  error?: string;
}

export interface Question {
  question: string;
  top_k: number;
  document_ids: string[] | null;
}
