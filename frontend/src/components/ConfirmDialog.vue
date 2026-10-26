<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { AlertTriangle, X } from '@lucide/vue'
const props = defineProps<{ open: boolean; busy?: boolean; title: string; message: string; confirmLabel?: string }>()
const emit = defineEmits<{ confirm: []; cancel: [] }>()
const dialog = ref<HTMLDialogElement>()
async function sync() { await nextTick(); if (!dialog.value) return; if (props.open && !dialog.value.open) dialog.value.showModal(); else if (!props.open && dialog.value.open) dialog.value.close() }
watch(() => props.open, sync)
onMounted(sync)
onBeforeUnmount(() => dialog.value?.close())
</script>
<template>
 <dialog ref="dialog" class="confirm-dialog" aria-labelledby="confirm-title" aria-describedby="confirm-description" @cancel.prevent="!busy && emit('cancel')">
  <div class="dialog-heading"><span class="icon-box amber"><AlertTriangle :size="22" aria-hidden="true" /></span><button type="button" class="icon-button" aria-label="关闭确认弹窗" :disabled="busy" @click="emit('cancel')"><X :size="20" /></button></div>
  <h2 id="confirm-title">{{ title }}</h2><p id="confirm-description">{{ message }}</p>
  <div class="dialog-actions"><button type="button" class="button secondary" :disabled="busy" autofocus @click="emit('cancel')">暂不执行</button><button type="button" class="button primary" data-testid="confirm-action" :disabled="busy" @click="emit('confirm')">{{ busy ? '正在提交…' : confirmLabel ?? '确认并执行一次' }}</button></div>
 </dialog>
</template>
