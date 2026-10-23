<script setup lang="ts">
import { AlertCircle, FileSearch, LoaderCircle } from '@lucide/vue'
import { errorMessage } from '../api/client'
defineProps<{ kind: 'loading' | 'empty' | 'error'; title?: string; detail?: string; error?: unknown; retry?: boolean }>()
defineEmits<{ retry: [] }>()
</script>
<template>
 <div class="ui-state" :class="kind" :role="kind === 'error' ? 'alert' : 'status'" :aria-busy="kind === 'loading'">
  <component :is="kind === 'loading' ? LoaderCircle : kind === 'error' ? AlertCircle : FileSearch" :class="{ spin: kind === 'loading' }" :size="28" aria-hidden="true" />
  <h3>{{ title ?? (kind === 'loading' ? '正在读取情报' : kind === 'error' ? '暂时无法读取' : '这里还没有内容') }}</h3>
  <p>{{ detail ?? (error ? errorMessage(error) : kind === 'loading' ? '从本机服务读取已保存的数据…' : '没有找到符合条件的已保存记录。') }}</p>
  <button v-if="retry" class="button secondary" type="button" @click="$emit('retry')">重新读取</button>
  <div v-if="kind === 'loading'" class="skeleton-lines" aria-hidden="true"><span></span><span></span><span></span></div>
 </div>
</template>
