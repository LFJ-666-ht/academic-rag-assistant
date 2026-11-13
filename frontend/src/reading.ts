import type { Paper, ReadingProgress, ReadingStatus } from "./types.ts";

export const readingLabels: Record<ReadingStatus, string> = {
  unread: "待读", reading: "在读", completed: "已读",
};

export function readingOf(paper: Paper): ReadingProgress {
  return paper.reading ?? {
    status: "unread", last_page: 0, furthest_page: 0, progress_percent: 0,
    updated_at: null, completed_at: null,
  };
}

export function filterReading(papers: Paper[], search: string, status: ReadingStatus | "all") {
  return papers.filter((paper) => paper.filename.toLowerCase().includes(search.trim().toLowerCase()) &&
    (status === "all" || readingOf(paper).status === status));
}

/** Serialize page writes so a slow earlier response cannot overwrite the latest bookmark.
 * Rapid page turns coalesce to the latest pending page. A failed write remains
 * pending until the user retries or turns another page; no silent success state.
 */
export class ReadingSaver {
  private pending: number | null = null;
  private saved: number | null = null;
  private flight: Promise<void> | null = null;
  private write: (page: number) => Promise<void>;
  constructor(write: (page: number) => Promise<void>) { this.write = write; }

  record(page: number): Promise<void> {
    if (page !== this.saved || this.flight) this.pending = page;
    return this.flush();
  }

  flush(): Promise<void> {
    if (this.flight) return this.flight;
    if (this.pending === null) return Promise.resolve();
    this.flight = this.drain().then(() => {
      this.flight = null;
      // A new visit can arrive after drain resolves but before this microtask.
      if (this.pending !== null) return this.flush();
    }, (error: unknown) => {
      this.flight = null;
      throw error;
    });
    return this.flight;
  }

  private async drain() {
    while (this.pending !== null) {
      const next = this.pending;
      this.pending = null;
      if (next === this.saved) continue;
      try {
        await this.write(next);
        this.saved = next;
      } catch (error) {
        if (this.pending === null) this.pending = next;
        throw error;
      }
    }
  }
}
