<script setup>
import { ref } from 'vue'
import { UploadCloud, X, FileBox } from 'lucide-vue-next'
import { toast } from '@/composables/useToast'

const props = defineProps({
  modelValue: { type: Object, default: null }, // { file, name, size }
})
const emit = defineEmits(['update:modelValue'])

const dragging = ref(false)
const inputRef = ref(null)

const ALLOWED = ['.3mf', '.gcode.3mf']
const MAX = 200 * 1024 * 1024

function pick() {
  inputRef.value?.click()
}
function onDrop(e) {
  dragging.value = false
  const f = e.dataTransfer.files?.[0]
  if (f) setFile(f)
}
function onChange(e) {
  const f = e.target.files?.[0]
  if (f) setFile(f)
}
function setFile(f) {
  const name = f.name.toLowerCase()
  if (!ALLOWED.some((ext) => name.endsWith(ext))) {
    toast.error('仅支持 .3mf 或 .gcode.3mf 文件')
    return
  }
  if (f.size > MAX) {
    toast.error('文件超过 200MB 上限')
    return
  }
  emit('update:modelValue', { file: f, name: f.name, size: f.size })
}
function clear() {
  emit('update:modelValue', null)
  if (inputRef.value) inputRef.value.value = ''
}
function fmtSize(b) {
  if (b < 1024) return b + ' B'
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB'
  return (b / 1024 / 1024).toFixed(1) + ' MB'
}
</script>

<template>
  <div>
    <div
      v-if="!modelValue"
      @click="pick"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
      :class="[
        'border-2 border-dashed rounded-2xl py-10 px-6 text-center cursor-pointer transition-colors',
        dragging
          ? 'border-zinc-400 bg-zinc-50 dark:bg-zinc-800'
          : 'border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600',
      ]"
    >
      <UploadCloud class="w-8 h-8 mx-auto text-zinc-400" />
      <p class="mt-3 text-sm text-zinc-600 dark:text-zinc-400">点击或拖拽文件到此处</p>
      <p class="mt-1 text-xs text-zinc-400">支持 .3mf / .gcode.3mf，最大 200MB</p>
      <input
        ref="inputRef" type="file" accept=".3mf,.gcode.3mf" class="hidden"
        @change="onChange"
      />
    </div>
    <div
      v-else
      class="flex items-center justify-between rounded-xl bg-zinc-50 dark:bg-zinc-800/60 px-4 py-3"
    >
      <div class="flex items-center gap-3 min-w-0">
        <FileBox class="w-5 h-5 text-zinc-400 shrink-0" />
        <div class="min-w-0">
          <p class="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">
            {{ modelValue.name }}
          </p>
          <p class="text-xs text-zinc-400">{{ fmtSize(modelValue.size) }}</p>
        </div>
      </div>
      <button
        @click="clear"
        class="p-1.5 rounded-lg text-zinc-400 hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950"
      >
        <X class="w-4 h-4" />
      </button>
    </div>
  </div>
</template>
