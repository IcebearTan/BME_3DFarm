<script setup>
/**
 * 打印机富下拉（admin 下发用）。原生 <select> 的 option 无法渲染色块，
 * 故用 @headlessui/vue Listbox 自建：每项显示 名字 + 状态徽章 + 4 迷你色点 + 队列数。
 * 值 = bambuddy_printer_id（与原 <select> 一致，OrdersView 逻辑无感）。
 */
import { computed } from 'vue'
import { Listbox, ListboxButton, ListboxOptions, ListboxOption } from '@headlessui/vue'
import { ChevronDown } from 'lucide-vue-next'
import { normTray, trayHex } from '@/utils/ams'

const props = defineProps({
  modelValue: { type: [Number, String], default: '' },
  printers: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'change'])

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

// 取某打印机主 AMS 的 4 槽归一化（供迷你色点）
function traysOf(p) {
  const raw = p?.status_detail?.ams?.[0]?.tray || []
  return raw.map((t, i) => normTray(t, i)).slice(0, 4)
}

const selected = computed(() => props.printers.find((p) => p.bambuddy_printer_id === props.modelValue) || null)

function onSelect(val) {
  emit('update:modelValue', val)
  emit('change', val)
}
</script>

<template>
  <Listbox :model-value="modelValue" @update:model-value="onSelect">
    <div class="relative">
      <ListboxButton
        class="w-full h-10 px-3 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm text-left
               flex items-center justify-between gap-2 focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10"
      >
        <template v-if="selected">
          <span class="flex items-center gap-2 min-w-0">
            <span class="truncate font-medium text-zinc-800 dark:text-zinc-100">{{ selected.public_name }}</span>
            <span v-if="statusLabel[selected.status]"
                  :class="['px-1.5 py-0.5 rounded-full text-[10px] font-medium shrink-0', statusColor[selected.status] || statusColor.offline]">
              {{ statusLabel[selected.status] || selected.status }}
            </span>
            <span class="flex items-center gap-0.5 shrink-0">
              <span v-for="(t, i) in traysOf(selected)" :key="i"
                    class="w-2 h-2 rounded-full"
                    :class="trayHex(t.color) ? 'ring-1 ring-black/10 dark:ring-white/15' : 'border border-dashed border-zinc-300 dark:border-zinc-600'"
                    :style="trayHex(t.color) ? { background: trayHex(t.color) } : null"></span>
            </span>
          </span>
        </template>
        <span v-else class="text-zinc-400">请选择打印机…</span>
        <ChevronDown class="w-4 h-4 text-zinc-400 shrink-0" />
      </ListboxButton>

      <ListboxOptions
        class="absolute z-40 mt-1 w-full max-h-72 overflow-auto py-1 rounded-xl border border-zinc-200 dark:border-zinc-700
               bg-white dark:bg-zinc-900 shadow-lg focus:outline-none"
      >
          <ListboxOption v-if="!printers.length" disabled value=""
            class="px-3 py-2 text-xs text-zinc-400">暂无可用打印机</ListboxOption>
          <ListboxOption
            v-for="p in printers" :key="p.bambuddy_printer_id" :value="p.bambuddy_printer_id"
            v-slot="{ active: hover, selected: chosen }"
            :class="['cursor-pointer select-none px-3 py-2 text-sm flex items-center gap-2',
                     hover ? 'bg-zinc-100 dark:bg-zinc-800' : '']"
          >
            <span class="flex-1 min-w-0">
              <span class="flex items-center gap-2">
                <span class="truncate font-medium text-zinc-800 dark:text-zinc-100">{{ p.public_name }}</span>
                <span v-if="statusLabel[p.status]"
                      :class="['px-1.5 py-0.5 rounded-full text-[10px] font-medium shrink-0', statusColor[p.status] || statusColor.offline]">
                  {{ statusLabel[p.status] || p.status }}
                </span>
              </span>
              <span class="flex items-center gap-1 mt-1">
                <span class="flex items-center gap-0.5">
                  <span v-for="(t, i) in traysOf(p)" :key="i"
                        class="w-2.5 h-2.5 rounded-full"
                        :class="trayHex(t.color) ? 'ring-1 ring-black/10 dark:ring-white/15' : 'border border-dashed border-zinc-300 dark:border-zinc-600'"
                        :style="trayHex(t.color) ? { background: trayHex(t.color) } : null"></span>
                </span>
                <span class="text-[10px] text-zinc-400">队列 {{ p.queue_count ?? 0 }}</span>
              </span>
            </span>
            <span v-if="chosen" class="text-indigo-600 dark:text-indigo-400 text-xs shrink-0">✓</span>
          </ListboxOption>
        </ListboxOptions>
    </div>
  </Listbox>
</template>
