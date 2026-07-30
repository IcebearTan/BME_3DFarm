<script setup>
import { onMounted } from 'vue'
import { Boxes, Sun, Moon } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { authApi } from '@/api/auth'
import UserMenu from '@/components/UserMenu.vue'
import Breadcrumb from '@/components/Breadcrumb.vue'
import NotificationBell from '@/components/NotificationBell.vue'

const auth = useAuthStore()
const theme = useThemeStore()

onMounted(async () => {
  // 刷新后 store.user 丢失，从 token 重建
  if (auth.token && !auth.user) {
    try {
      auth.setUser((await authApi.me()).data)
    } catch {
      /* 401 已由拦截器处理 */
    }
  }
})
</script>

<template>
  <header
    class="sticky top-0 z-30 backdrop-blur bg-white/80 dark:bg-zinc-950/80 border-b border-zinc-100 dark:border-zinc-800"
  >
    <div class="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
      <div class="flex items-center gap-2 min-w-0">
        <router-link
          :to="{ name: 'home' }"
          class="flex items-center gap-2 font-semibold text-zinc-900 dark:text-zinc-100 shrink-0"
        >
          <Boxes class="w-5 h-5" />
          <span>3D 农场</span>
        </router-link>
        <Breadcrumb />
      </div>

      <div class="flex items-center gap-2">
        <button
          @click="theme.toggle()"
          class="p-2 rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          :title="theme.isDark ? '切换亮色' : '切换暗色'"
        >
          <Sun v-if="theme.isDark" class="w-4 h-4" /><Moon v-else class="w-4 h-4" />
        </button>
        <NotificationBell />
        <UserMenu />
      </div>
    </div>
  </header>
</template>
