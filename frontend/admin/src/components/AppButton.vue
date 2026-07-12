<script setup>
import { computed } from 'vue'
import { Loader2 } from 'lucide-vue-next'

const props = defineProps({
  variant: { type: String, default: 'primary' },
  size: { type: String, default: 'md' },
  loading: Boolean,
  disabled: Boolean,
  block: Boolean,
})
defineEmits(['click'])

const variantClass = {
  primary: 'bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200',
  subtle: 'bg-zinc-100 text-zinc-900 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700',
  ghost: 'bg-transparent text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800',
  success: 'bg-emerald-600 text-white hover:bg-emerald-500',
  warning: 'bg-amber-500 text-white hover:bg-amber-400',
  danger: 'bg-rose-600 text-white hover:bg-rose-500',
}
const sizeClass = {
  xs: 'h-7 px-2.5 text-xs',
  sm: 'h-8 px-3 text-sm',
  md: 'h-10 px-4 text-sm',
  lg: 'h-12 px-6 text-base',
}
const cls = computed(() => [
  'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium select-none',
  'focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400/40',
  'disabled:opacity-50 disabled:cursor-not-allowed',
  variantClass[props.variant] || variantClass.primary,
  sizeClass[props.size] || sizeClass.md,
  props.block && 'w-full',
])
</script>

<template>
  <button :class="cls" :disabled="disabled || loading" @click="$emit('click')">
    <Loader2 v-if="loading" class="w-4 h-4 animate-spin" />
    <slot />
  </button>
</template>
