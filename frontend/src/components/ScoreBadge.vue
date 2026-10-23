<script setup lang="ts">
defineProps<{ score: number | null }>()
</script>
<template><span class="score" :class="score === null ? 'unknown' : score >= 70 ? 'high' : score >= 50 ? 'medium' : 'low'" :aria-label="score === null ? '尚未评分' : `重要性评分 ${score} 分，满分 100`">{{ score ?? '—' }}<small v-if="score !== null">分</small></span></template>
