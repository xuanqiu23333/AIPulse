import { onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue'
import { api, ApiError, isAbort } from '../api/client'
import type { Evidence, ResearchReport, TaskSnapshot } from '../api/types'
import { activeTask, validTaskId } from '../lib/format'
import { rememberTask } from '../lib/history'

export function useResearchTask(id: Ref<string>) {
 const snapshot = shallowRef<TaskSnapshot | null>(null), evidence = shallowRef<Evidence[]>([]), report = shallowRef<ResearchReport | null>(null)
 const error = shallowRef<unknown>(null), evidenceError = shallowRef<unknown>(null), reportError = shallowRef<unknown>(null)
 const loading = ref(false), pollingNote = ref('')
 let timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined
 let attempts = 0, failures = 0, deadline = 0, disposed = false
 function stop() { clearTimeout(timer); controller?.abort() }
 async function read(manual = false) {
  clearTimeout(timer)
  if (disposed || !validTaskId(id.value) || document.visibilityState !== 'visible') return
  if (!manual && (attempts >= 80 || Date.now() >= deadline || failures >= 3)) {
   pollingNote.value = '自动观察已暂停；这不表示任务已结束。可以手动读取当前状态。'; return
  }
  controller?.abort(); const current = controller = new AbortController(), target = id.value
  const live = () => !disposed && !current.signal.aborted && target === id.value
  attempts += 1; loading.value = true; error.value = null
  try {
   const task = await api.task(target, current.signal)
   if (!live()) return
   snapshot.value = task; failures = 0; rememberTask(target)
   try {
    const result = await api.evidence(target, current.signal)
    if (live()) { evidence.value = result.evidence; evidenceError.value = null }
   } catch (err) { if (live() && !isAbort(err)) evidenceError.value = err }
   if (live() && !activeTask(task.status) && task.report_ready) {
    try { const result = await api.report(target, current.signal); if (live()) { report.value = result; reportError.value = null } }
    catch (err) { if (live() && !isAbort(err)) reportError.value = err }
   }
  } catch (err) {
   if (live() && !isAbort(err)) {
    error.value = err; failures += 1
    if (err instanceof ApiError && [403, 404, 422].includes(err.status)) failures = 3
   }
  } finally {
   if (live()) {
    loading.value = false
    if ((!snapshot.value || activeTask(snapshot.value.status)) && failures < 3 && attempts < 80 && Date.now() < deadline && document.visibilityState === 'visible') timer = setTimeout(() => void read(), 5000)
    else if ((!snapshot.value || activeTask(snapshot.value.status)) && (failures >= 3 || attempts >= 80 || Date.now() >= deadline)) pollingNote.value = '自动观察已暂停；请检查连接后手动读取，不会重新提交研究任务。'
   }
  }
 }
 watch(id, () => { stop(); snapshot.value = null; evidence.value = []; report.value = null; error.value = null; reportError.value = null; evidenceError.value = null; pollingNote.value = ''; attempts = 0; failures = 0; deadline = Date.now() + 420000; loading.value = false; void read() }, { immediate: true })
 function visibility() { if (document.visibilityState !== 'visible') { stop(); loading.value = false } else if (!snapshot.value || activeTask(snapshot.value.status)) void read() }
 document.addEventListener('visibilitychange', visibility)
 onScopeDispose(() => { disposed = true; stop(); document.removeEventListener('visibilitychange', visibility) })
 function refresh() { failures = 0; pollingNote.value = ''; return read(true) }
 return { snapshot, evidence, report, error, evidenceError, reportError, loading, pollingNote, refresh }
}
