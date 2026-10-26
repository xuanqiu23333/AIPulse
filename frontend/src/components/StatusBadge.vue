<script setup lang="ts">
import { computed } from 'vue'
import { taskLabels } from '../lib/format'
const props = defineProps<{ status: string }>()
const tone = computed(() => ({ completed: 'green', complete: 'green', failed: 'red', partial: 'amber', interrupted: 'amber', running: 'blue', queued: 'neutral' })[props.status] ?? 'neutral')
</script>
<template><span class="badge status-badge" :class="tone"><span class="status-dot"></span>{{ taskLabels[status] ?? status }}<span class="status-code">{{ status }}</span></span></template>
