<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: [String, Number],
  label: String,
  error: String,
  hint: String,
  type: { type: String, default: 'text' },
  placeholder: String,
  required: Boolean,
})
defineEmits(['update:modelValue'])

const cls = computed(() => [
  'w-full h-10 px-3 rounded-lg border bg-white dark:bg-zinc-900 text-sm',
  'border-zinc-200 dark:border-zinc-700 placeholder:text-zinc-400',
  'focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10 focus:border-zinc-400',
  props.error && 'border-rose-400 focus:ring-rose-500/10',
])
</script>

<template>
  <div class="space-y-1.5">
    <label v-if="label" class="block text-sm font-medium text-zinc-700 dark:text-zinc-300">
      {{ label }}<span v-if="required" class="text-rose-500 ml-0.5">*</span>
    </label>
    <input
      :type="type" :value="modelValue" :placeholder="placeholder" :class="cls"
      @input="$emit('update:modelValue', $event.target.value)"
    />
    <p v-if="error" class="text-xs text-rose-500">{{ error }}</p>
    <p v-else-if="hint" class="text-xs text-zinc-400">{{ hint }}</p>
  </div>
</template>
