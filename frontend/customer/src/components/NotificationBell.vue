<script setup>
// 顶栏铃铛：badge 未读数 + hover 预览最近 5 条 + 30s 轮询。
// 3DFarm Tailwind 风格（非 BME 的 DewPopover），hover 浮层用 group 实现。
import { computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Bell } from 'lucide-vue-next'
import { useNotifications, formatRelativeTime } from '@/composables/useNotifications'

const router = useRouter()
const { notificationList, unreadCount, startPolling, stopPolling } = useNotifications()
const recent = computed(() => notificationList.value.slice(0, 5))

onMounted(() => startPolling(30000))
onBeforeUnmount(() => stopPolling())
</script>

<template>
  <div class="relative group">
    <button
      @click="router.push('/notifications')"
      class="relative p-2 rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition"
      title="通知"
    >
      <Bell class="w-4 h-4" />
      <span v-if="unreadCount > 0"
        class="absolute top-1 right-1 min-w-[16px] h-4 px-1 rounded-full bg-rose-500 text-white text-[10px] font-bold leading-4 text-center">
        {{ unreadCount > 99 ? '99+' : unreadCount }}
      </span>
    </button>

    <!-- hover 预览浮层：外层紧贴铃铛（无 gap 保证 hover 连续），pt 桥接视觉间距 -->
    <div class="absolute right-0 top-full z-40 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition origin-top">
      <div class="pt-1.5">
        <div class="w-72 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-lg">
          <div class="flex items-center justify-between px-4 py-3 border-b border-zinc-100 dark:border-zinc-800">
            <span class="text-sm font-semibold text-zinc-900 dark:text-zinc-100">通知</span>
            <span v-if="unreadCount > 0" class="text-xs text-rose-500">{{ unreadCount }} 条未读</span>
          </div>
          <div v-if="recent.length" class="max-h-72 overflow-y-auto py-1">
            <button v-for="n in recent" :key="n.id" @click="router.push('/notifications')"
              :class="['w-full text-left px-4 py-2 flex items-start gap-2 hover:bg-zinc-50 dark:hover:bg-zinc-800',
                       !n.is_read && 'bg-rose-50/50 dark:bg-rose-950/20']">
              <span v-if="!n.is_read" class="mt-1.5 w-1.5 h-1.5 rounded-full bg-rose-500 shrink-0"></span>
              <span v-else class="w-1.5 shrink-0"></span>
              <div class="min-w-0">
                <p :class="['text-sm truncate', !n.is_read ? 'font-medium text-zinc-900 dark:text-zinc-100' : 'text-zinc-600 dark:text-zinc-300']">
                  {{ n.title }}
                </p>
                <p class="text-xs text-zinc-400">{{ formatRelativeTime(n.created_at) }}</p>
              </div>
            </button>
          </div>
          <div v-else class="py-8 text-center text-sm text-zinc-400">暂无新通知</div>
          <button @click="router.push('/notifications')"
            class="w-full text-center py-2.5 text-sm text-zinc-500 hover:bg-zinc-50 dark:hover:bg-zinc-800 border-t border-zinc-100 dark:border-zinc-800">
            查看全部通知
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
