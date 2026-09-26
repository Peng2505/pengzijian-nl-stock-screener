export type Operator = "lt" | "lte" | "gt" | "gte" | "between" | "eq";

export interface Condition {
  id: string;
  intent_label: string;
  field: string;
  op: Operator;
  value: number | number[];
  unit: string;
  required: boolean;
  source_hint: string;
  confidence: number;
  enabled: boolean;
  note: string;
}

export interface ConflictItem {
  condition_ids: string[];
  reason: string;
  suggestion: string;
}

export interface ClarificationItem {
  id: string;
  question: string;
  options: string[];
  default?: string | null;
  related_intent: string;
}

export interface ScreeningSpec {
  id: string;
  universe: {
    type: "index" | "custom" | "sample";
    index_code?: string;
    tickers: string[];
    limit: number;
  };
  conditions: Condition[];
  conflicts: ConflictItem[];
  clarifications: ClarificationItem[];
  unsupported: { text: string; reason: string }[];
  meta: {
    raw_query: string;
    model: string;
    generated_at: string;
    assumptions: string[];
  };
  version: number;
}

export interface DataPointEvidence {
  field: string;
  value: number | string | null;
  unit: string;
  as_of?: string | null;
  source: string;
  request_id?: string | null;
  status: "ok" | "missing" | "error" | "stale";
  message: string;
}

export interface ConditionEval {
  condition_id: string;
  intent_label: string;
  field: string;
  passed: boolean | null;
  expected: string;
  actual: DataPointEvidence;
  kind: "fact";
}

export interface StockResult {
  thscode: string;
  ticker: string;
  name: string;
  selected: boolean;
  score: number;
  condition_evals: ConditionEval[];
  summary: string;
  data_quality: "complete" | "partial" | "failed";
}

export interface ScreeningResult {
  spec: ScreeningSpec;
  meta: {
    run_id: string;
    started_at: string;
    finished_at?: string;
    universe_size: number;
    selected_count: number;
    excluded_count: number;
    data_mode: "live" | "mock";
    warnings: string[];
    disclaimer: string;
  };
  selected: StockResult[];
  excluded: StockResult[];
  excluded_sample_limit: number;
}

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg =
      typeof data?.detail === "string"
        ? data.detail
        : data?.detail?.message || data?.message || res.statusText;
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return data as T;
}

export const api = {
  health: () =>
    request<{
      status: string;
      use_mock_data: boolean;
      fuyao_configured: boolean;
      llm_configured: boolean;
      ifind_configured?: boolean;
    }>("/health"),
  enrichStock: (thscode: string, name = "") =>
    request<{
      available: boolean;
      kind: "reference";
      disclaimer: string;
      thscode: string;
      quote: {
        status: string;
        source: string;
        as_of?: string | null;
        fields: Record<string, string | number>;
        message?: string;
      } | null;
      news: Array<{ title: string; summary: string; source: string; as_of?: string | null; url?: string }>;
      errors: string[];
    }>("/enrich/stock", {
      method: "POST",
      body: JSON.stringify({ thscode, name }),
    }),
  parseIntent: (
    query: string,
    answers: Record<string, string> = {},
    history: Array<{ role: "user" | "assistant" | "system"; content: string }> = [],
    follow_up = "",
  ) =>
    request<{ spec: ScreeningSpec; ai_notes: string[]; used_fallback: boolean }>("/intent/parse", {
      method: "POST",
      body: JSON.stringify({ query, answers, history, follow_up }),
    }),
  runScreen: (spec: ScreeningSpec) =>
    request<ScreeningResult>("/screen/run", {
      method: "POST",
      body: JSON.stringify({ spec, exclude_sample_limit: 20 }),
    }),
  saveStrategy: (name: string, spec: ScreeningSpec, result_summary?: object) =>
    request("/strategies", {
      method: "POST",
      body: JSON.stringify({ name, spec, result_summary }),
    }),
  listStrategies: () => request<Array<{ id: string; name: string; created_at: string; notes: string }>>("/strategies"),
  compare: (left_spec: ScreeningSpec, right_spec: ScreeningSpec) =>
    request<{
      condition_diff: Array<{ field: string; left: string | null; right: string | null; changed: boolean }>;
      overlap_selected: string[];
      only_left: string[];
      only_right: string[];
      left_result: ScreeningResult;
      right_result: ScreeningResult;
    }>("/compare", {
      method: "POST",
      body: JSON.stringify({ left_spec, right_spec }),
    }),
  backtestLite: (spec: ScreeningSpec) =>
    request<{
      kind: string;
      disclaimer: string;
      selected_count?: number;
      universe_size?: number;
      distribution?: Array<{ field: string; hit_rate: number; hits: number; total: number }>;
      message?: string;
    }>("/backtest/lite", {
      method: "POST",
      body: JSON.stringify({ spec }),
    }),
  createMonitorDraft: (name: string, spec: ScreeningSpec) =>
    request("/monitor/drafts", {
      method: "POST",
      body: JSON.stringify({ name, spec, schedule: "daily_close" }),
    }),
};
