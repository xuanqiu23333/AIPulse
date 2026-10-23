<script setup lang="ts">
import { computed } from 'vue'
import { ArrowUpRight } from '@lucide/vue'
import { safeUrl, sourceHost } from '../lib/format'
const props = defineProps<{ url: string; label?: string }>()
const href = computed(() => safeUrl(props.url))
</script>
<template><a v-if="href" :href="href" class="source-link" target="_blank" rel="noopener noreferrer" :title="href">{{ label ?? sourceHost(url) }}<ArrowUpRight :size="14" aria-hidden="true" /><span class="sr-only">（新窗口打开）</span></a><span v-else class="muted">来源链接不可用</span></template>
