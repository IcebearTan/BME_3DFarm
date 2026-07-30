<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Wallet, List, LogOut } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { creditApi } from '@/api/credit'
import { toast } from '@/composables/useToast'

// 导航栏右侧的头像入口：hover 弹出个人中心 popover。
// 额度展示与流水入口合一（点额度行即进 /wallet），避免功能重复。
const router = useRouter()
const auth = useAuthStore()
const balance = ref(null)
const open = ref(false)
let closeTimer = null

const initial = computed(() => {
  const name = auth.username || auth.user?.email || '?'
  return name.charAt(0).toUpperCase()
})

async function loadBalance() {
  try {
    balance.value = (await creditApi.balance()).data
  } catch {
    /* 401 已由拦截器处理 */
  }
}

function show() {
  if (closeTimer) { clearTimeout(closeTimer); closeTimer = null }
  open.value = true
}
function hide() {
  // 留一点 grace，避免鼠标在头像与面板之间移动时意外关闭
  closeTimer = setTimeout(() => { open.value = false }, 120)
}
function toggle() {
  open.value = !open.value
}

function go(name) {
  open.value = false
  router.push({ name })
}
function logout() {
  open.value = false
  auth.logout()
  toast.info('已退出登录')
  router.push({ name: 'login' })
}

onMounted(loadBalance)
defineExpose({ loadBalance })
</script>

<template>
  <div class="relative" @mouseenter="show" @mouseleave="hide">
    <button
      @click="toggle"
      class="w-9 h-9 rounded-full flex items-center justify-center text-sm font-semibold
             bg-zinc-900 text-white dark:bg-white dark:text-zinc-900 hover:opacity-90 transition-opacity"
      :title="auth.username || auth.user?.email"
    >
      {{ initial }}
    </button>

    <transition
      enter-active-class="transition duration-100 ease-out origin-top"
      enter-from-class="opacity-0 scale-95"
      enter-to-class="opacity-100 scale-100"
      leave-active-class="transition duration-75 ease-in origin-top"
      leave-from-class="opacity-100 scale-100"
      leave-to-class="opacity-0 scale-95"
    >
      <div
        v-if="open"
        class="absolute right-0 mt-2 w-64 rounded-2xl bg-white dark:bg-zinc-900
               border border-zinc-100 dark:border-zinc-800 shadow-lg overflow-hidden"
      >
        <!-- 用户信息 -->
        <div class="px-4 py-3 border-b border-zinc-100 dark:border-zinc-800">
          <p class="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate">
            {{ auth.username || '未命名' }}
          </p>
          <p class="text-xs text-zinc-400 truncate">{{ auth.user?.email }}</p>
        </div>

        <!-- 额度（展示 + 入口合一：点这里进 /wallet 看流水） -->
        <button
          @click="go('wallet')"
          class="w-full flex items-center justify-between px-4 py-3 hover:bg-zinc-50 dark:hover:bg-zinc-800"
        >
          <span class="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-300">
            <Wallet class="w-4 h-4" />可用额度
          </span>
          <span class="flex items-center gap-1 text-sm font-semibold text-emerald-600">
            {{ balance?.available ?? '—' }}
            <span class="text-zinc-300 dark:text-zinc-600 font-normal">›</span>
          </span>
        </button>
        <div
          v-if="balance && Number(balance.frozen) > 0"
          class="px-4 pb-2 -mt-1 text-right text-xs text-amber-600"
        >
          冻结 {{ balance.frozen }}
        </div>

        <!-- 订单入口 -->
        <div class="border-t border-zinc-100 dark:border-zinc-800 py-1">
          <button
            @click="go('orders')"
            class="w-full flex items-center gap-2.5 px-4 py-2 text-sm text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800"
          >
            <List class="w-4 h-4 text-zinc-400" />订单记录
          </button>
        </div>

        <!-- 退出 -->
        <div class="border-t border-zinc-100 dark:border-zinc-800 py-1">
          <button
            @click="logout"
            class="w-full flex items-center gap-2.5 px-4 py-2 text-sm text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40"
          >
            <LogOut class="w-4 h-4" />退出登录
          </button>
        </div>
      </div>
    </transition>
  </div>
</template>
