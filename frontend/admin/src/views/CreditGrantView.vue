<script setup>
import { ref, reactive } from 'vue'
import { Coins, Search } from 'lucide-vue-next'
import AppCard from '@/components/AppCard.vue'
import AppInput from '@/components/AppInput.vue'
import AppButton from '@/components/AppButton.vue'
import { adminApi } from '@/api/admin'
import { useForm } from '@/composables/useForm'
import { toast } from '@/composables/useToast'

const selected = ref(null) // { id, email, username, role, available }
const query = ref('')
const results = ref([])
const searching = ref(false)
const form = reactive({ amount: '', reason: '管理员发放' })
const loading = ref(false)

const { errors, validate } = useForm(form, {
  amount: [(v) => !!v || '请输入金额', (v) => Number(v) > 0 || '金额必须为正数'],
})

let searchTimer = null
function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    if (!query.value.trim()) {
      results.value = []
      return
    }
    searching.value = true
    try {
      const res = await adminApi.users(query.value.trim())
      results.value = res.data.items
    } finally {
      searching.value = false
    }
  }, 300)
}
function pick(u) {
  selected.value = u
  query.value = u.email
  results.value = []
}

async function refreshSelected() {
  if (!selected.value) return
  try {
    const fresh = await adminApi.users(selected.value.email)
    if (fresh.data.items[0]) selected.value = fresh.data.items[0]
  } catch {
    /* 忽略 */
  }
}

async function submit() {
  if (!selected.value) {
    toast.error('请先搜索并选择用户')
    return
  }
  if (!validate()) return
  loading.value = true
  try {
    const res = await adminApi.grantCredit({
      user_id: selected.value.id,
      amount: Number(form.amount),
      reason: form.reason,
    })
    if (res.code === 200) {
      toast.success(res.message || '发放成功')
      form.amount = ''
      await refreshSelected()
    } else {
      toast.error(res.message || '发放失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '网络错误')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-xl space-y-6">
    <div>
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">发放 credit</h1>
      <p class="text-sm text-zinc-400 mt-1">搜索用户 → 选中 → 发放（credit 唯一增加途径）</p>
    </div>

    <AppCard title="选择用户">
      <div class="relative">
        <div class="relative">
          <Search class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400" />
          <input
            v-model="query" @input="onSearch" placeholder="搜索 email 或用户名…"
            class="w-full h-10 pl-9 pr-3 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10"
          />
        </div>
        <div
          v-if="searching || results.length"
          class="absolute z-10 mt-1 w-full rounded-lg bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-lg max-h-64 overflow-y-auto"
        >
          <div v-if="searching" class="px-3 py-2 text-sm text-zinc-400">搜索中…</div>
          <button
            v-for="u in results" :key="u.id" @click="pick(u)"
            class="w-full text-left px-3 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center justify-between gap-3"
          >
            <div class="min-w-0">
              <p class="text-sm text-zinc-900 dark:text-zinc-100 truncate">
                {{ u.username }} <span class="text-zinc-400">· {{ u.email }}</span>
              </p>
              <p class="text-xs text-zinc-400">#{{ u.id }} · {{ u.role }}</p>
            </div>
            <span class="text-sm text-emerald-600 shrink-0">{{ u.available }}</span>
          </button>
        </div>
      </div>

      <div
        v-if="selected"
        class="mt-4 rounded-lg bg-zinc-50 dark:bg-zinc-800/60 px-4 py-3 flex items-center justify-between"
      >
        <div>
          <p class="text-sm font-medium text-zinc-900 dark:text-zinc-100">
            {{ selected.username }}（{{ selected.email }}）
          </p>
          <p class="text-xs text-zinc-400">#{{ selected.id }}</p>
        </div>
        <div class="text-right">
          <p class="text-xs text-zinc-400">当前可用</p>
          <p class="text-sm font-semibold text-emerald-600">{{ selected.available }}</p>
        </div>
      </div>
    </AppCard>

    <AppCard title="发放金额">
      <form @submit.prevent="submit" class="space-y-4">
        <AppInput
          v-model="form.amount" label="金额（credit）" type="number" placeholder="正数"
          :error="errors.amount" required
        />
        <AppInput v-model="form.reason" label="原因" placeholder="可选备注" />
        <div class="flex justify-end pt-2">
          <AppButton type="submit" :loading="loading" :disabled="!selected">
            <Coins class="w-4 h-4" />发放
          </AppButton>
        </div>
      </form>
    </AppCard>
  </div>
</template>
