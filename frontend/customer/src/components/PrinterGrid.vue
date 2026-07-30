<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { printersApi } from '@/api/printers'

// 自包含的打印机网格：拉取 + 30s 轮询 + 卡片渲染。
// 首页与 /printers 页共用，避免两处重复实现。
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

// AMS 耗材色：Bambuddy tray_color 是无 # 的 RGB hex，转成可用色值；空/无效返回 null
function trayHex(c) {
  return c && c.length >= 6 ? '#' + c.slice(0, 6) : null
}

// Bambu 协议只有 hex、没有颜色名——用参考色最近邻（RGB 欧氏距离）推断中文名
const COLOR_REF = [
  ['FFFFFF', '白'], ['000000', '黑'], ['D9D9D9', '浅灰'], ['5A5A5A', '深灰'],
  ['DB2F2F', '红'], ['F26B1F', '橙'], ['FFCF3F', '黄'], ['D4A04B', '金'],
  ['1E9B54', '绿'], ['0B8AB8', '青'], ['1E9BFF', '蓝'], ['7C5BBA', '紫'],
  ['E66FA5', '粉'], ['8B5A2B', '棕'],
]
function colorName(c) {
  if (!c || c.length < 6) return null
  const n = (s, o) => parseInt(s.slice(o, o + 2), 16)
  const r = n(c, 0), g = n(c, 2), b = n(c, 4)
  let best = null, bestD = Infinity
  for (const [ref, name] of COLOR_REF) {
    const d = (n(ref, 0) - r) ** 2 + (n(ref, 2) - g) ** 2 + (n(ref, 4) - b) ** 2
    if (d < bestD) { bestD = d; best = name }
  }
  return best
}

// 耗材全名：品牌 + 类型 + 子系列，如 "Bambu PLA Matte"
function fullName(t) {
  const parts = []
  if (t.brand) parts.push(t.brand)
  const ts = [t.type, t.subtype].filter(Boolean).join(' ')
  if (ts) parts.push(ts)
  return parts.join(' ') || null
}

defineExpose({ load })
</script>

<template>
  <div>
    <div v-if="loading && !printers.length" class="py-16 text-center text-sm text-zinc-400">加载中…</div>
    <div v-else-if="!printers.length" class="py-16 text-center text-sm text-zinc-400">暂无打印机</div>
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
        <div class="mt-4">
          <div class="flex items-center justify-between">
            <p class="text-xs text-zinc-400">AMS 耗材</p>
            <span class="text-xs text-zinc-700 dark:text-zinc-300">队列 {{ p.queue_count ?? 0 }}</span>
          </div>

          <!-- 四格耗材：扁平色点显色 + 进度条显余量（仿 Bambuddy） -->
          <div v-if="p.ams.length" class="mt-2 grid grid-cols-4 gap-2">
            <div v-for="t in p.ams" :key="t.slot ?? t.type"
                 class="group relative rounded-lg border border-zinc-100 dark:border-zinc-800 px-2 py-1.5">
              <!-- 行1：色点 + 材料类型 -->
              <div class="flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded-full shrink-0"
                      :class="trayHex(t.color)
                        ? 'ring-1 ring-black/10 dark:ring-white/15'
                        : 'border border-dashed border-zinc-300 dark:border-zinc-600'"
                      :style="trayHex(t.color) ? { background: trayHex(t.color) } : null"></span>
                <span class="text-[11px] text-zinc-700 dark:text-zinc-300 truncate">{{ t.type || '空槽' }}</span>
              </div>
              <!-- 行2：余量进度条 + 百分比 -->
              <div class="mt-1.5 flex items-center gap-1.5">
                <div class="flex-1 h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
                  <div class="h-full rounded-full transition-all"
                       :class="t.remain != null && t.remain < 20 ? 'bg-amber-500' : 'bg-zinc-500 dark:bg-zinc-300'"
                       :style="{ width: (t.remain != null ? Math.max(2, t.remain) : 0) + '%' }"></div>
                </div>
                <span class="text-[10px] tabular-nums shrink-0"
                      :class="t.remain != null && t.remain < 20 ? 'text-amber-500' : 'text-zinc-400'">
                  {{ t.remain_g != null ? Math.round(t.remain_g) + 'g' : '—' }}
                </span>
              </div>

              <!-- hover 详情：颜色名 / 全名 / hex·余量（有则展示，无则少展示） -->
              <div v-if="trayHex(t.color) || fullName(t)"
                   class="absolute z-30 bottom-full left-1/2 -translate-x-1/2 mb-2 w-max max-w-[220px]
                          opacity-0 scale-95 group-hover:opacity-100 group-hover:scale-100
                          pointer-events-none transition origin-bottom
                          rounded-lg bg-zinc-900 dark:bg-zinc-700 px-3 py-2 shadow-lg ring-1 ring-black/10">
                <div class="flex items-center gap-1.5 text-xs font-medium text-white">
                  <span class="w-2.5 h-2.5 rounded-full ring-1 ring-white/30"
                        :style="trayHex(t.color) ? { background: trayHex(t.color) } : null"></span>
                  {{ colorName(t.color) || '未知色' }}
                </div>
                <p v-if="fullName(t)" class="mt-1 text-[11px] text-white/80">{{ fullName(t) }}</p>
                <p class="mt-0.5 text-[10px] text-white/50 tabular-nums">
                  #{{ (t.color || '------').slice(0, 6).toUpperCase() }} · {{ t.remain_g != null ? '余量 ' + Math.round(t.remain_g) + 'g' + (t.label_weight_g ? ' / ' + t.label_weight_g + 'g' : '') : '无料' }}
                </p>
              </div>
            </div>
          </div>

          <p v-else-if="p.has_ams" class="mt-2 text-xs text-zinc-400">耗材状态同步中…</p>
          <p v-else class="mt-2 text-xs text-zinc-400">未加装 AMS</p>
        </div>
      </div>
    </div>
  </div>
</template>
