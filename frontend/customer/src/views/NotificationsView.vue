<script setup>
// 通知中心：列表 + 筛选（全部/公告/订单/未读）+ 全部已读。
// 点订单类通知 → 标记已读 + 跳订单（带 order_id 自动展开详情）。
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useNotifications, formatRelativeTime } from '@/composables/useNotifications'

const router = useRouter()
const { notificationList, unreadCount, fetchNotifications, markAsRead, markAllAsRead } = useNotifications()

const filter = ref('all')
const filters = [
  { key: 'all', label: '全部' },
  { key: 'system', label: '公告' },
  { key: 'order', label: '订单' },
  { key: 'unread', label: '未读' },
]

async function load() {
  const params = {}
  if (filter.value === 'system' || filter.value === 'order') params.category = filter.value
  if (filter.value === 'unread') params.is_read = 'false'
  await fetchNotifications(params)
}
function setFilter(k) { filter.value = k; load() }

function onClick(n) {
  if (!n.is_read) markAsRead(n.id)
  if (n.category === 'order' && n.source_id) {
    router.push({ path: '/orders', query: { order: n.source_id } })
  }
}

onMounted(load)
</script>

<template>
  <div class="max-w-3xl mx-auto px-4 py-6 space-y-4">
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">通知中心</h1>
      <button v-if="unreadCount > 0" @click="markAllAsRead()"
        class="text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300">
        全部已读
      </button>
    </div>

    <!-- 筛选 -->
    <div class="flex gap-2">
      <button v-for="f in filters" :key="f.key" @click="setFilter(f.key)"
        :class="['px-3 py-1 rounded-full text-sm transition',
                 filter === f.key
                   ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900'
                   : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300']">
        {{ f.label }}
      </button>
    </div>

    <!-- 列表 -->
    <div v-if="notificationList.length" class="space-y-2">
      <div v-for="n in notificationList" :key="n.id" @click="onClick(n)"
        :class="['rounded-xl border p-4 transition cursor-pointer',
                 n.is_read
                   ? 'border-zinc-100 dark:border-zinc-800 bg-white dark:bg-zinc-900'
                   : 'border-rose-200 dark:border-rose-900/50 bg-rose-50/40 dark:bg-rose-950/20']">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p :class="['text-sm', n.is_read ? 'text-zinc-700 dark:text-zinc-200' : 'font-semibold text-zinc-900 dark:text-zinc-100']">
              {{ n.title }}
            </p>
            <p v-if="n.content" class="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{{ n.content }}</p>
          </div>
          <span class="text-xs text-zinc-400 shrink-0">{{ formatRelativeTime(n.created_at) }}</span>
        </div>
      </div>
    </div>
    <div v-else class="py-20 text-center text-sm text-zinc-400">暂无通知</div>
  </div>
</template>
