<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, FlaskConical, Sparkles } from '@lucide/vue'
import { api, ApiError, errorMessage } from '../api/client'
import type { NewsDetail, NewsSummary } from '../api/types'
import { useResource } from '../composables/useResource'
import { categories, dateTime, providers } from '../lib/format'
import UiState from '../components/UiState.vue'
import ScoreBadge from '../components/ScoreBadge.vue'
import SourceLink from '../components/SourceLink.vue'
import UsageNote from '../components/UsageNote.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
const route = useRoute(), detail = useResource<NewsDetail>(), summary = useResource<NewsSummary>()
const id = computed(() => Number(route.params.eventId)), valid = computed(() => Number.isSafeInteger(id.value) && id.value > 0)
const confirm = ref(false), pending = ref(false), generationError = ref<unknown>(null)
const cacheMissing = computed(() => summary.error.value instanceof ApiError && summary.error.value.status === 404)
function read() { if (!valid.value) return; generationError.value = null; void detail.load(signal => api.detail(id.value, signal)); void summary.load(signal => api.summary(id.value, signal)) }
watch(id, () => { confirm.value = false; read() }, { immediate: true })
async function generate() {
 if (pending.value || !confirm.value) return
 pending.value = true; generationError.value = null
 const target = id.value
 try { const result = await api.generateSummary(target); if (target === id.value) { summary.data.value = result; summary.error.value = null } }
 catch (error) { if (target === id.value) generationError.value = error }
 finally { pending.value = false; confirm.value = false }
}
</script>
<template>
 <RouterLink to="/news" class="section-link" style="margin-bottom:20px"><ArrowLeft :size="15" />返回新闻中心</RouterLink>
 <UiState v-if="!valid" kind="error" title="新闻编号格式不正确" detail="请从新闻列表打开有效记录。" /><UiState v-else-if="detail.loading.value" kind="loading" /><UiState v-else-if="detail.error.value" kind="error" :error="detail.error.value" retry @retry="read" />
 <template v-else-if="detail.data.value"><div class="detail-columns"><div class="stack"><article class="panel article-panel"><p class="article-kicker">SOURCE INTELLIGENCE / 已保存新闻</p><div class="row"><span class="badge category">{{ categories[detail.data.value.category ?? ''] ?? '未分类' }}</span><span v-for="source in detail.data.value.providers" :key="source" class="provider" :class="source">{{ providers[source] ?? source }}</span></div><h2 class="article-title">{{ detail.data.value.title }}</h2><div class="row muted text-xs article-meta"><span>更新于 {{ dateTime(detail.data.value.updated_at) }}</span><span>事件 #{{ id }}</span></div><hr /><h3 style="margin-bottom:12px">已存事件摘录</h3><p v-if="detail.data.value.summary" class="article-copy pre-wrap">{{ detail.data.value.summary }}</p><p v-else class="article-copy muted reading-rule">当前事件未提供可展示的正文摘录。以下摘要和来源仅依据后端已保存材料；本页面不会额外抓取网页全文。</p></article>
  <section class="panel ai-summary-panel"><div class="section-heading"><div><h2 class="row"><Sparkles :size="19" style="color:var(--brand)" />AI 中文摘要</h2><p>优先读取已保存缓存，阅读不会调用模型。</p></div><button type="button" class="button secondary" :disabled="pending || summary.loading.value" @click="confirm = true">{{ pending ? '正在生成…' : '生成 / 更新' }}</button></div><UiState v-if="summary.loading.value" kind="loading" title="正在读取摘要缓存" /><template v-else-if="summary.data.value"><span class="badge" :class="summary.data.value.cached ? 'green' : 'blue'">{{ summary.data.value.cached ? '缓存命中' : '本次生成' }}</span><p class="article-copy" style="margin-top:17px">{{ summary.data.value.summary_zh }}</p><h3 style="margin-top:22px">核心要点</h3><ul class="article-copy"><li v-for="point in summary.data.value.key_points" :key="point">{{ point }}</li></ul><div class="notice info" style="margin-top:18px"><strong>信息局限</strong>{{ summary.data.value.limitations }}</div><div class="row"><SourceLink v-for="url in summary.data.value.sources" :key="url" :url="url" /></div><UsageNote :usage="summary.data.value.token_usage" :model="summary.data.value.model" :latency-ms="summary.data.value.latency_ms" /></template><UiState v-else-if="cacheMissing" kind="empty" title="暂无有效摘要缓存" detail="你仍可以阅读原始新闻来源。生成摘要需要主动确认，并可能产生模型费用。" /><UiState v-else-if="summary.error.value" kind="error" :error="summary.error.value" /><p v-if="generationError" class="form-error" role="alert">{{ errorMessage(generationError) }}</p></section>
  </div><aside class="stack"><section class="panel score-panel"><div class="between"><h3>重要性评分</h3><ScoreBadge :score="detail.data.value.importance_score" /></div><p class="article-copy" style="font-size:12px;margin-top:15px">{{ detail.data.value.score_reason || '暂无评分理由。' }}</p><dl class="side-meta"><dt>分类 / 评分版本</dt><dd>{{ detail.data.value.rule_version ?? '未提供' }}</dd><dt>事件状态</dt><dd>{{ detail.data.value.status }}</dd><dt>来源记录</dt><dd>{{ detail.data.value.item_count }} 条</dd></dl></section><section class="panel"><h3>原始新闻来源</h3><div v-for="source in detail.data.value.sources" :key="`${source.provider}:${source.external_id}`" class="source-record"><span class="provider" :class="source.provider">{{ providers[source.provider] ?? source.provider }}</span><h3>{{ source.title }}</h3><p class="muted text-xs">{{ source.source_name }}</p><p class="muted text-xs">发布 {{ dateTime(source.published_at) }}</p><SourceLink :url="source.original_url" label="查看原始来源" /></div><p v-if="!detail.data.value.sources.length" class="muted" style="margin-top:14px">暂无可用来源记录，具体内容尚无法核验。</p></section><RouterLink :to="{ path: '/research', query: { event_id: id, topic: detail.data.value.title } }" class="button secondary"><FlaskConical :size="16" />以此新闻发起研究</RouterLink></aside></div></template>
 <ConfirmDialog :open="confirm" :busy="pending" title="确认生成这条新闻的摘要？" message="这会发送一次 POST。命中有效缓存时复用缓存，否则最多调用一次付费模型并保存结果。功能由后端开关控制；提交后不会自动重试，失败也可能产生费用。" @confirm="generate" @cancel="confirm = false" />
</template>
