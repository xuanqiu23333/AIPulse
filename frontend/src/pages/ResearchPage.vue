<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, FlaskConical, Search, ShieldCheck, Trash2 } from '@lucide/vue'
import { api, ApiError, errorMessage } from '../api/client'
import type { ResearchRequest } from '../api/types'
import { validTaskId } from '../lib/format'
import { clearHistory, recentTasks, rememberTask } from '../lib/history'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import TaskHistoryList from '../components/TaskHistoryList.vue'
const route = useRoute(), router = useRouter()
const topic = ref(typeof route.query.topic === 'string' ? route.query.topic.slice(0, 300) : ''), focus = ref(''), lookupId = ref('')
const validation = ref(''), lookupError = ref(''), pending = ref(false), error = ref<unknown>(null), uncertain = ref(false), acceptedId = ref('')
const draft = ref<ResearchRequest | null>(null)
const linkedId = computed(() => { const id = Number(route.query.event_id); return Number.isSafeInteger(id) && id > 0 ? id : undefined })
function prepare() {
 if (pending.value || uncertain.value || acceptedId.value) return
 validation.value = ''; error.value = null
 const value = topic.value.trim(), detail = focus.value.trim()
 if (value.length < 3 || value.length > 300 || (detail.length > 0 && detail.length < 2) || detail.length > 600 || /https?:\/\/|www\.|[\x00-\x1f]/i.test(value + detail)) { validation.value = '主题需 3–300 字，关注点可留空或填 2–600 字；不接受 URL 或控制字符。'; return }
 draft.value = { topic: value, ...(detail ? { focus: detail } : {}), ...(linkedId.value ? { event_id: linkedId.value } : {}) }
}
async function submit() {
 if (pending.value || !draft.value) return
 pending.value = true; const request = { ...draft.value }
 try {
  const accepted = await api.createResearch(request)
  if (!validTaskId(accepted.task_id)) throw new ApiError(202, 'invalid_response', true)
  acceptedId.value = accepted.task_id; rememberTask(accepted.task_id)
  await router.push(`/research/${accepted.task_id}`)
 } catch (err) { error.value = err; uncertain.value = err instanceof ApiError && err.uncertain }
 finally { pending.value = false; draft.value = null }
}
function lookup() { lookupError.value = ''; const id = lookupId.value.trim(); if (!validTaskId(id)) { lookupError.value = '请输入完整的 UUID 任务 ID。'; return }; void router.push(`/research/${id}`) }
</script>
<template>
 <div class="page-intro research-intro"><div class="row"><span class="icon-box"><FlaskConical :size="25" /></span><div><p class="eyebrow">AIPULSE · DEEP RESEARCH</p><h2>让每一步研究，都有证据。</h2><p>基于真实材料，按有界流程规划、检索、验证并生成可追溯报告。</p></div></div></div>
 <div class="research-create"><div class="stack"><section class="panel research-compose"><div class="row"><span class="icon-box blue"><FlaskConical :size="21" /></span><div><h2>从一个好问题开始</h2><p class="muted text-xs">有界研究 · 真实证据 · 可追溯结论</p></div></div><form class="research-form" @submit.prevent="prepare"><div class="field"><label for="research-topic">研究主题 <span class="muted">必填</span></label><input id="research-topic" v-model="topic" placeholder="例如：Agent 的状态持久化与条件路由" minlength="3" maxlength="300" required :disabled="pending" /><small>{{ topic.length }} / 300 字</small></div><div class="field"><label for="research-focus">关注重点 <span class="muted">选填</span></label><textarea id="research-focus" v-model="focus" placeholder="希望了解哪些机制、公开证据或适用局限？" maxlength="600" :disabled="pending"></textarea><small>{{ focus.length }} / 600 字</small></div><p v-if="linkedId" class="badge neutral">关联新闻事件 #{{ linkedId }}</p><p v-if="validation" class="field-error" role="alert">{{ validation }}</p><div class="notice info" style="margin:0"><strong>按需执行，可能产生费用</strong>研究最多调用 2 次 DeepSeek；生成输出累计上限 7,144 Token，输入另计。任务最多 360 秒，失败或取消也可能计费。后端默认关闭，启用后才能执行。</div><p v-if="error" class="form-error" role="alert">{{ errorMessage(error) }}</p><div v-if="uncertain" class="notice warning">请先在下方输入已有任务 ID 查询，或核对后端记录。不会自动重新提交。<button type="button" class="button secondary" style="margin-top:10px" @click="uncertain = false">已核对，返回编辑</button></div><RouterLink v-if="acceptedId" :to="`/research/${acceptedId}`" class="section-link">查看已创建任务 {{ acceptedId }} <ArrowRight :size="15" /></RouterLink><div class="form-actions"><span class="muted text-xs row"><ShieldCheck :size="14" />提交前还需确认一次</span><button type="submit" class="button primary" data-testid="prepare-research" :disabled="pending || uncertain || !!acceptedId"><FlaskConical :size="16" />{{ pending ? '正在提交…' : '创建研究任务' }}</button></div></form></section>
 <section class="panel research-lookup"><h2>通过任务 ID 查看</h2><p class="muted text-xs" style="margin-top:7px">读取已有任务、证据与报告，不创建任务，不产生模型费用。</p><form class="lookup" @submit.prevent="lookup"><div class="field"><label for="task-lookup">任务 ID</label><input id="task-lookup" v-model="lookupId" class="mono" placeholder="完整 UUID" autocomplete="off" required /></div><button type="submit" class="button secondary"><Search :size="16" />查看任务</button></form><p v-if="lookupError" class="form-error" role="alert">{{ lookupError }}</p></section></div>
 <aside class="stack"><section class="quick-card"><p class="eyebrow">RESEARCH BOUNDARIES</p><h3>每次研究，都有边界</h3><ul class="budget-list"><li><span>模型调用</span><strong>最多 2 次</strong></li><li><span>检索轮数</span><strong>最多 3 轮</strong></li><li><span>真实证据</span><strong>最多 18 条</strong></li><li><span>任务时限</span><strong>360 秒</strong></li></ul><p>只依据获取的材料形成判断。证据不足会保留缺口；不会把模型内部知识当作已核实来源。</p></section><section class="quick-card"><div class="between"><h3>本浏览器最近访问</h3><button v-if="recentTasks.length" class="icon-button" type="button" aria-label="清除本地访问历史" @click="clearHistory"><Trash2 :size="15" /></button></div><p>仅保存最近 10 个任务 ID，并非数据库中的全部任务列表。</p><TaskHistoryList :limit="3" /></section></aside></div>
 <ConfirmDialog :open="!!draft" :busy="pending" title="确认创建一次付费研究？" :message="`主题：${draft?.topic ?? ''}。这将创建新任务并写入数据库，最多 2 次模型调用、3 轮检索、18 条证据、360 秒。不会自动重试。功能关闭时后端会拒绝；即使失败或取消也可能产生费用。`" confirm-label="确认创建一次" @confirm="submit" @cancel="draft = null" />
</template>
