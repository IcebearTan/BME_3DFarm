<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import AppButton from '@/components/AppButton.vue'
import PreviewImage from '@/components/PreviewImage.vue'
import { adminApi } from '@/api/admin'
import { toast } from '@/composables/useToast'

const printers = ref([])
const loading = ref(false)
let timer = null

async function load() {
  loading.value = true
  try {
    const res = await adminApi.getPrinters()
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
  maintenance: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-200',
}
const statusLabel = {
  idle: '空闲', printing: '打印中', offline: '离线', error: '故障', maintenance: '维护',
}

function fmtRemaining(min) {
  // Bambuddy remaining_time 单位是分钟（不是秒）
  if (min == null || min < 0) return '-'
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}m`
  return `${m}m`
}
function trayColor(c) {
  return c && c.length >= 6 ? '#' + c.slice(0, 6) : '#ccc'
}

async function stopPrint(p) {
  if (!window.confirm(`确认停止 ${p.public_name} 的当前打印？`)) return
  try {
    const res = await adminApi.stopPrinter(p.id)
    if (res.code === 200) {
      toast.success('已发送停止命令（状态由 Poller 同步刷新）')
      await load()
    } else {
      toast.error(res.message || '停止失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '停止失败')
  }
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">打印机监控</h1>
        <p class="text-sm text-zinc-400 mt-1">每 30s 自动刷新（Poller 同步 Bambuddy）</p>
      </div>
      <AppButton variant="ghost" size="sm" :loading="loading" @click="load">刷新</AppButton>
    </div>

    <div v-if="loading && !printers.length" class="py-20 text-center text-sm text-zinc-400">加载中…</div>
    <div v-else-if="!printers.length" class="py-20 text-center text-sm text-zinc-400">
      暂无打印机（Poller 未同步，或 Bambuddy 无真机/VP）
    </div>
    <div v-else class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <div
        v-for="p in printers" :key="p.id"
        class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-sm p-5"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <p class="font-semibold text-zinc-900 dark:text-zinc-100 truncate">{{ p.public_name }}</p>
            <p class="text-xs text-zinc-400">{{ p.model }} · {{ p.source === 'virtual' ? '虚拟' : '真机' }}</p>
          </div>
          <span
            :class="['px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap',
                     statusColor[p.status] || statusColor.offline]"
          >{{ statusLabel[p.status] || p.status }}</span>
        </div>

        <!-- 正在打印：显示当前打印订单的缩略图 -->
        <div v-if="p.status === 'printing' && p.current_order_id" class="mt-3 max-w-[180px]">
          <PreviewImage :loader="() => adminApi.orderPreview(p.current_order_id)" />
          <p class="mt-1 text-xs text-zinc-400 truncate">{{ p.current_order_no }}</p>
        </div>

        <div class="mt-4 space-y-3 text-sm">
          <!-- 进度条 -->
          <div v-if="p.status_detail?.progress != null">
            <div class="flex items-center justify-between text-xs mb-1">
              <span class="text-zinc-400">进度 {{ p.status_detail.progress }}%</span>
              <span class="text-zinc-400">剩余 {{ fmtRemaining(p.status_detail.remaining_time) }}</span>
            </div>
            <div class="h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
              <div class="h-full bg-indigo-500 transition-all" :style="{ width: Math.min(100, p.status_detail.progress) + '%' }"></div>
            </div>
          </div>

          <!-- 当前文件 + 队列 -->
          <div class="flex items-center justify-between text-xs gap-2">
            <span class="text-zinc-500 dark:text-zinc-400 truncate">{{ p.status_detail?.current_print || '空闲' }}</span>
            <span class="text-zinc-400 whitespace-nowrap">队列 {{ p.queue_count ?? 0 }}</span>
          </div>

          <!-- 温度 -->
          <div v-if="p.status_detail?.temperatures" class="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span class="text-zinc-400">喷头</span>
              <span class="text-zinc-700 dark:text-zinc-300 ml-1">{{ p.status_detail.temperatures.nozzle }}°/{{ p.status_detail.temperatures.nozzle_target }}°</span>
            </div>
            <div>
              <span class="text-zinc-400">热床</span>
              <span class="text-zinc-700 dark:text-zinc-300 ml-1">{{ p.status_detail.temperatures.bed }}°/{{ p.status_detail.temperatures.bed_target }}°</span>
            </div>
          </div>

          <!-- AMS 耗材 -->
          <div v-if="p.status_detail?.ams?.length" class="space-y-1">
            <p class="text-xs text-zinc-400">AMS 耗材</p>
            <div v-for="tray in (p.status_detail.ams[0]?.tray || [])" :key="tray.id" class="flex items-center gap-2 text-xs">
              <span class="w-3 h-3 rounded-full border border-zinc-200 dark:border-zinc-700 shrink-0" :style="{ background: trayColor(tray.tray_color) }"></span>
              <span class="text-zinc-700 dark:text-zinc-300 truncate">{{ tray.tray_type || '-' }}<span v-if="tray.tray_sub_brands" class="text-zinc-400"> · {{ tray.tray_sub_brands }}</span></span>
              <span class="text-zinc-400 ml-auto whitespace-nowrap">{{ tray.remain >= 0 ? tray.remain + '%' : '-' }}</span>
            </div>
          </div>

          <div v-if="!p.status_detail" class="text-xs text-zinc-400">无实时状态（Poller 未同步）</div>
        </div>

        <p class="mt-3 text-xs text-zinc-400">最后同步：{{ p.last_seen_at || '-' }}</p>

        <div v-if="p.status === 'printing'" class="mt-3">
          <AppButton variant="warning" size="sm" @click="stopPrint(p)">停止打印</AppButton>
        </div>
      </div>
    </div>
  </div>
</template>
