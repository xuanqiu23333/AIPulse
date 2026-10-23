<script setup lang="ts">
import { ref, shallowRef, watch, onBeforeUnmount } from 'vue'
import { ArrowUpRight, FlaskConical } from '@lucide/vue'
import { api } from '../api/client'
import type { TaskSnapshot } from '../api/types'
import { recentTasks } from '../lib/history'
import StatusBadge from './StatusBadge.vue'
const props = withDefaults(defineProps<{ limit?: number }>(), { limit: 3 })
const items = shallowRef<Record<string, TaskSnapshot>>({}), unreadable = ref<string[]>([]), loading = ref(false)
let controller: AbortController | undefined
watch(() => recentTasks.value.slice(0, Math.min(props.limit, 3)), async ids => {
 controller?.abort(); const current = controller = new AbortController()
 items.value = {}; unreadable.value = []; loading.value = ids.length > 0
 const results = await Promise.all(ids.map(async id => { try { return await api.task(id, current.signal) } catch { return null } }))
 if (current.signal.aborted) return
 items.value = Object.fromEntries(results.filter((item): item is TaskSnapshot => item !== null).map(item => [item.task_id, item]))
 unreadable.value = ids.filter(id => !items.value[id]); loading.value = false
}, { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
 <p v-if="!recentTasks.length" class="rail-empty"><FlaskConical :size="26" aria-hidden="true" />尚无本浏览器访问记录。输入已有任务 ID 即可只读查询。</p>
 <p v-if="loading" class="muted text-xs" role="status">正在读取已访问任务的实际状态…</p>
 <ul class="task-history-list">
  <li v-for="id in recentTasks.slice(0, Math.min(limit, 3))" :key="id">
   <RouterLink :to="`/research/${id}`"><template v-if="items[id]"><StatusBadge :status="items[id]!.status" /><h4>{{ items[id]!.request.topic }}</h4><p class="text-xs muted">{{ items[id]!.evidence_count }} 条已保存证据 · {{ items[id]!.metrics.model_calls }} 次模型调用</p><code v-if="items[id]!.error_code" class="task-error-code">{{ items[id]!.error_code }}</code></template><template v-else><span class="badge neutral">{{ unreadable.includes(id) ? '状态暂不可读' : '读取中' }}</span><p class="mono text-xs">{{ id }}</p></template><span class="task-history-footer"><span class="mono">{{ id.slice(0, 8) }}…</span>查看任务 <ArrowUpRight :size="13" /></span></RouterLink>
  </li>
 </ul>
</template>
