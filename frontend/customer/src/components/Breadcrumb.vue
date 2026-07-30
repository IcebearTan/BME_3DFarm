<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

// 按 path 分段生成面包屑——logo「3D 农场」充当「首页」首项，
// 这里从当前层级第一段开始，首个分隔符紧跟 logo。新增页面在这里补标题即可。
const SEG_TITLES = {
  wallet: '钱包',
  orders: '我的订单',
  new: '新建订单',
  printers: '打印机',
}

const route = useRoute()
const crumbs = computed(() => {
  const items = []
  let acc = ''
  for (const s of route.path.split('/').filter(Boolean)) {
    acc += '/' + s
    items.push({ title: SEG_TITLES[s] || s, to: acc })
  }
  return items
})
</script>

<template>
  <nav v-if="crumbs.length" class="flex items-center gap-1.5 text-sm min-w-0" aria-label="面包屑">
    <template v-for="(c, i) in crumbs" :key="c.to">
      <span class="text-zinc-300 dark:text-zinc-600">/</span>
      <router-link
        v-if="i < crumbs.length - 1"
        :to="c.to"
        class="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"
      >{{ c.title }}</router-link>
      <span v-else class="text-zinc-900 dark:text-zinc-100 font-semibold truncate">{{ c.title }}</span>
    </template>
  </nav>
</template>
