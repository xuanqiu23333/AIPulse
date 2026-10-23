import { onScopeDispose, shallowRef } from 'vue'
import { api } from '../api/client'
import type { NewsDetail, NewsEvent } from '../api/types'

/** At most ten existing detail GETs; no summaries are generated or fetched. */
export function useNewsDetails() {
 const details = shallowRef<Record<number, NewsDetail>>({})
 let controller: AbortController | undefined
 async function load(events: NewsEvent[]) {
  controller?.abort(); const current = controller = new AbortController()
  details.value = {}
  const results = await Promise.all(events.slice(0, 10).map(async event => {
   try { return await api.detail(event.event_id, current.signal) } catch { return null }
  }))
  if (!current.signal.aborted) details.value = Object.fromEntries(results.filter((item): item is NewsDetail => item !== null).map(item => [item.event_id, item]))
 }
 onScopeDispose(() => controller?.abort())
 return { details, load }
}
