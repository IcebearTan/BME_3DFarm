<script setup>
import { computed } from 'vue'

const props = defineProps({ status: String })

// public_status → 低饱和配色（中性/琥珀/靛/天蓝/玫红/翠）
const map = {
  报价中: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
  待确认: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
  准备打印: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300',
  排队中: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300',
  打印中: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-200',
  '已打印，质检中': 'bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300',
  异常处理中: 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300',
  无法打印: 'bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-200',
  已退款: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200',
  已取消: 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-500',
  已完成: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
}
const cls = computed(() => [
  'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium whitespace-nowrap',
  map[props.status] || map['报价中'],
])
</script>

<template>
  <span :class="cls">{{ status }}</span>
</template>
