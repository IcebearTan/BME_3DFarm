<script setup>
import { ref, reactive, computed } from 'vue'
import { Coins, Search, X, Check, RotateCw } from 'lucide-vue-next'
import AppCard from '@/components/AppCard.vue'
import AppInput from '@/components/AppInput.vue'
import AppButton from '@/components/AppButton.vue'
import { adminApi } from '@/api/admin'
import { useForm } from '@/composables/useForm'
import { toast } from '@/composables/useToast'

// 多选：selected 是已选用户数组；lastResult 存上次批量结果（展示明细 + 失败可重试）
const selected = ref([]) // [{ id, email, username, role, available }]
const lastResult = ref(null) // { batch_id, total, success_count, fail_count, succeeded:[], failed:[] }
const query = ref('')
const results = ref([])
const searching = ref(false)
const form = reactive({ amount: '', reason: '管理员发放' })
const loading = ref(false)

const { errors, validate } = useForm(form, {
  amount: [(v) => !!v || '请输入金额', (v) => Number(v) > 0 || '金额必须为正数'],
})

const selectedIds = computed(() => new Set(selected.value.map((u) => u.id)))
const totalCredit = computed(() =>
  Number(form.amount) > 0 ? (Number(form.amount) * selected.value.length).toFixed(2) : null
)

let searchTimer = null
function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    if (!query.value.trim()) { results.value = []; return }
    searching.value = true
    try {
      const res = await adminApi.users(query.value.trim())
      results.value = res.data.items
    } finally {
      searching.value = false
    }
  }, 300)
}

function toggle(u) {
  const idx = selected.value.findIndex((s) => s.id === u.id)
  if (idx >= 0) selected.value.splice(idx, 1)
  else selected.value.push({ ...u })
}
function removeSelected(id) {
  selected.value = selected.value.filter((s) => s.id !== id)
}
function clearSelected() {
  selected.value = []
}

async function refreshSelected() {
  if (!selected.value.length) return
  try {
    // 逐人按 email 搜回最新余额（发完后 available 会变）
    selected.value = await Promise.all(
      selected.value.map(async (u) => {
        const r = await adminApi.users(u.email)
        return r.data.items.find((x) => x.id === u.id) || u
      })
    )
  } catch {
    /* 忽略 */
  }
}

