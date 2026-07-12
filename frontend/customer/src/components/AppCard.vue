<script setup>
defineProps({ title: String })
</script>

<template>
  <div class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-sm">
    <div
      v-if="title || $slots.header"
      class="px-6 py-4 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between"
    >
      <slot name="header">
        <h3 class="text-base font-semibold text-zinc-900 dark:text-zinc-100">{{ title }}</h3>
      </slot>
      <slot name="action" />
    </div>
    <div class="p-6"><slot /></div>
  </div>
</template>
