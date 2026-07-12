<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { printersApi } from '@/api/printers'

const printers = ref([])
const loading = ref(false)
let timer = null

async function load() {
  loading.value = true
  try {
    const res = await printersApi.list()
    printers.value = res.data.items
  } finally {
    loading.value = false
  }
}
onMounted(() => {
  load()
  timer = setInterval(load, 30000)
})
onUnmounted(() => clearInterval(timer))

const statusColor = {
  idle: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
  printing: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-200',
  offline: 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400',
  error: 'bg-rose-100 text-rose-700 dark:bg-rose-900 dark:text-rose-200',
}
const statusLabel = {
  idle: '空闲', printing: '打印中', offline: '离线', error: '故障',
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">打印机状态</h1>
      <p class="text-sm text-zinc-400 mt-1">农场打印机忙闲（每 30s 刷新）</p>
    </div>

    <div v-if="loading && !printers.length" class="py-20 text-center text-sm text-zinc-400">加载中…</div>
    <div v-else-if="!printers.length" class="py-20 text-center text-sm text-zinc-400">
      暂无打印机
    </div>
    <div v-else class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <div
        v-for="p in printers" :key="p.id"
        class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-sm p-5"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <p class="font-semibold text-zinc-900 dark:text-zinc-100 truncate">{{ p.public_name }}</p>
            <p class="text-xs text-zinc-400">{{ p.model }}</p>
          </div>
          <span
            :class="['px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap',
                     statusColor[p.status] || statusColor.offline]"
          >{{ statusLabel[p.status] || p.status }}</span>
        </div>
        <div class="mt-4 text-sm">
          <p class="text-xs text-zinc-400">队列任务</p>
          <p class="text-zinc-700 dark:text-zinc-300">{{ p.queue_count }}</p>
        </div>
      </div>
    </div>
  </div>
</template>
