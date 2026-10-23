<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, RefreshCw, Square } from '@lucide/vue'
import { api, errorMessage } from '../api/client'
import { useResearchTask } from '../composables/useResearchTask'
import { activeTask, dateTime, nodes, validTaskId } from '../lib/format'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import EvidenceList from '../components/EvidenceList.vue'
import ReportViewer from '../components/ReportViewer.vue'
import StatusBadge from '../components/StatusBadge.vue'
import UiState from '../components/UiState.vue'
import UsageNote from '../components/UsageNote.vue'
import ResearchIssueNotice from '../components/ResearchIssueNotice.vue'
const route = useRoute(), id = computed(() => String(route.params.taskId ?? '')), state = useResearchTask(id)
const confirm = ref(false), cancelling = ref(false), cancelError = ref<unknown>(null), cancelAccepted = ref(false)
watch(id, () => { confirm.value = false; cancelError.value = null; cancelAccepted.value = false })
async function cancel() {
 if (!confirm.value || cancelling.value) return
 cancelling.value = true; cancelError.value = null; const target = id.value
 try { await api.cancelResearch(target); if (target === id.value) { cancelAccepted.value = true; await state.refresh() } }
 catch (err) { if (target === id.value) cancelError.value = err }
 finally { cancelling.value = false; confirm.value = false }
}
</script>
<template>
 <div class="between" style="margin-bottom:20px"><RouterLink to="/research" class="section-link"><ArrowLeft :size="15" />返回研究工作台</RouterLink><button v-if="validTaskId(id)" type="button" class="button secondary" :disabled="state.loading.value" @click="state.refresh"><RefreshCw :size="15" :class="{ spin: state.loading.value }" />读取状态</button></div>
 <UiState v-if="!validTaskId(id)" kind="error" title="任务 ID 格式不正确" detail="请输入完整 UUID，或从本浏览器访问历史打开。" /><UiState v-else-if="state.loading.value && !state.snapshot.value" kind="loading" title="正在读取研究任务" /><UiState v-else-if="state.error.value && !state.snapshot.value" kind="error" :error="state.error.value" retry @retry="state.refresh" />
 <template v-if="state.snapshot.value"><div v-if="state.error.value" class="notice warning" role="alert">{{ errorMessage(state.error.value) }} 当前展示的是上次读取状态。</div><div v-if="state.pollingNote.value" class="notice info">{{ state.pollingNote.value }}</div><section class="panel task-overview" style="margin-bottom:22px"><div class="task-head"><div style="min-width:0"><StatusBadge :status="state.snapshot.value.status" /><h2>{{ state.snapshot.value.request.topic }}</h2><p v-if="state.snapshot.value.request.focus">{{ state.snapshot.value.request.focus }}</p><p class="mono" style="margin-top:10px">{{ id }}</p><p style="margin-top:6px">开始 {{ dateTime(state.snapshot.value.started_at) }} · 结束 {{ dateTime(state.snapshot.value.finished_at) }}</p></div><button v-if="activeTask(state.snapshot.value.status)" class="button danger" type="button" :disabled="cancelling || cancelAccepted" @click="confirm = true"><Square :size="13" />{{ cancelAccepted ? '已请求取消' : '取消任务' }}</button></div><ResearchIssueNotice v-if="state.snapshot.value.error_code" :status="state.snapshot.value.status" :code="state.snapshot.value.error_code" /><p v-if="cancelError" class="form-error" role="alert">{{ errorMessage(cancelError) }}</p><div v-if="cancelAccepted" class="notice info">取消请求已受理，最终状态以随后读取结果为准。已发生的调用可能计费。</div><dl class="task-metrics"><div><dt>当前阶段</dt><dd style="font-size:15px">{{ nodes[state.snapshot.value.stage] ?? state.snapshot.value.stage }}</dd></div><div><dt>已执行检索</dt><dd>{{ state.snapshot.value.metrics.search_rounds }} <small>/ 3 轮</small></dd></div><div><dt>已保存证据</dt><dd>{{ state.snapshot.value.evidence_count }} <small>/ 18 条</small></dd></div><div><dt>模型调用</dt><dd>{{ state.snapshot.value.metrics.model_calls }} <small>/ 2 次</small></dd></div></dl><UsageNote :usage="state.snapshot.value.metrics.token_usage" :missing-calls="state.snapshot.value.metrics.usage_missing_calls" :latency-ms="state.snapshot.value.metrics.model_latency_ms" /><p class="muted text-xs" style="margin-top:12px">任务已记录耗时 {{ (state.snapshot.value.metrics.elapsed_ms / 1000).toFixed(2) }} 秒。仅活动任务在可见页面中有界轮询；读取不会重新发起研究。</p></section>
 <div class="task-columns"><aside class="panel workflow-panel"><p class="eyebrow">WORKFLOW TRACE</p><h3>研究执行轨迹</h3><p class="muted text-xs" style="margin-top:7px">仅展示后端记录的节点，不模拟进度。</p><ol class="timeline"><li v-for="event in state.snapshot.value.events" :key="event.seq"><strong>{{ nodes[event.node_name] ?? event.node_name }}</strong><small>{{ dateTime(event.created_at) }}</small><code>{{ event.node_name }} · {{ event.event_type }}</code></li></ol><p v-if="!state.snapshot.value.events.length" class="muted text-xs" style="margin-top:16px">暂无已记录节点。</p></aside><div class="stack mobile-stack"><nav class="tab-links" aria-label="研究结果导航"><a href="#report">研究报告</a><a href="#evidence">真实证据 {{ state.evidence.value.length }}</a></nav><UiState v-if="state.reportError.value" kind="error" :error="state.reportError.value" title="报告暂时无法读取" retry @retry="state.refresh" /><ReportViewer v-else-if="state.report.value" :report="state.report.value" :evidence-ids="state.evidence.value.map(item => item.citation)" :task-failed="state.snapshot.value.status === 'failed'" :task-error="state.snapshot.value.error_code" /><section v-else id="report"><UiState kind="empty" :title="activeTask(state.snapshot.value.status) ? '研究仍在进行' : '任务没有已保存报告'" :detail="activeTask(state.snapshot.value.status) ? '等待真实节点完成。页面不会模拟生成内容，也不会重新提交任务。' : '可以查看已有证据和执行轨迹；此状态不表示研究成功。'" /></section><UiState v-if="state.evidenceError.value" kind="error" :error="state.evidenceError.value" title="证据暂时无法读取" retry @retry="state.refresh" /><EvidenceList :evidence="state.evidence.value" /></div></div></template>
 <ConfirmDialog :open="confirm" :busy="cancelling" title="确认取消当前研究？" message="仅请求取消这个任务，不会重新创建。已发生的模型调用仍可能产生费用，已保存证据会保留。最终状态以后端返回为准。" confirm-label="确认取消" @confirm="cancel" @cancel="confirm = false" />
</template>
