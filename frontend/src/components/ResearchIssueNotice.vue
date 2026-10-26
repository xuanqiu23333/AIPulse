<script setup lang="ts">
import { computed } from 'vue'
import { errorLabels } from '../api/client'
const props = defineProps<{ status: string; code: string }>()
const githubLimited = computed(() => props.code === 'github_rate_limited')
const warning = computed(() => githubLimited.value || props.status === 'partial' || props.status === 'completed')
</script>
<template>
 <div class="notice" :class="warning ? 'warning' : 'error'" :role="warning ? 'status' : 'alert'">
  <strong>{{ githubLimited ? 'GitHub 检索限流' : warning ? '研究存在信息缺口或来源警告' : '研究执行失败' }}</strong>
  <p v-if="githubLimited">该可选来源的检索受到限流；其他来源已经取得的证据仍保留。是否完成以有效核心结论及覆盖范围为准。</p>
  <p v-else>{{ errorLabels[code] ?? '请结合真实任务状态、证据和报告局限阅读。' }}</p>
  <code>{{ code }}</code>
 </div>
</template>
