<script setup>
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'
import AppButton from './AppButton.vue'
import PreviewImage from './PreviewImage.vue'
import { ordersApi } from '@/api/orders'

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

// 剩余秒 → "2h30m" / "45m" / "<1m"；无效返回空串（模板据此隐藏）
function fmtRemaining(sec) {
  if (sec == null || sec < 0) return ''
  const m = Math.round(sec / 60)
  if (m < 1) return '<1m'
  const h = Math.floor(m / 60)
  const mm = m % 60
  return h > 0 ? `${h}h${String(mm).padStart(2, '0')}m` : `${mm}m`
}
</script>

<template>
  <div
    class="flex gap-4 items-start rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-sm p-4 hover:shadow-md transition-shadow"
  >
    <!-- 左：缩略图（固定尺寸容器，PreviewImage 填满） -->
    <div class="w-24 h-24 shrink-0">
      <PreviewImage :loader="() => ordersApi.previewImage(order.id)" />
    </div>

    <!-- 右：内容 -->
    <div class="flex-1 min-w-0">
      <div class="flex items-start justify-between gap-2">
        <div class="min-w-0">
          <p class="text-xs font-mono text-zinc-400 truncate">{{ order.order_no }}</p>
          <p class="mt-0.5 text-base font-semibold text-zinc-900 dark:text-zinc-100 truncate">
            {{ order.material
            }}<span class="text-zinc-400 font-normal"> · {{ order.quantity }} 件</span>
          </p>
        </div>
        <StatusBadge :status="order.public_status" />
      </div>

      <!-- 进行中：进度条 -->
      <div v-if="order.public_progress > 0" class="mt-2">
        <div class="flex items-center justify-between text-xs text-zinc-400 mb-1">
          <span class="truncate">{{ order.printer_name ? '在 ' + order.printer_name + ' 打印' : '打印进度' }}</span>
          <span class="tabular-nums whitespace-nowrap">
            {{ order.public_progress }}%<span v-if="fmtRemaining(order.remaining_seconds)"> · 剩 {{ fmtRemaining(order.remaining_seconds) }}</span>
          </span>
        </div>
        <div class="h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
          <div
            class="h-full bg-zinc-500 dark:bg-zinc-300 rounded-full transition-all"
            :style="{ width: order.public_progress + '%' }"
          ></div>
        </div>
      </div>

      <div class="mt-3 flex items-center justify-between gap-2 text-xs">
        <span class="text-zinc-400 truncate">{{ created }}</span>
        <span v-if="order.estimated_credit" class="text-zinc-700 dark:text-zinc-300 font-medium whitespace-nowrap">
          {{ order.estimated_credit }} credit
        </span>
      </div>

      <!-- 失败原因 / 交付说明 摘要（全文在详情） -->
      <div v-if="order.fail_reason" class="mt-2 text-xs text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/30 rounded-lg px-2.5 py-1.5 truncate">
        失败：{{ order.fail_reason }}
      </div>
      <div v-else-if="order.completion_note" class="mt-2 text-xs text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/30 rounded-lg px-2.5 py-1.5 truncate">
        {{ order.completion_note }}
      </div>

      <div class="mt-3 flex gap-2">
        <AppButton variant="subtle" size="sm" @click="emit('click')">详情</AppButton>
        <AppButton v-if="canConfirm" variant="primary" size="sm" @click="emit('confirm')">
          确认下单
        </AppButton>
        <AppButton v-if="canCancel" variant="ghost" size="sm" @click="emit('cancel')">
          取消
        </AppButton>
      </div>
    </div>
  </div>
</template>
