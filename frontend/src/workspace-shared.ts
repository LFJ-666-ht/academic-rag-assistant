import type { RagApi } from "./api";
import type { Paper } from "./types";
export type Session = { api: RagApi; papers: Paper[]; version: string; id: number };
export function messageOf(e: unknown) { return e instanceof Error ? e.message : "操作失败，请重试。"; }
