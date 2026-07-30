<script setup>
import PrinterGrid from '@/components/PrinterGrid.vue'
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">打印机状态</h1>
      <p class="text-sm text-zinc-400 mt-1">农场打印机忙闲（每 30s 刷新）</p>
    </div>
    <PrinterGrid />
  </div>
</template>