async function submit(retryFailed = false) {
  let user_ids
  if (retryFailed) {
    if (!lastResult.value?.failed?.length) return
    user_ids = lastResult.value.failed.map((f) => f.user_id)
  } else {
    if (!selected.value.length) {
      toast.error('请先搜索并选择用户')
      return
    }
    if (!validate()) return
    user_ids = selected.value.map((u) => u.id)
  }
  loading.value = true
  try {
    const res = await adminApi.grantCreditBatch({
      user_ids,
      amount: Number(form.amount),
      reason: form.reason,
      // 重试时复用上次 batch_id：已成功的幂等 replay，仅失败的重试
      ...(retryFailed ? { batch_id: lastResult.value.batch_id } : {}),
    })
    if (res.code === 200) {
      lastResult.value = res.data
      toast.success(res.message)
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
      <p class="text-sm text-zinc-400 mt-1">搜索用户 → 勾选多个 → 统一发放（credit 唯一增加途径）</p>
    </div>

    <AppCard title="选择用户">
      <!-- 搜索 -->
      <div class="relative">
        <Search class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400" />
        <input
          v-model="query" @input="onSearch" placeholder="搜索 email 或用户名…"
          class="w-full h-10 pl-9 pr-3 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10"
        />
      </div>

      <!-- 搜索结果：可勾选多选，已选高亮 + 打勾 -->
      <div
        v-if="searching || results.length"
        class="mt-2 rounded-lg border border-zinc-100 dark:border-zinc-800 overflow-hidden"
      >
        <div v-if="searching" class="px-3 py-2 text-sm text-zinc-400">搜索中…</div>
        <div v-else class="max-h-64 overflow-y-auto divide-y divide-zinc-100 dark:divide-zinc-800">
          <button
            v-for="u in results" :key="u.id" @click="toggle(u)"
            :class="['w-full text-left px-3 py-2 flex items-center gap-3',
                     selectedIds.has(u.id) ? 'bg-emerald-50 dark:bg-emerald-950/40' : 'hover:bg-zinc-50 dark:hover:bg-zinc-800']"
          >
            <span :class="['w-4 h-4 rounded shrink-0 flex items-center justify-center border',
                           selectedIds.has(u.id)
                             ? 'bg-emerald-500 border-emerald-500'
                             : 'border-zinc-300 dark:border-zinc-600']">
              <Check v-if="selectedIds.has(u.id)" class="w-3 h-3 text-white" />
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-sm text-zinc-900 dark:text-zinc-100 truncate">
                {{ u.username }} <span class="text-zinc-400">· {{ u.email }}</span>
              </p>
              <p class="text-xs text-zinc-400">#{{ u.id }} · {{ u.role }}</p>
            </div>
            <span class="text-sm text-emerald-600 shrink-0">{{ u.available }}</span>
          </button>
        </div>
      </div>

      <!-- 已选区：chips + 计数 + 合计 -->
      <div v-if="selected.length" class="mt-3">
        <div class="flex items-center justify-between mb-2">
          <p class="text-xs text-zinc-500 dark:text-zinc-400">
            已选 <span class="font-semibold text-zinc-700 dark:text-zinc-200">{{ selected.length }}</span> 人
            <span v-if="totalCredit" class="ml-2 text-zinc-400">· 合计 {{ totalCredit }} credit</span>
          </p>
          <button @click="clearSelected" class="text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300">清空</button>
        </div>
        <div class="flex flex-wrap gap-2">
          <span
            v-for="u in selected" :key="u.id"
            class="inline-flex items-center gap-1.5 pl-2.5 pr-1.5 py-1 rounded-full bg-zinc-100 dark:bg-zinc-800 text-xs text-zinc-700 dark:text-zinc-200"
          >
            {{ u.username }}
            <button @click="removeSelected(u.id)" class="p-0.5 rounded-full hover:bg-zinc-200 dark:hover:bg-zinc-700">
              <X class="w-3 h-3" />
            </button>
          </span>
        </div>
      </div>
    </AppCard>

    <AppCard title="发放金额">
      <form @submit.prevent="submit(false)" class="space-y-4">
        <AppInput
          v-model="form.amount" label="金额（credit / 人）" type="number" placeholder="正数"
          :error="errors.amount" required
        />
        <AppInput v-model="form.reason" label="原因" placeholder="可选备注" />
        <div class="flex justify-end pt-2">
          <AppButton type="submit" :loading="loading" :disabled="!selected.length">
            <Coins class="w-4 h-4" />发放给 {{ selected.length }} 人
          </AppButton>
        </div>
      </form>
    </AppCard>

    <!-- 批量结果明细 -->
    <AppCard v-if="lastResult" title="发放结果">
      <div class="flex items-center gap-4 text-sm">
        <span class="text-emerald-600 font-medium">成功 {{ lastResult.success_count }}</span>
        <span v-if="lastResult.fail_count" class="text-rose-600 font-medium">失败 {{ lastResult.fail_count }}</span>
        <span class="text-zinc-400">/ 共 {{ lastResult.total }}</span>
      </div>

      <div v-if="lastResult.failed.length" class="mt-3 space-y-2">
        <div
          v-for="f in lastResult.failed" :key="f.user_id"
          class="flex items-center justify-between rounded-lg bg-rose-50 dark:bg-rose-950/30 px-3 py-2 text-sm"
        >
          <span class="text-zinc-700 dark:text-zinc-200 truncate">
            {{ f.username || '未知' }} <span class="text-zinc-400">#{{ f.user_id }}</span>
          </span>
          <span class="text-rose-600 text-xs shrink-0 ml-2">{{ f.message }}</span>
        </div>
        <div class="flex justify-end pt-1">
          <AppButton variant="ghost" size="sm" :loading="loading" @click="submit(true)">
            <RotateCw class="w-4 h-4" />重试失败的 {{ lastResult.fail_count }} 个
          </AppButton>
        </div>
      </div>
    </AppCard>
  </div>
</template>
