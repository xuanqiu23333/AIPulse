<script setup lang="ts">
import { ArrowRight, Clock3, FileText, GitBranch, MessageSquare } from '@lucide/vue'
import type { NewsDetail, NewsEvent } from '../api/types'
import { categories, dateTime, providers } from '../lib/format'
import ScoreBadge from './ScoreBadge.vue'
import SourceLink from './SourceLink.vue'
defineProps<{ event: NewsEvent; compact?: boolean; detail?: NewsDetail }>()
const marks: Record<string, string> = { github: 'GitHub', arxiv: 'arXiv', hn: 'HN' }
</script>
<template>
 <article class="news-card" :class="{ compact }">
  <div class="news-top"><span class="badge category" :class="`category-${event.category || 'other'}`">{{ categories[event.category ?? ''] ?? '未分类' }}</span><span class="provider-row"><span v-for="source in event.providers" :key="source" class="provider" :class="source">{{ providers[source] ?? source }}</span><span v-if="!event.providers.length" class="muted text-xs">来源待补充</span></span><ScoreBadge :score="event.importance_score" /></div>
  <div class="news-body"><div v-if="!compact" class="source-mark" :class="event.providers[0]" aria-hidden="true"><component :is="event.providers[0] === 'github' ? GitBranch : event.providers[0] === 'arxiv' ? FileText : MessageSquare" :size="28" /><span>{{ marks[event.providers[0] || ''] || 'NEWS' }}</span><i></i></div><div class="news-copy"><h3><RouterLink :to="`/news/${event.event_id}`">{{ event.title }}</RouterLink></h3><template v-if="!compact"><p v-if="detail?.summary" class="news-excerpt"><span>已存事件摘录</span>{{ detail.summary }}</p><p v-else class="news-excerpt unavailable">当前未提供正文摘录，请对照来源与详情阅读。</p></template></div></div>
  <details v-if="!compact && event.score_reason" class="score-explanation"><summary>评分依据</summary><p>{{ event.score_reason }}</p></details>
  <div class="news-footer"><span class="news-time"><Clock3 :size="12" aria-hidden="true" />{{ detail?.sources.find(source => source.published_at)?.published_at ? '发布' : '更新' }} {{ dateTime(detail?.sources.find(source => source.published_at)?.published_at || event.updated_at) }}</span><span class="news-source-count">{{ event.item_count }} 条来源</span></div>
  <div v-if="!compact" class="news-actions"><SourceLink v-if="detail?.sources[0]" :url="detail.sources[0].original_url" label="原始来源" /><span v-else class="muted text-xs">来源见详情</span><RouterLink :to="`/news/${event.event_id}`" class="section-link">查看详情 <ArrowRight :size="13" /></RouterLink></div>
 </article>
</template>
