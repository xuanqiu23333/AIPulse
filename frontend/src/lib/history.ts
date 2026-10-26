import { ref } from 'vue'
import { validTaskId } from './format'
const key = 'aipulse.recent-task-ids.v1'
function read(): string[] { try { const ids: unknown = JSON.parse(localStorage.getItem(key) ?? '[]'); return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string' && validTaskId(id)).slice(0, 10) : [] } catch { return [] } }
export const recentTasks = ref(read())
export function rememberTask(id: string) { if (!validTaskId(id)) return; recentTasks.value = [id, ...recentTasks.value.filter(item => item !== id)].slice(0, 10); try { localStorage.setItem(key, JSON.stringify(recentTasks.value)) } catch { /* memory-only in private browsing */ } }
export function clearHistory() { recentTasks.value = []; try { localStorage.removeItem(key) } catch { /* memory-only */ } }
