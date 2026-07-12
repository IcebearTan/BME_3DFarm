<script setup>
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'
import AppButton from './AppButton.vue'

const props = defineProps({ order: Object })
const emit = defineEmits(['confirm', 'cancel', 'click'])

const POST_PRINT = ['PRINTING', 'PRINT_COMPLETED', 'QC_PENDING', 'CLOSED', 'CANCELLED', 'REFUNDED']

const created = computed(() => {
  if (!props.order.created_at) return ''
  const d = new Date(props.order.created_at)
  return isNaN(d) ? props.order.created_at : d.toLocaleString('zh-CN')
})
const canConfirm = computed(() => props.order.status === 'WAITING_CONFIRM')
const canCancel = computed(() => !POST_PRINT.includes(props.order.status))
</script>

<template>
  <div
    class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-sm p-5 hover:shadow-md transition-shadow"
  >
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <p class="text-xs font-mono text-zinc-400">{{ order.order_no }}</p>
        <p class="mt-1 text-base font-semibold text-zinc-900 dark:text-zinc-100">
          {{ order.material
          }}<span class="text-zinc-400 font-normal"> · {{ order.quantity }} 件</span>
        </p>
      </div>
      <StatusBadge :status="order.public_status" />
    </div>

    <div class="mt-4 flex items-center justify-between text-sm">
      <span class="text-zinc-400">{{ created }}</span>
      <span v-if="order.estimated_credit" class="text-zinc-700 dark:text-zinc-300 font-medium">
        {{ order.estimated_credit }} credit
      </span>
    </div>

    <div v-if="order.public_progress > 0" class="mt-3">
      <div class="flex justify-between text-xs text-zinc-400 mb-1">
        <span>{{ order.public_status }}</span>
        <span>{{ order.public_progress }}%</span>
      </div>
      <div class="h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
        <div
          class="h-full bg-indigo-500 rounded-full transition-all"
          :style="{ width: order.public_progress + '%' }"
        ></div>
      </div>
    </div>

    <div class="mt-4 flex gap-2">
      <AppButton variant="subtle" size="sm" @click="emit('click')">详情</AppButton>
      <AppButton v-if="canConfirm" variant="primary" size="sm" @click="emit('confirm')">
        确认下单
      </AppButton>
      <AppButton v-if="canCancel" variant="ghost" size="sm" @click="emit('cancel')">
        取消
      </AppButton>
    </div>
  </div>
</template>
