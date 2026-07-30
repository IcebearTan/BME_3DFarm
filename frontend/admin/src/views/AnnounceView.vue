<script setup>
// 发布公告：全员 customer 或指定用户（搜索 + chips 多选，复用 CreditGrantView 的多选模式）。
import { ref, reactive, computed } from 'vue'
import { Megaphone, Search, X, Check } from 'lucide-vue-next'
import AppCard from '@/components/AppCard.vue'
import AppInput from '@/components/AppInput.vue'
import AppButton from '@/components/AppButton.vue'
import { adminApi } from '@/api/admin'
import { useForm } from '@/composables/useForm'
import { toast } from '@/composables/useToast'

const target = ref('all')               // all（全员 customer）/ selected（指定用户）
const selected = ref([])
const query = ref('')
const results = ref([])
const searching = ref(false)
const form = reactive({ title: '', content: '' })
const loading = ref(false)
const lastResult = ref(null)

const { errors, validate } = useForm(form, {
  title: [(v) => !!v || '请输入标题'],
})

const selectedIds = computed(() => new Set(selected.value.map((u) => u.id)))

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

async function submit() {
  if (target.value === 'selected' && !selected.value.length) {
    toast.error('请先搜索并选择用户')
    return
  }
  if (!validate()) return
  loading.value = true
  try {
    const res = await adminApi.announce({
      title: form.title,
      content: form.content,
      ...(target.value === 'selected' ? { user_ids: selected.value.map((u) => u.id) } : {}),
    })
    if (res.code === 200) {
      lastResult.value = res.data
      toast.success(res.message)
      form.title = ''
      form.content = ''
      clearSelected()
    } else {
      toast.error(res.message || '发布失败')
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
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">发布公告</h1>
      <p class="text-sm text-zinc-400 mt-1">向客户发送站内公告（出现在通知中心）</p>
    </div>

    <AppCard title="接收对象">
      <div class="flex gap-2 mb-4">
        <button @click="target = 'all'"
          :class="['px-3 py-1.5 rounded-lg text-sm transition',
                   target === 'all' ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900' : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300']">
          全部客户
        </button>
        <button @click="target = 'selected'"
          :class="['px-3 py-1.5 rounded-lg text-sm transition',
                   target === 'selected' ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900' : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300']">
          指定用户
        </button>
      </div>

      <div v-if="target === 'selected'">
        <div class="relative">
          <Search class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400" />
          <input v-model="query" @input="onSearch" placeholder="搜索 email 或用户名…"
            class="w-full h-10 pl-9 pr-3 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10" />
        </div>
        <div v-if="searching || results.length" class="mt-2 rounded-lg border border-zinc-100 dark:border-zinc-800 overflow-hidden">
          <div v-if="searching" class="px-3 py-2 text-sm text-zinc-400">搜索中…</div>
          <div v-else class="max-h-64 overflow-y-auto divide-y divide-zinc-100 dark:divide-zinc-800">
            <button v-for="u in results" :key="u.id" @click="toggle(u)"
              :class="['w-full text-left px-3 py-2 flex items-center gap-3',
                       selectedIds.has(u.id) ? 'bg-emerald-50 dark:bg-emerald-950/40' : 'hover:bg-zinc-50 dark:hover:bg-zinc-800']">
              <span :class="['w-4 h-4 rounded shrink-0 flex items-center justify-center border',
                             selectedIds.has(u.id) ? 'bg-emerald-500 border-emerald-500' : 'border-zinc-300 dark:border-zinc-600']">
                <Check v-if="selectedIds.has(u.id)" class="w-3 h-3 text-white" />
              </span>
              <div class="min-w-0 flex-1">
                <p class="text-sm text-zinc-900 dark:text-zinc-100 truncate">{{ u.username }} <span class="text-zinc-400">· {{ u.email }}</span></p>
                <p class="text-xs text-zinc-400">#{{ u.id }} · {{ u.role }}</p>
              </div>
            </button>
          </div>
        </div>
        <div v-if="selected.length" class="mt-3">
          <div class="flex items-center justify-between mb-2">
            <p class="text-xs text-zinc-500 dark:text-zinc-400">已选 <span class="font-semibold text-zinc-700 dark:text-zinc-200">{{ selected.length }}</span> 人</p>
            <button @click="clearSelected" class="text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300">清空</button>
          </div>
          <div class="flex flex-wrap gap-2">
            <span v-for="u in selected" :key="u.id"
              class="inline-flex items-center gap-1.5 pl-2.5 pr-1.5 py-1 rounded-full bg-zinc-100 dark:bg-zinc-800 text-xs text-zinc-700 dark:text-zinc-200">
              {{ u.username }}
              <button @click="removeSelected(u.id)" class="p-0.5 rounded-full hover:bg-zinc-200 dark:hover:bg-zinc-700"><X class="w-3 h-3" /></button>
            </span>
          </div>
        </div>
      </div>
      <p v-else class="text-sm text-zinc-400">将发送给所有客户（不含管理员）</p>
    </AppCard>

    <AppCard title="公告内容">
      <form @submit.prevent="submit" class="space-y-4">
        <AppInput v-model="form.title" label="标题" placeholder="公告标题" :error="errors.title" required />
        <div>
          <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">内容</label>
          <textarea v-model="form.content" rows="4" placeholder="公告正文…"
            class="w-full px-3 py-2 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10"></textarea>
        </div>
        <div class="flex justify-end pt-2">
          <AppButton type="submit" :loading="loading"
            :disabled="target === 'selected' && !selected.length">
            <Megaphone class="w-4 h-4" />发布
          </AppButton>
        </div>
      </form>
      <p v-if="lastResult" class="mt-3 text-xs text-emerald-600">上次发布：已向 {{ lastResult.created_count }} 名用户发送</p>
    </AppCard>
  </div>
</template>
