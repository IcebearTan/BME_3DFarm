<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

// loader: () => Promise<Blob>（如 () => adminApi.orderPreview(orderId)）
const props = defineProps({
  loader: { type: Function, required: true },
  alt: { type: String, default: '预览图' },
})

const url = ref(null)
const loading = ref(false)
let objectUrl = null

async function load() {
  loading.value = true
  try {
    const blob = await props.loader()
    objectUrl = URL.createObjectURL(blob)
    url.value = objectUrl
  } catch {
    // 无预览图（404）静默：不渲染任何节点
  } finally {
    loading.value = false
  }
}
onMounted(load)
onUnmounted(() => {
  if (objectUrl) URL.revokeObjectURL(objectUrl)
})
</script>

<template>
  <div
    v-if="loading"
    class="w-full aspect-square rounded-lg bg-zinc-100 dark:bg-zinc-800 animate-pulse"
  />
  <img
    v-else-if="url" :src="url" :alt="alt"
    class="w-full aspect-square object-contain rounded-lg bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-100 dark:border-zinc-800"
  />
  <!-- 无图：不渲染 -->
</template>
