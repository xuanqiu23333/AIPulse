<script setup lang="ts">
import { computed } from 'vue'
import type { ResearchReport } from '../api/types'
import { errorLabels } from '../api/client'
import StatusBadge from './StatusBadge.vue'
import ClaimBlock from './ClaimBlock.vue'
import SourceLink from './SourceLink.vue'
import UsageNote from './UsageNote.vue'
import ResearchIssueNotice from './ResearchIssueNotice.vue'
const props = defineProps<{ report: ResearchReport; evidenceIds: string[]; taskFailed?: boolean; taskError?: string | null }>()
const failed = computed(() => props.taskFailed || props.report.status === 'failed')
const code = computed(() => props.report.error_code || props.taskError || 'research_failed')
</script>
<template><section id="report" class="panel report-panel"><div class="section-heading"><div><p class="eyebrow">RESEARCH REPORT</p><h2>{{ failed ? '研究未完成' : '研究报告' }}</h2></div><StatusBadge :status="failed ? 'failed' : report.status" /></div>
 <div v-if="failed" class="notice error" role="alert" data-testid="failed-report"><strong>报告生成失败</strong>{{ errorLabels[code] ?? '这次研究没有生成可用的完整报告，请查看任务状态与信息局限。' }}<br /><code>{{ code }}</code><p>已保存的证据仍可查看。HTTP 200 仅表示读取成功，不表示研究成功。</p></div>
 <div v-else><ResearchIssueNotice v-for="warning in (report.source_warnings ?? []).filter(code => code !== taskError)" :key="warning" :status="report.status" :code="warning" /><div v-if="report.status === 'partial'" class="notice warning">这是一份部分完成的报告，存在证据缺口或步骤失败。请结合局限阅读。</div><section class="report-section"><h3>研究概览</h3><ClaimBlock v-for="(claim, index) in report.overview" :key="index" :claim="claim" :evidence-ids="evidenceIds" /><p v-if="!report.overview.length" class="muted text-xs">暂无通过证据校验的概览。</p></section><section class="report-section"><h3>核心结论</h3><ClaimBlock v-for="(claim, index) in report.conclusions" :key="index" :claim="claim" :evidence-ids="evidenceIds" /><p v-if="!report.conclusions.length" class="muted text-xs">当前材料不足以形成通过引用校验的核心结论；已验证的概览和证据仍可阅读。</p></section><section v-for="section in report.analyses" :key="section.question_id" class="report-section"><h3><span class="badge neutral">{{ section.question_id }}</span> {{ section.question }}</h3><ClaimBlock v-for="(claim, index) in section.claims" :key="index" :claim="claim" :evidence-ids="evidenceIds" /><p v-if="!section.claims.length" class="muted text-xs">当前证据不足以形成具体判断。</p></section><section v-if="report.trends.length" class="report-section"><h3>趋势观察</h3><ClaimBlock v-for="(claim, index) in report.trends" :key="index" :claim="claim" :evidence-ids="evidenceIds" /></section></div>
 <section class="report-section"><h3>研究局限</h3><ul class="report-limitations"><li v-for="(item, index) in report.limitations" :key="index">{{ item }}</li></ul></section><details class="report-limitations" style="margin-top:20px"><summary style="cursor:pointer">报告来源与版本 · {{ report.prompt_version }}</summary><ul><li v-for="source in report.sources" :key="source.citation"><strong>{{ source.citation }}</strong> · <SourceLink :url="source.source_url" :label="source.title" /></li></ul></details><UsageNote :usage="report.metrics.token_usage" :model="report.models.join(' / ') || null" :latency-ms="report.metrics.model_latency_ms" :missing-calls="report.metrics.usage_missing_calls" /></section></template>
