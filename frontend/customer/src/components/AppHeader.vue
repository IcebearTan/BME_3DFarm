<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Boxes, Sun, Moon, LogOut, Wallet, Plus, List, Printer } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { creditApi } from '@/api/credit'
import { authApi } from '@/api/auth'
import { toast } from '@/composables/useToast'

const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()
const balance = ref(null)

async function loadBalance() {
  try {
    balance.value = (await creditApi.balance()).data
  } catch {
    /* 静默（401 已由拦截器处理） */
  }
}

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
  // 刷新后 store.user 丢失，从 token 重建
  if (auth.token && !auth.user) {
    try {
      auth.setUser((await authApi.me()).data)
    } catch {
      /* 401 已处理 */
    }
  }
  loadBalance()
})

defineExpose({ loadBalance })
</script>

<template>
  <header
    class="sticky top-0 z-30 backdrop-blur bg-white/80 dark:bg-zinc-950/80 border-b border-zinc-100 dark:border-zinc-800"
  >
    <div class="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
      <router-link
        :to="{ name: 'dashboard' }"
        class="flex items-center gap-2 font-semibold text-zinc-900 dark:text-zinc-100"
      >
        <Boxes class="w-5 h-5" />
        <span>3D 农场</span>
      </router-link>

      <nav class="flex items-center gap-1">
        <router-link :to="{ name: 'dashboard' }" v-slot="{ isActive }" :class="navClass(isActive)">
          <Wallet class="w-4 h-4" /><span class="hidden sm:inline">额度</span>
        </router-link>
        <router-link :to="{ name: 'orders' }" v-slot="{ isActive }" :class="navClass(isActive)">
          <List class="w-4 h-4" /><span class="hidden sm:inline">订单</span>
        </router-link>
        <router-link :to="{ name: 'printers' }" v-slot="{ isActive }" :class="navClass(isActive)">
          <Printer class="w-4 h-4" /><span class="hidden sm:inline">打印机</span>
        </router-link>
        <router-link
          :to="{ name: 'new-order' }"
          class="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg text-sm bg-zinc-900 text-white dark:bg-white dark:text-zinc-900 hover:opacity-90"
        >
          <Plus class="w-4 h-4" /><span class="hidden sm:inline">下单</span>
        </router-link>
      </nav>

      <div class="flex items-center gap-2">
        <div
          v-if="balance"
          class="hidden sm:flex items-center gap-1 px-3 h-9 rounded-lg bg-emerald-50 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-300 text-sm font-medium"
        >
          <Wallet class="w-4 h-4" />{{ balance.available }}
        </div>
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
