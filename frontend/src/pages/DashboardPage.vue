<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowRight, ArrowUpRight, CalendarDays, Database, FlaskConical, Layers3, Radio, RefreshCw, Sparkles, TrendingUp } from '@lucide/vue'
import { api } from '../api/client'
import type { DailyBrief, NewsPage } from '../api/types'
import { useResource } from '../composables/useResource'
import { useNewsDetails } from '../composables/useNewsDetails'
import { briefCandidates, findSavedBrief } from '../lib/overview'
import { categories, dateTime, shanghaiDate } from '../lib/format'
import NewsCard from '../components/NewsCard.vue'
import ScoreBadge from '../components/ScoreBadge.vue'
import TaskHistoryList from '../components/TaskHistoryList.vue'
import IntelligenceIllustration from '../components/IntelligenceIllustration.vue'
import UiState from '../components/UiState.vue'
const latest = useResource<NewsPage>(), ranked = useResource<NewsPage>(), brief = useResource<DailyBrief | null>()
const context = useNewsDetails(), sort = ref('latest'), category = ref('')
const sourceCount = computed(() => new Set((latest.data.value?.items ?? []).flatMap(event => event.providers)).size)
const priority = computed(() => (ranked.data.value?.items ?? []).filter(item => ['model', 'paper', 'tool', 'product'].includes(item.category ?? '')).slice(0, 5))
const visibleNews = computed(() => (latest.data.value?.items ?? []).filter(item => !category.value || item.category === category.value))
const datesChecked = ref<string[]>([])
watch(() => latest.data.value, page => { if (!page) return; void context.load(page.items); datesChecked.value = briefCandidates(page.items, shanghaiDate()); void brief.load(signal => findSavedBrief(datesChecked.value, signal)) })
function refresh() { void latest.load(signal => api.news(new URLSearchParams({ sort: sort.value, page_size: '6' }), signal)); void ranked.load(signal => api.news(new URLSearchParams({ sort: 'score', page_size: '20' }), signal)) }
onMounted(refresh)
</script>
<template>
 <div class="dashboard-workspace">
  <div class="dashboard-main">
   <section class="hero"><div class="hero-copy"><p class="eyebrow">AIPULSE · AI INTELLIGENCE</p><h2>把信息变成<span>判断。</span></h2><p class="hero-description">追踪 AI 领域的发布、论文与社区动态。<br class="desktop-only" />阅读真实来源，在需要时展开更深一步的研究。</p><div class="hero-actions"><RouterLink to="/news" class="button primary">探索最新情报 <ArrowRight :size="16" /></RouterLink><RouterLink to="/research" class="button secondary"><FlaskConical :size="16" />开始深度研究</RouterLink></div></div><IntelligenceIllustration /></section>
   <div class="stat-grid" aria-label="实际数据库查询与当前加载范围统计">
    <div class="stat-card"><span class="icon-box blue"><Database :size="22" /></span><div><span>已保存事件</span><strong>{{ latest.data.value?.total ?? '—' }}<small> 条</small></strong><small>当前开发库 · 全部分类</small></div></div>
    <div class="stat-card"><span class="icon-box violet"><Radio :size="22" /></span><div><span>本页来源</span><strong>{{ latest.data.value ? sourceCount : '—' }}<small> 类</small></strong><small>仅本次加载的 {{ latest.data.value?.items.length ?? 0 }} 条</small></div></div>
    <div class="stat-card"><span class="icon-box amber"><Layers3 :size="22" /></span><div><span>本页重点</span><strong>{{ ranked.data.value ? priority.length : '—' }}<small> 条</small></strong><small>高分候选中的模型/论文等</small></div></div>
    <RouterLink :to="`/briefs?date=${brief.data.value?.brief_date || shanghaiDate()}`" class="stat-card stat-link"><span class="icon-box green"><CalendarDays :size="22" /></span><div><span>可读简报</span><strong class="stat-date">{{ latest.error.value || brief.error.value ? '暂不可读' : brief.loading.value || latest.loading.value ? '读取中' : brief.data.value ? brief.data.value.brief_date.slice(5).replace('-', '.') : '未找到' }}</strong><small>{{ latest.error.value ? '新闻读取失败，无法定位日期' : '仅当前检查日期 · 非总量' }}</small></div></RouterLink>
   </div>
   <section class="news-feed"><div class="section-heading"><div><p class="eyebrow">LATEST INTELLIGENCE</p><h2>最新情报</h2><p>来自已保存的真实来源 · 读取于 {{ dateTime(latest.readAt.value) }}</p></div><div class="feed-operations"><label class="sr-only" for="home-sort">首页新闻排序</label><select id="home-sort" v-model="sort" @change="refresh"><option value="latest">最新更新</option><option value="score">重要性优先</option></select><button type="button" class="icon-button" aria-label="刷新首页数据" :disabled="latest.loading.value || ranked.loading.value" @click="refresh"><RefreshCw :size="17" :class="{ spin: latest.loading.value }" /></button></div></div>
    <nav class="category-tabs" aria-label="筛选首页已加载新闻"><button type="button" :class="{ active: category === '' }" :aria-pressed="category === ''" @click="category = ''">全部</button><button v-for="(label, key) in categories" :key="key" type="button" :class="{ active: category === key }" :aria-pressed="category === key" @click="category = key">{{ label }}</button></nav><p class="feed-scope">分类仅筛选本页 {{ latest.data.value?.items.length ?? 0 }} 条；<RouterLink to="/news">前往新闻中心查询全部事件 <ArrowUpRight :size="12" /></RouterLink></p>
    <UiState v-if="latest.loading.value" kind="loading" /><UiState v-else-if="latest.error.value" kind="error" :error="latest.error.value" retry @retry="refresh" /><UiState v-else-if="!visibleNews.length" kind="empty" title="本页暂无这类资讯" detail="可调整本页分类，或进入新闻中心查询全部已保存记录。不会用模拟新闻补齐。" /><div v-else class="news-grid"><NewsCard v-for="event in visibleNews" :key="event.event_id" :event="event" :detail="context.details.value[event.event_id]" /></div><RouterLink to="/news" class="feed-more">进入完整新闻中心 <ArrowRight :size="15" /></RouterLink>
   </section>
  </div>
  <aside class="dashboard-rail">
   <section class="rail-card"><div class="rail-heading"><span class="icon-box blue"><CalendarDays :size="24" /></span><div><h2>每日情报简报</h2><p>已保存材料，随时回看</p></div><RouterLink :to="`/briefs?date=${brief.data.value?.brief_date || shanghaiDate()}`" class="icon-button soft" aria-label="打开日报页面"><ArrowUpRight :size="17" /></RouterLink></div><p v-if="brief.loading.value || latest.loading.value" class="rail-empty" role="status">正在查找已保存日报…</p><p v-else-if="brief.error.value || latest.error.value" class="rail-empty error-text">日报暂不可读取，请到日报页面重试。</p><template v-else-if="brief.data.value"><div class="rail-brief-meta"><strong>{{ brief.data.value.brief_date }}</strong><span v-if="brief.data.value.rules_current === false" class="badge amber">旧规则缓存</span><span v-else-if="brief.data.value.rules_current === true" class="badge green">当前规则</span><span v-else class="badge neutral">规则状态未提供</span></div><ol class="brief-preview"><li v-for="(item, index) in brief.data.value.highlights.slice(0, 3)" :key="item.event_id"><span>{{ index + 1 }}</span><RouterLink :to="`/news/${item.event_id}`">{{ item.title }}</RouterLink></li></ol></template><p v-else class="rail-empty">已检查日期内暂无有效日报，可前往日报页选择其他日期。不会自动生成。</p><RouterLink :to="`/briefs?date=${brief.data.value?.brief_date || shanghaiDate()}`" class="rail-action">{{ brief.data.value ? '阅读完整简报' : '按日期读取日报' }} <ArrowRight :size="15" /></RouterLink><p class="rail-disclaimer">{{ datesChecked.length ? `仅检查 ${datesChecked.join('、')}，最多 3 日；不是全部日报列表。` : '新闻暂不可读，尚未检查日报日期。' }}</p></section>
   <section class="rail-card"><div class="rail-heading"><span class="icon-box cyan"><TrendingUp :size="24" /></span><div><h2>重点情报</h2><p>已存重要性评分，无虚构趋势</p></div><RouterLink to="/news?sort=score" class="icon-button soft" aria-label="按重要性查看新闻"><ArrowRight :size="17" /></RouterLink></div><UiState v-if="ranked.error.value" kind="error" :error="ranked.error.value" /><p v-else-if="ranked.loading.value" class="rail-empty">正在读取高分新闻…</p><ol v-else-if="priority.length" class="priority-list"><li v-for="(event, index) in priority" :key="event.event_id"><span class="priority-rank">{{ index + 1 }}</span><div><RouterLink :to="`/news/${event.event_id}`">{{ event.title }}</RouterLink><small>{{ event.providers.join(' / ') }} · {{ categories[event.category || ''] || '未分类' }}</small></div><ScoreBadge :score="event.importance_score" /></li></ol><p v-else class="rail-empty">当前没有可展示的重点情报。</p><p class="rail-disclaimer">{{ ranked.data.value ? `从评分前 ${ranked.data.value.items.length} 条中选模型、论文、工具、产品，最多 5 条；未另作主题核验。` : '高分候选暂不可读。' }}</p></section>
   <section class="rail-card research-rail"><div class="rail-heading"><span class="icon-box blue"><FlaskConical :size="24" /></span><div><h2>Deep Research</h2><p>让判断有据可循</p></div><Sparkles :size="19" class="muted" /></div><TaskHistoryList :limit="1" /><RouterLink to="/research" class="rail-action">打开研究工作台 <ArrowRight :size="15" /></RouterLink><p class="rail-disclaimer">仅展示本浏览器最近访问的任务。新任务需二次确认，默认关闭付费执行。</p></section>
  </aside>
 </div>
</template>
