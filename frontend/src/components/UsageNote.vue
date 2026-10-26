<script setup lang="ts">
import type { TokenUsage } from '../api/types'
defineProps<{ usage?: TokenUsage | null; model?: string | null; latencyMs?: number | null; missingCalls?: number }>()
</script>
<template>
 <div class="usage-note">
  <span v-if="model">模型 <strong>{{ model }}</strong></span>
  <span v-if="usage">{{ missingCalls ? '已知 Token' : 'Token' }} <strong>{{ usage.total_tokens.toLocaleString() }}</strong><small>输入 {{ usage.prompt_tokens }} / 输出 {{ usage.completion_tokens }}</small></span>
  <span v-else>Token <strong>用量未知</strong></span>
  <span v-if="missingCalls" class="warning-text">{{ missingCalls }} 次调用用量未知，当前统计不是完整账单</span>
  <span v-if="latencyMs != null">模型耗时 <strong>{{ (latencyMs / 1000).toFixed(2) }} 秒</strong></span>
 </div>
</template>
