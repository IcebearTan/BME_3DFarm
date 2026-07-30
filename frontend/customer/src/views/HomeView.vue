<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight } from 'lucide-vue-next'
import FileDrop from '@/components/FileDrop.vue'
import PrinterGrid from '@/components/PrinterGrid.vue'
import { useOrderDraftStore } from '@/stores/orderDraft'

// 首页 = 下单上传入口 + 打印机网格（亮色白底）。
const router = useRouter()
const draft = useOrderDraftStore()
const dropFile = ref(null)

// 选中文件 → 草稿存文件 → 跳下单页（NewOrderView 挂载时取走并自动解析）
watch(dropFile, (nv) => {
  if (!nv) return
  draft.set(nv)
  dropFile.value = null
  router.push({ name: 'new-order' })
})
</script>

<template>
  <div class="space-y-8">
    <!-- 下单 hero（亮色白底） -->
    <section
      class="rounded-3xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 p-6 sm:p-8 shadow-sm"
    >
      <div class="max-w-xl">
        <h1 class="text-2xl sm:text-3xl font-semibold text-zinc-900 dark:text-zinc-100">上传模型，立即下单</h1>
        <p class="mt-2 text-sm text-zinc-400">
          拖拽 <code class="px-1 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300">.gcode.3mf</code>
          自动识别材料/颜色并报价
        </p>
      </div>
      <div class="mt-5">
        <FileDrop v-model="dropFile" />
      </div>
    </section>

    <!-- 打印机状态 -->
    <section>
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-xl font-semibold text-zinc-900 dark:text-zinc-100">打印机状态</h2>
          <p class="text-sm text-zinc-400 mt-0.5">农场打印机忙闲（每 30s 刷新）</p>
        </div>
        <router-link
          :to="{ name: 'printers' }"
          class="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 inline-flex items-center gap-1"
        >
          查看全部 <ArrowRight class="w-4 h-4" />
        </router-link>
      </div>
      <PrinterGrid />
    </section>
  </div>
</template>
