<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ShieldCheck, Sun, Moon, LogOut, ListOrdered, Coins, DollarSign, Printer, Megaphone } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { authApi } from '@/api/auth'
import { toast } from '@/composables/useToast'

const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()

function logout() {
  auth.logout()
  toast.info('已退出登录')
  router.push({ name: 'login' })
}

function navClass(isActive) {
  return [
    'inline-flex items-center gap-1.5 h-9 px-3 rounded-lg text-sm',
    isActive
      ? 'bg-zinc-100 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100'
      : 'text-zinc-600 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-900',
  ]
}

onMounted(async () => {
  if (auth.token && !auth.user) {
    try {
      auth.setUser((await authApi.me()).data)
    } catch {
      /* 401 已处理 */
    }
  }
})
</script>

<template>
  <header
    class="sticky top-0 z-30 backdrop-blur bg-white/80 dark:bg-zinc-950/80 border-b border-zinc-100 dark:border-zinc-800"
  >
    <div class="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
      <router-link
        :to="{ name: 'orders' }"
        class="flex items-center gap-2 font-semibold text-zinc-900 dark:text-zinc-100"
      >
        <ShieldCheck class="w-5 h-5" />
        <span>3D 农场 · 管理</span>
      </router-link>

      <nav class="flex items-center gap-1">
        <router-link :to="{ name: 'orders' }" v-slot="{ isActive }" :class="navClass(isActive)">
          <ListOrdered class="w-4 h-4" /><span class="hidden sm:inline">订单</span>
        </router-link>
        <router-link :to="{ name: 'printers' }" v-slot="{ isActive }" :class="navClass(isActive)">
          <Printer class="w-4 h-4" /><span class="hidden sm:inline">打印机</span>
        </router-link>
        <router-link :to="{ name: 'credit' }" v-slot="{ isActive }" :class="navClass(isActive)">
          <Coins class="w-4 h-4" /><span class="hidden sm:inline">发额度</span>
        </router-link>
        <router-link :to="{ name: 'pricing' }" v-slot="{ isActive }" :class="navClass(isActive)">
          <DollarSign class="w-4 h-4" /><span class="hidden sm:inline">费率</span>
        </router-link>
        <router-link :to="{ name: 'announce' }" v-slot="{ isActive }" :class="navClass(isActive)">
          <Megaphone class="w-4 h-4" /><span class="hidden sm:inline">公告</span>
        </router-link>
      </nav>

      <div class="flex items-center gap-2">
        <span v-if="auth.username" class="hidden sm:inline text-sm text-zinc-500">{{ auth.username }}</span>
        <button
          @click="theme.toggle()"
          class="p-2 rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          :title="theme.isDark ? '切换亮色' : '切换暗色'"
        >
          <Sun v-if="theme.isDark" class="w-4 h-4" /><Moon v-else class="w-4 h-4" />
        </button>
        <button
          @click="logout"
          class="p-2 rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          title="退出"
        >
          <LogOut class="w-4 h-4" />
        </button>
      </div>
    </div>
  </header>
</template>
