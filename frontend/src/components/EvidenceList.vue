<script setup lang="ts">
import type { Evidence } from '../api/types'
import { dateTime, providers } from '../lib/format'
import SourceLink from './SourceLink.vue'
defineProps<{ evidence: Evidence[] }>()
const coverage: Record<string, string> = { abstract: '论文摘要', excerpt: '已存摘录', metadata: '项目自述元数据', title_only: '仅标题' }
</script>
<template><section id="evidence" class="panel evidence-panel"><div class="section-heading"><div><p class="eyebrow">EVIDENCE LIBRARY</p><h2>研究证据 <span class="badge neutral">{{ evidence.length }} 条</span></h2></div></div><p class="muted text-xs">来自真实检索或已有新闻记录。片段覆盖有限，不表示已阅读全文或完成独立验证。</p><p v-if="!evidence.length" class="muted" style="margin-top:20px">该任务尚无可读取的已保存证据。</p><article v-for="item in evidence" :id="`evidence-${item.citation}`" :key="item.citation" class="evidence-card" tabindex="-1"><div class="row"><span class="badge blue mono">{{ item.citation }}</span><span class="provider" :class="item.source_type">{{ providers[item.source_type] ?? item.source_type }}</span><span class="badge neutral">{{ coverage[item.coverage] ?? item.coverage }}</span></div><h3>{{ item.title }}</h3><SourceLink :url="item.source_url" /><p style="margin-top:13px">{{ item.content || '仅保存了标题，不能据此推断具体事实。' }}</p><details><summary>证据局限与元数据</summary><p>{{ item.quality_note }}</p><p>{{ item.relevance_note }}</p><p>获取时间：{{ dateTime(item.retrieved_at) }}<br />发布时间：{{ dateTime(item.published_at) }}<br />关联子问题：{{ item.question_ids.join('、') || '未提供' }}</p></details></article></section></template>
