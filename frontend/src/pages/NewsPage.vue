<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronLeft, ChevronRight, SlidersHorizontal } from '@lucide/vue'
import { api } from '../api/client'
import type { NewsPage } from '../api/types'
import { useResource } from '../composables/useResource'
import { useNewsDetails } from '../composables/useNewsDetails'
import { categories, dateTime } from '../lib/format'
import NewsCard from '../components/NewsCard.vue'
import UiState from '../components/UiState.vue'
const route = useRoute(), router = useRouter(), resource = useResource<NewsPage>()
const context = useNewsDetails()
watch(() => resource.data.value, page => { if (page) void context.load(page.items) })
const category = ref(''), minimum = ref(''), sort = ref('score')
const page = computed(() => Math.max(1, Math.floor(Number(route.query.page) || 1)))
const pages = computed(() => Math.max(1, Math.ceil((resource.data.value?.total ?? 0) / 10)))
function read() {
 category.value = typeof route.query.category === 'string' && route.query.category in categories ? route.query.category : ''
 minimum.value = ['0', '30', '50', '70', '90'].includes(String(route.query.min_score)) ? String(route.query.min_score) : ''
 sort.value = route.query.sort === 'latest' ? 'latest' : 'score'
 const params = new URLSearchParams({ page: String(page.value), page_size: '10', sort: sort.value })
 if (category.value) params.set('category', category.value)
 if (minimum.value) params.set('min_score', minimum.value)
 void resource.load(signal => api.news(params, signal))
}
function apply() { void router.replace({ path: '/news', query: { ...(category.value ? { category: category.value } : {}), ...(minimum.value ? { min_score: minimum.value } : {}), sort: sort.value } }) }
function move(next: number) { void router.push({ query: { ...route.query, page: String(next) } }) }
watch(() => route.query, read, { immediate: true })
</script>
<template>
 <div class="page-intro"><div><h2>发现值得关注的变化</h2><p>从发布、论文与社区讨论中，阅读已保存的真实信息。摘要、摘录和评分依据分别标注。</p></div><span class="intro-tag">NEWS INTELLIGENCE</span></div>
 <form class="filters panel" @submit.prevent="apply"><div class="field"><label for="news-category">新闻分类</label><select id="news-category" v-model="category"><option value="">全部分类</option><option v-for="(label, key) in categories" :key="key" :value="key">{{ label }}</option></select></div><div class="field"><label for="news-score">最低重要性评分</label><select id="news-score" v-model="minimum"><option value="">不限评分</option><option v-for="score in [0, 30, 50, 70, 90]" :key="score" :value="String(score)">{{ score }} 分及以上</option></select></div><div class="field"><label for="news-sort">排序方式</label><select id="news-sort" v-model="sort"><option value="score">重要性优先</option><option value="latest">最新更新</option></select></div><button class="button primary" type="submit"><SlidersHorizontal :size="16" />应用筛选</button></form>
 <UiState v-if="resource.loading.value" kind="loading" /><UiState v-else-if="resource.error.value" kind="error" :error="resource.error.value" retry @retry="read" /><template v-else-if="resource.data.value"><div class="section-heading"><p class="muted text-xs">当前条件匹配 {{ resource.data.value.total }} 个事件 · 已加载 {{ resource.data.value.items.length }} 个</p><span class="muted text-xs">读取于 {{ dateTime(resource.readAt.value) }}</span></div><div v-if="resource.data.value.items.length" class="news-grid"><NewsCard v-for="event in resource.data.value.items" :key="event.event_id" :event="event" :detail="context.details.value[event.event_id]" /></div><UiState v-else kind="empty" title="没有匹配的新闻" detail="尝试调整分类或最低评分，也可以返回第一页查看。" />
 <nav class="pagination" aria-label="新闻分页"><span>第 {{ page }} / {{ pages }} 页 · 每页最多 10 条</span><div class="pagination-actions"><button class="button secondary" type="button" :disabled="page <= 1" @click="move(1)">首页</button><button class="icon-button" aria-label="上一页" :disabled="page <= 1" @click="move(page - 1)"><ChevronLeft :size="18" /></button><button class="icon-button" aria-label="下一页" :disabled="page >= pages" @click="move(page + 1)"><ChevronRight :size="18" /></button></div></nav></template>
</template>
