<script setup>
/**
 * AMS 四格统一渲染组件（admin）。
 *  - 框内显示百分比，hover 显示克数/颜色名/全名
 *  - 空槽按 tray_color 有无判定（能读到颜色 = 有料；读不到重量仍按有料，重量显 —）
 *  - tray_id 用数组下标 +1（1-based，与后端 ams_mapping / current_ams_mapping 同体系）
 *
 * 可选交互（下发弹窗用）：selectable 时点击有色格 emit('pick', tray_id)。
 * 状态高亮：selectedId（当前 extruder 已选）、recommendedId（系统推荐）、
 * activeIds（监控页"使用中"）、assignedLabels（多 extruder 时每格分配给的 extruder 角标）。
 */
import { trayHex, colorName, fullName } from '@/utils/ams'

const props = defineProps({
  trays: { type: Array, default: () => [] },
  selectable: { type: Boolean, default: false },
  selectedId: { type: Number, default: null },
  recommendedId: { type: Number, default: null },
  activeIds: { type: Array, default: () => [] },
  // { [tray_id]: 'E1' } —— 被某个 extruder 占用的格显示角标
  assignedLabels: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['pick'])

function low(t) {
  return t.remain != null && t.remain < 20
}
function isActive(t) {
  return props.activeIds.includes(t.tray_id)
}
function isRecommended(t) {
  return props.recommendedId === t.tray_id && props.selectedId !== t.tray_id
}
function onClick(t) {
  if (!props.selectable) return
  if (!trayHex(t.color)) return // 空槽不可选
  emit('pick', t.tray_id)
}
</script>

<template>
  <div class="grid grid-cols-4 gap-2">
    <div
      v-for="(t, idx) in trays" :key="t.tray_id ?? t.slot ?? idx"
      @click="onClick(t)"
      :class="[
        'group relative rounded-lg border px-2 py-1.5 transition',
        trayHex(t.color)
          ? 'border-zinc-100 dark:border-zinc-800'
          : 'border-dashed border-zinc-200 dark:border-zinc-700',
        selectable && trayHex(t.color)
          ? 'cursor-pointer hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800/40'
          : '',
        selectable && !trayHex(t.color) ? 'opacity-50' : '',
        selectedId === t.tray_id ? 'ring-2 ring-indigo-500 border-indigo-500' : '',
        isRecommended(t) ? 'ring-1 ring-sky-400 ring-offset-0' : '',
        isActive(t) ? 'ring-2 ring-emerald-500 border-emerald-500' : '',
      ]"
    >
      <!-- 行1：色点 + 材料类型 -->
      <div class="flex items-center gap-1.5">
        <span class="w-2.5 h-2.5 rounded-full shrink-0"
              :class="trayHex(t.color)
                ? 'ring-1 ring-black/10 dark:ring-white/15'
                : 'border border-dashed border-zinc-300 dark:border-zinc-600'"
              :style="trayHex(t.color) ? { background: trayHex(t.color) } : null"></span>
        <span class="text-[11px] text-zinc-700 dark:text-zinc-300 truncate">{{ trayHex(t.color) ? (t.type || '耗材') : '空槽' }}</span>
      </div>

      <!-- 行2：余量进度条 + 百分比（无余量数据显 —） -->
      <div class="mt-1.5 flex items-center gap-1.5">
        <div class="flex-1 h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
          <div class="h-full rounded-full transition-all"
               :class="low(t) ? 'bg-amber-500' : 'bg-zinc-500 dark:bg-zinc-300'"
               :style="{ width: (t.remain != null ? Math.max(2, t.remain) : 0) + '%' }"></div>
        </div>
        <span class="text-[10px] tabular-nums shrink-0"
              :class="low(t) ? 'text-amber-500' : 'text-zinc-400'">
          {{ trayHex(t.color) && t.remain != null ? Math.round(t.remain) + '%' : '—' }}
        </span>
      </div>

      <!-- 角标：assigned(extruder 号) / active(使用中) -->
      <span v-if="assignedLabels[t.tray_id]"
            class="absolute -top-1.5 -right-1.5 px-1 py-px rounded-full text-[9px] font-semibold leading-none bg-indigo-600 text-white shadow">{{ assignedLabels[t.tray_id] }}</span>
      <span v-else-if="isActive(t)"
            class="absolute -top-1.5 -right-1.5 px-1 py-px rounded-full text-[9px] font-medium leading-none bg-emerald-600 text-white shadow">使用中</span>
      <span v-else-if="isRecommended(t)"
            class="absolute -top-1.5 -right-1.5 w-3.5 h-3.5 flex items-center justify-center rounded-full text-[9px] leading-none bg-sky-500 text-white shadow">✓</span>

      <!-- hover 详情：颜色名 / 全名 / hex · 余量克数（有则展示） -->
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
          #{{ (t.color || '------').slice(0, 6).toUpperCase() }} · {{ t.remain_g != null ? '余量 ' + Math.round(t.remain_g) + 'g' + (t.label_weight_g ? ' / ' + t.label_weight_g + 'g' : '') : '无料重' }}
        </p>
      </div>
    </div>
  </div>
</template>
