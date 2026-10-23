<script setup lang="ts">
import type { VerifiedClaim } from '../api/types'
defineProps<{ claim: VerifiedClaim; evidenceIds: string[] }>()
function focusEvidence(citation: string) { document.getElementById(`evidence-${citation}`)?.focus({ preventScroll: true }) }
</script>
<template><div class="claim"><p>{{ claim.text_zh }} <template v-for="citation in claim.citations" :key="citation"><a v-if="evidenceIds.includes(citation)" :href="`#evidence-${citation}`" class="citation-link" @click="focusEvidence(citation)">{{ citation }}</a><span v-else class="badge amber">{{ citation }} · 证据未读取</span></template></p><details v-if="claim.support_quotes.length"><summary>对照原文引文</summary><blockquote v-for="(quote, index) in claim.support_quotes" :key="index"><strong>{{ quote.citation }}</strong> · {{ quote.quote }}</blockquote></details></div></template>
