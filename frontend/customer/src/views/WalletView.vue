<script setup>
import { ref, onMounted } from 'vue'
import { Wallet, Snowflake, Layers, ArrowDownRight, ArrowUpRight } from 'lucide-vue-next'
import AppCard from '@/components/AppCard.vue'
import { creditApi } from '@/api/credit'

// 原 Dashboard 内容（额度余额 + 流水），从首页迁到这里，入口在头像 popover。
const balance = ref({ available: '0', frozen: '0', total: '0' })
const txns = ref([])
const loading = ref(false)

const TYPE_LABEL = {
  grant: '发放', freeze: '冻结', capture: '实扣', release: '释放',
  refund: '退款', adjust: '调整', reward: '奖励',
}
const INFLOW = new Set(['grant', 'release', 'refund', 'reward'])

function typeColor(type) {
  return INFLOW.has(type) ? 'text-emerald-600' : 'text-zinc-600'
}
function typeSign(type) {
  return INFLOW.has(type) ? '+' : '-'
}

async function load() {
  loading.value = true
  try {
    const [b, t] = await Promise.all([
      creditApi.balance(),
      creditApi.transactions({ page: 1, per_page: 20 }),
    ])
    balance.value = b.data
    txns.value = t.data.items
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">额度流水</h1>
      <p class="text-sm text-zinc-400 mt-1">用 credit 下单打印</p>
    </div>

    <div class="grid grid-cols-3 gap-3 sm:gap-4">
      <div class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 p-5">
        <div class="flex items-center gap-2 text-zinc-400 text-xs font-medium">
          <Wallet class="w-3.5 h-3.5" /> 可用
        </div>
        <p class="mt-2 text-2xl sm:text-3xl font-semibold text-emerald-600">{{ balance.available }}</p>
      </div>
      <div class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 p-5">
        <div class="flex items-center gap-2 text-zinc-400 text-xs font-medium">
          <Snowflake class="w-3.5 h-3.5" /> 冻结
        </div>
        <p class="mt-2 text-2xl sm:text-3xl font-semibold text-amber-600">{{ balance.frozen }}</p>
      </div>
      <div class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 p-5">
        <div class="flex items-center gap-2 text-zinc-400 text-xs font-medium">
          <Layers class="w-3.5 h-3.5" /> 合计
        </div>
        <p class="mt-2 text-2xl sm:text-3xl font-semibold text-zinc-900 dark:text-zinc-100">{{ balance.total }}</p>
      </div>
    </div>

    <AppCard title="额度流水">
      <div v-if="loading" class="py-10 text-center text-sm text-zinc-400">加载中…</div>
      <div v-else-if="!txns.length" class="py-10 text-center text-sm text-zinc-400">暂无流水</div>
      <ul v-else class="divide-y divide-zinc-100 dark:divide-zinc-800 -m-6">
        <li
          v-for="t in txns" :key="t.id"
          class="flex items-center justify-between px-6 py-3.5"
        >
          <div class="flex items-center gap-3 min-w-0">
            <div
              :class="['w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-zinc-100 dark:bg-zinc-800', typeColor(t.type)]"
            >
              <ArrowDownRight v-if="typeSign(t.type) === '+'" class="w-4 h-4" />
              <ArrowUpRight v-else class="w-4 h-4" />
            </div>
            <div class="min-w-0">
              <p class="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">
                {{ TYPE_LABEL[t.type] || t.type }}
                <span v-if="t.reason" class="text-zinc-400 font-normal">· {{ t.reason }}</span>
              </p>
              <p class="text-xs text-zinc-400">{{ t.created_at }}</p>
            </div>
          </div>
          <span :class="['text-sm font-semibold shrink-0 ml-3', typeColor(t.type)]">
            {{ typeSign(t.type) }}{{ t.amount }}
          </span>
        </li>
      </ul>
    </AppCard>
  </div>
</template>
