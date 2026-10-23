export const categories: Record<string, string> = { model: '模型', paper: '论文研究', tool: '工具与框架', product: '产品', industry: '行业', discussion: '社区讨论', other: '其他' }
export const providers: Record<string, string> = { github: 'GitHub', arxiv: 'arXiv', hn: 'Hacker News', stored_news: '已存新闻' }
export const taskLabels: Record<string, string> = { queued: '等待执行', running: '正在研究', completed: '已完成', partial: '部分完成', failed: '研究失败', interrupted: '已中断 / 取消' }
export const nodes: Record<string, string> = { queued: '等待执行', plan_research: '规划研究', retrieve_sources: '检索来源', evaluate_evidence: '评估证据', identify_gaps: '识别缺口', refine_research: '补充检索', synthesize_report: '生成报告', validate_report: '校验引用', persist_result: '保存结果', completed: '研究完成', partial: '部分完成', failed: '执行失败', interrupted: '执行中断' }
export function dateTime(value?: string | null) { if (!value || !Number.isFinite(Date.parse(value))) return '时间未提供'; return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) }
export function shanghaiDate() { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()) }
export function safeUrl(value: string): string | null {
 try { if (value.length > 2048) return null; const url = new URL(value), host = url.hostname.toLowerCase().replace(/^\[|\]$/g, ''); if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !host.includes('.') || /^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host) || host.endsWith('.localhost') || host.endsWith('.local')) return null; return url.href } catch { return null }
}
export function sourceHost(value: string) { const safe = safeUrl(value); return safe ? new URL(safe).hostname.replace(/^www\./, '') : '链接不可用' }
export function validTaskId(value: string) { return /^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/i.test(value) }
export function activeTask(value?: string) { return value === 'queued' || value === 'running' }
