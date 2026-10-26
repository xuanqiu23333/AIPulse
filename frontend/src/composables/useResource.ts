import { onScopeDispose, ref, shallowRef } from 'vue'
import { isAbort } from '../api/client'
export function useResource<T>() {
 const data = shallowRef<T | null>(null), error = shallowRef<unknown>(null), loading = ref(false), readAt = ref<string | null>(null)
 let controller: AbortController | undefined
 async function load(reader: (signal: AbortSignal) => Promise<T>) {
  controller?.abort(); const current = controller = new AbortController()
  loading.value = true; error.value = null; data.value = null
  try { const value = await reader(current.signal); if (!current.signal.aborted) { data.value = value; readAt.value = new Date().toISOString() } }
  catch (err) { if (!current.signal.aborted && !isAbort(err)) error.value = err }
  finally { if (current === controller) loading.value = false }
 }
 onScopeDispose(() => controller?.abort())
 return { data, error, loading, readAt, load }
}
