// Mirrors the public FastAPI response contracts; never contains server credentials.
export interface TokenUsage { prompt_tokens: number; completion_tokens: number; total_tokens: number; reasoning_tokens?: number | null }
export type Category = 'model' | 'paper' | 'tool' | 'product' | 'industry' | 'discussion' | 'other'
export interface NewsEvent {
 event_id: number; event_key: string; title: string; category: string | null; importance_score: number | null
 score_reason: string | null; rule_version: string | null; status: string; created_at: string; updated_at: string; providers: string[]; item_count: number
}
export interface NewsPage { page: number; page_size: number; total: number; items: NewsEvent[] }
export interface NewsSource { provider: string; external_id: string; title: string; original_url: string; source_name: string; published_at: string | null; fetched_at: string }
export interface NewsDetail extends NewsEvent { summary: string | null; sources: NewsSource[] }
export interface NewsSummary { event_id: number; title: string; summary_zh: string; key_points: string[]; limitations: string; sources: string[]; model: string; token_usage: TokenUsage | null; latency_ms: number; cached: boolean }
export interface BriefHighlight { event_id: number; title: string; category: string | null; importance_score: number; summary_zh: string; sources: string[] }
export interface DailyBrief {
 brief_date: string; timezone: 'Asia/Shanghai'; status: 'complete' | 'empty'; overview: string | null; highlights: BriefHighlight[]
 model_and_products: string[]; tools_and_frameworks: string[]; research: string[]; trends: string[]; limitations: string | null
 model: string | null; token_usage: TokenUsage | null; latency_ms: number | null; generated_at: string | null; cached: boolean; rules_current?: boolean
}
export type TaskStatus = 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'interrupted'
export interface ResearchRequest { topic: string; focus?: string; event_id?: number }
export interface RunMetrics { model_calls: number; search_rounds: number; supplementary_rounds: number; token_usage: TokenUsage | null; usage_missing_calls: number; model_latency_ms: number; elapsed_ms: number }
export interface ProgressEvent { seq: number; node_name: string; event_type: string; payload: Record<string, unknown>; created_at: string }
export interface TaskSnapshot {
 task_id: string; request: ResearchRequest; status: TaskStatus; stage: string; created_at: string; started_at: string | null; finished_at: string | null
 metrics: RunMetrics; evidence_count: number; report_ready: boolean; error_code: string | null; stop_reason: string | null; events: ProgressEvent[]
}
export interface Evidence {
 source_type: string; source_id: string; source_url: string; title: string; content: string; coverage: string; quality_note: string
 published_at: string | null; retrieved_at: string; identity_url?: string | null; citation: string; relevance_note: string; question_ids: string[]
}
export interface VerifiedClaim { text_zh: string; citations: string[]; support_quotes: { citation: string; quote: string }[]; verification: 'source_attributed' }
export interface ClaimValidationIssue {
 section: 'overview' | 'conclusions' | 'analyses' | 'trends'; index: number; question_id: string | null
 text_zh: string; supports: { citation: string; quote: string }[]; reasons: string[]
}
export interface ResearchReport {
 topic: string; status: 'completed' | 'partial' | 'failed'; error_code?: string | null; overview: VerifiedClaim[]; conclusions: VerifiedClaim[]
 analyses: { question_id: string; question: string; claims: VerifiedClaim[] }[]; trends: VerifiedClaim[]; limitations: string[]
 sources: { citation: string; source_type: string; source_url: string; title: string; coverage: string; quality_note: string; excerpt: string }[]
 models: string[]; metrics: RunMetrics; prompt_version: string
 source_warnings?: string[]; validation_issues?: ClaimValidationIssue[]
}
export interface SchedulerStatus { enabled: boolean; active: boolean; running: boolean; last_run: { started_at: string; finished_at: string | null; status: string; processed_count: number; failure_codes: string[] } | null }
