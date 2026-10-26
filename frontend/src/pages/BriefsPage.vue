<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CalendarDays, FileText, Sparkles } from '@lucide/vue'
import { api, ApiError, errorMessage } from '../api/client'
import type { DailyBrief } from '../api/types'
import { useResource } from '../composables/useResource'
import { categories, dateTime, shanghaiDate } from '../lib/format'
import UiState from '../components/UiState.vue'
import SourceLink from '../components/SourceLink.vue'
import ScoreBadge from '../components/ScoreBadge.vue'
import UsageNote from '../components/UsageNote.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
const route = useRoute(), router = useRouter(), resource = useResource<DailyBrief>()
const date = ref(''), confirm = ref(false), pending = ref(false), generationError = ref<unknown>(null)
const selected = computed(() => typeof route.query.date === 'string' ? route.query.date : shanghaiDate())
const valid = computed(() => /^\d{4}-\d{2}-\d{2}$/.test(selected.value) && Number.isFinite(Date.parse(selected.value)) && new Date(selected.value).toISOString().slice(0, 10) === selected.value)
const missing = computed(() => resource.error.value instanceof ApiError && resource.error.value.status === 404)
const sections = computed(() => resource.data.value ? [ { key: 'model', title: '模型与产品动态', items: resource.data.value.model_and_products }, { key: 'tools', title: 'AI 工具与框架', items: resource.data.value.tools_and_frameworks }, { key: 'research', title: '研究论文', items: resource.data.value.research }, { key: 'trends', title: '趋势观察', items: resource.data.value.trends } ] : [])
function read() { date.value = selected.value; generationError.value = null; if (valid.value) void resource.load(signal => api.brief(selected.value, signal)) }
watch(selected, () => { confirm.value = false; read() }, { immediate: true })
function choose() { if (date.value === selected.value) read(); else void router.replace({ path: '/briefs', query: { date: date.value } }) }
async function generate() {
 if (pending.value || !confirm.value) return
 pending.value = true; generationError.value = null; const target = selected.value
 try { const result = await api.generateBrief(target); if (target === selected.value) { resource.data.value = result; resource.error.value = null } }
 catch (error) { if (target === selected.value) generationError.value = error }
 finally { pending.value = false; confirm.value = false }
}
</script>
<template>
 <form class="panel filters brief-controls" @submit.prevent="choose"><div class="field" style="max-width:230px"><label for="brief-date">选择日报日期</label><input id="brief-date" v-model="date" type="date" required /></div><button type="submit" class="button primary"><CalendarDays :size="16" />读取日报</button><span class="muted text-xs" style="align-self:center">日期口径：Asia/Shanghai · 只读缓存</span><button type="button" class="button secondary" style="margin-left:auto" :disabled="!valid || pending || resource.loading.value" @click="confirm = true"><Sparkles :size="15" />{{ pending ? '正在生成…' : '生成 / 更新日报' }}</button></form>
 <p v-if="generationError" class="notice error" role="alert">{{ errorMessage(generationError) }}</p>
 <UiState v-if="!valid" kind="error" title="日期格式不正确" detail="请选择有效的 YYYY-MM-DD 日历日期。" /><UiState v-else-if="resource.loading.value" kind="loading" title="正在读取日报缓存" /><UiState v-else-if="missing" kind="empty" title="这一天还没有已保存日报" detail="可以选择其他日期。页面不会自动生成内容；生成操作需要单独确认并可能产生费用。" /><UiState v-else-if="resource.error.value" kind="error" :error="resource.error.value" retry @retry="read" /><UiState v-else-if="resource.data.value?.status === 'empty'" kind="empty" title="当天没有符合筛选条件的新闻" detail="后端返回空结果，未生成日报。不会用其他日期或模拟内容代替。" />
 <div v-else-if="resource.data.value" class="brief-document"><div v-if="resource.data.value.rules_current === false" class="notice warning"><strong>旧版本日报缓存</strong>这份日报按历史筛选与 Prompt 规则生成，尚未按当前规则重新生成。可能包含与 AI 主题相关性不足的旧内容。</div><div v-else-if="resource.data.value.rules_current === undefined" class="notice info">此响应未提供规则版本状态，不将它标记为最新规则生成的日报。</div><article class="panel brief-cover"><div class="between"><p class="eyebrow">AIPULSE DAILY BRIEF</p><span class="badge" :class="resource.data.value.cached ? 'green' : 'blue'">{{ resource.data.value.cached ? '已保存缓存' : '本次生成' }}</span></div><h2>{{ resource.data.value.brief_date }} · AI 情报简报</h2><p class="muted text-xs" style="margin-bottom:22px">生成于 {{ dateTime(resource.data.value.generated_at) }} · {{ resource.data.value.timezone }}</p><p class="overview">{{ resource.data.value.overview || '此份日报未提供动态概览。' }}</p><UsageNote :usage="resource.data.value.token_usage" :model="resource.data.value.model" :latency-ms="resource.data.value.latency_ms" /></article>
 <section class="panel brief-highlights"><h2 class="row"><FileText :size="19" style="color:var(--brand)" />重点新闻<span class="badge neutral">{{ resource.data.value.highlights.length }} 条</span></h2><article v-for="(item, index) in resource.data.value.highlights" :key="item.event_id" class="brief-highlight"><span class="brief-number">{{ String(index + 1).padStart(2, '0') }}</span><div style="min-width:0;flex:1"><div class="between"><span class="badge category">{{ categories[item.category ?? ''] ?? '未分类' }}</span><ScoreBadge :score="item.importance_score" /></div><h3 style="margin-top:9px"><RouterLink :to="`/news/${item.event_id}`">{{ item.title }}</RouterLink></h3><p>{{ item.summary_zh }}</p><div class="row"><SourceLink v-for="url in item.sources" :key="url" :url="url" /></div></div></article></section>
 <div class="brief-sections"><section v-for="section in sections" :key="section.key" class="panel brief-section"><h2>{{ section.title }}</h2><ul v-if="section.items.length"><li v-for="(item, index) in section.items" :key="index">{{ item }}</li></ul><p v-else class="muted text-xs" style="margin-top:13px">本份日报没有可展示的相关内容，栏目保持为空。</p></section></div><section class="panel brief-limitations"><h3>信息局限与阅读说明</h3><p class="article-copy" style="font-size:13px;margin-top:12px">{{ resource.data.value.limitations || '后端未提供额外局限说明，请对照原始来源核验。' }}</p></section></div>
 <ConfirmDialog :open="confirm" :busy="pending" :title="`确认生成 ${selected} 的日报？`" message="这会发送一次 POST，优先检查有效缓存；未命中时最多调用一次付费模型，并保存日报。筛选最多 5 条事件。功能由后端开关控制，不会自动重试；失败也可能产生费用。" @confirm="generate" @cancel="confirm = false" />
</template>
