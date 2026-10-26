import type { DailyBrief, Evidence, NewsDetail, NewsPage, NewsSummary, ResearchReport, ResearchRequest, SchedulerStatus, TaskSnapshot } from './types'
const messages: Record<number, string> = {
 403: '功能未启用，或请求不符合后端的本机访问限制。请检查服务端开关。', 404: '未找到对应记录或有效缓存。',
 409: '当前有任务正在执行、数据已变化，或结果尚未准备好。请先读取当前状态。', 422: '参数格式不正确，请检查日期、编号或输入长度。',
 429: '请求受到限流，请稍后手动重试。', 500: '后端处理失败，请检查本机服务状态。', 502: '上游响应无效或模型输出不完整。',
 503: '服务暂不可用，请检查数据库版本、配置或服务端功能开关。', 504: '后端等待超时，可能已产生模型费用，请先查看已有结果。',
}
export const errorLabels: Record<string, string> = {
 report_output_truncated: '研究报告输出达到上限，未生成有效结论。参数修复后的真实付费验收尚未完成。',
 report_invalid_json: '模型没有返回完整、合法的 JSON 报告。', report_schema_mismatch: '模型报告未通过结构校验。',
 research_schema_not_ready: '研究所需的开发库结构尚未准备好。', task_deadline: '研究已达到任务时间上限。',
 task_cancelled: '研究被取消；已完成的模型调用仍可能产生费用。', execution_not_active: '执行进程已停止，任务无法自动恢复。', process_restart: '运行进程发生中断，任务未自动恢复。',
}
export class ApiError extends Error {
 constructor(public status: number, public code: string, public uncertain = false) {
  super(errorLabels[code] ?? messages[status] ?? (code === 'timeout' ? '请求超时。' : code === 'invalid_response' ? '服务未返回预期的 JSON 数据。' : '无法连接本机后端，请确认服务已启动。'))
  this.name = 'ApiError'
 }
}
export function errorMessage(error: unknown): string {
 if (!(error instanceof ApiError)) return '读取数据失败，请手动刷新。'
 return error.message + (error.uncertain ? ' 请求结果可能已被受理，请先查询已有结果，不要立即重复提交。' : '')
}
export function isAbort(error: unknown) { return error instanceof DOMException && error.name === 'AbortError' }
export async function request<T>(path: string, options: { method?: 'GET' | 'POST'; body?: unknown; signal?: AbortSignal; timeout?: number } = {}): Promise<T> {
 if (!path.startsWith('/api/')) throw new ApiError(0, 'invalid_request')
 const controller = new AbortController(), method = options.method ?? 'GET'
 let timedOut = false
 const relay = () => controller.abort()
 if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
 options.signal?.addEventListener('abort', relay, { once: true })
 const timer = setTimeout(() => { timedOut = true; controller.abort() }, options.timeout ?? 15000)
 try {
  const response = await fetch(path, { method, signal: controller.signal, credentials: 'omit', redirect: 'error', headers: { Accept: 'application/json', ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }) })
  let data: unknown
  try { data = await response.json() } catch { throw new ApiError(response.status, 'invalid_response', method === 'POST') }
  if (!response.ok) {
   const value = data as { code?: unknown }
   const code = typeof value?.code === 'string' && /^[a-z0-9_]{1,80}$/.test(value.code) ? value.code : 'http_error'
   throw new ApiError(response.status, code, method === 'POST' && response.status >= 500)
  }
  return data as T
 } catch (error) {
  if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
  if (error instanceof ApiError) throw error
  throw new ApiError(0, timedOut ? 'timeout' : 'network_error', method === 'POST')
 } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', relay) }
}
// No automatic POST retries; components require an explicit confirmation first.
export const api = {
 news: (params: URLSearchParams, signal?: AbortSignal) => request<NewsPage>('/api/v1/news/events?' + params, { signal }),
 detail: (id: number, signal?: AbortSignal) => request<NewsDetail>(`/api/v1/news/events/${id}`, { signal }),
 summary: (id: number, signal?: AbortSignal) => request<NewsSummary>(`/api/v1/news/events/${id}/summary`, { signal }),
 generateSummary: (id: number) => request<NewsSummary>(`/api/v1/news/events/${id}/summary`, { method: 'POST', timeout: 150000 }),
 brief: (date: string, signal?: AbortSignal) => request<DailyBrief>(`/api/v1/briefs/daily/${encodeURIComponent(date)}`, { signal }),
 generateBrief: (date: string) => request<DailyBrief>('/api/v1/briefs/daily', { method: 'POST', body: { brief_date: date }, timeout: 150000 }),
 createResearch: (body: ResearchRequest) => request<{ task_id: string; status: 'queued' }>('/api/v1/research/tasks', { method: 'POST', body, timeout: 20000 }),
 task: (id: string, signal?: AbortSignal) => request<TaskSnapshot>(`/api/v1/research/tasks/${encodeURIComponent(id)}`, { signal }),
 evidence: (id: string, signal?: AbortSignal) => request<{ task_id: string; evidence: Evidence[] }>(`/api/v1/research/tasks/${encodeURIComponent(id)}/evidence`, { signal }),
 report: (id: string, signal?: AbortSignal) => request<ResearchReport>(`/api/v1/research/tasks/${encodeURIComponent(id)}/report`, { signal }),
 cancelResearch: (id: string) => request<{ task_id: string; status: string }>(`/api/v1/research/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
 scheduler: () => request<SchedulerStatus>('/api/v1/news/collect/status'),
}
