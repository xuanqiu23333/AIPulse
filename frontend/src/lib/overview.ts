import { api, ApiError } from '../api/client'
import type { DailyBrief, NewsEvent } from '../api/types'

/** Bounded discovery only: there is no backend daily-brief list API. */
export function briefCandidates(events: NewsEvent[], today: string): string[] {
 const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' })
 const dates = events.flatMap(event => [event.updated_at, event.created_at])
  .filter(value => Number.isFinite(Date.parse(value))).map(value => formatter.format(new Date(value)))
 return [...new Set([today, ...dates])].sort().reverse().slice(0, 3)
}

export async function findSavedBrief(dates: string[], signal: AbortSignal): Promise<DailyBrief | null> {
 for (const date of dates.slice(0, 3)) {
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  try { const result = await api.brief(date, signal); if (result.status === 'complete') return result }
  catch (error) { if (!(error instanceof ApiError && error.status === 404)) throw error }
 }
 return null
}
