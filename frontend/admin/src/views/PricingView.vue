<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Plus } from 'lucide-vue-next'
import AppCard from '@/components/AppCard.vue'
import AppButton from '@/components/AppButton.vue'
import AppInput from '@/components/AppInput.vue'
import { adminApi } from '@/api/admin'
import { toast } from '@/composables/useToast'

const items = ref([])
const loading = ref(false)
const editing = reactive({}) // id -> { value, label }
const saving = reactive({})
const showAdd = ref(false)
const newItem = reactive({ key: '', value: '', label: '' })

async function load() {
  loading.value = true
  try {
    const res = await adminApi.getPricing()
    items.value = res.data.items
    items.value.forEach((p) => {
      editing[p.id] = { value: p.value, label: p.label }
    })
  } finally {
    loading.value = false
  }
}

async function save(p) {
  const ed = editing[p.id]
  if (!ed || ed.value === undefined) return
  saving[p.id] = true
  try {
    const res = await adminApi.updatePricing(p.id, { value: ed.value, label: ed.label })
    if (res.code === 200) {
      toast.success('已更新')
      await load()
    } else {
      toast.error(res.message || '失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '失败')
  } finally {
    saving[p.id] = false
  }
}

async function add() {
  if (!newItem.key || !newItem.value) {
    toast.error('材料名/单价必填')
    return
  }
  const key = newItem.key.startsWith('material:') ? newItem.key : `material:${newItem.key}`
  try {
    const res = await adminApi.addPricing({
      key,
      value: Number(newItem.value),
      label: newItem.label || newItem.key,
      unit: 'g',
    })
    if (res.code === 200) {
      toast.success('已添加')
      showAdd.value = false
      newItem.key = ''
      newItem.value = ''
      newItem.label = ''
      await load()
    } else {
      toast.error(res.message || '失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '失败')
  }
}

onMounted(load)
</script>

<template>
  <div class="max-w-3xl space-y-6">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">费率配置</h1>
        <p class="text-sm text-zinc-400 mt-1">
          credit = 基础费 + 克重 × 材料单价 + (时长/3600) × 机时单价
        </p>
      </div>
      <AppButton @click="showAdd = !showAdd"><Plus class="w-4 h-4" />加材料</AppButton>
    </div>

    <AppCard v-if="showAdd" title="新材料">
      <div class="grid sm:grid-cols-3 gap-3">
        <AppInput v-model="newItem.key" label="材料名" placeholder="NYLON（自动加 material: 前缀）" />
        <AppInput v-model="newItem.value" label="单价（元/克）" type="number" placeholder="0.8" />
        <AppInput v-model="newItem.label" label="显示名" placeholder="如 尼龙" />
      </div>
      <div class="flex justify-end mt-3 gap-2">
        <AppButton variant="ghost" @click="showAdd = false">取消</AppButton>
        <AppButton @click="add">添加</AppButton>
      </div>
    </AppCard>

    <AppCard title="费率列表">
      <div v-if="loading" class="py-8 text-center text-sm text-zinc-400">加载中…</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs text-zinc-400 border-b border-zinc-100 dark:border-zinc-800">
            <th class="py-2 font-medium">配置项</th>
            <th class="py-2 font-medium">值</th>
            <th class="py-2 font-medium">单位</th>
            <th class="py-2 font-medium text-right">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-zinc-50 dark:divide-zinc-800/60">
          <tr v-for="p in items" :key="p.id">
            <td class="py-3">
              <p class="font-medium text-zinc-900 dark:text-zinc-100">{{ p.label }}</p>
              <p class="text-xs text-zinc-400 font-mono">{{ p.key }}</p>
            </td>
            <td class="py-3">
              <input
                v-model="editing[p.id].value" type="number"
                class="w-24 h-8 px-2 rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10"
              />
            </td>
            <td class="py-3 text-zinc-400">{{ p.unit }}</td>
            <td class="py-3 text-right">
              <AppButton size="xs" :loading="saving[p.id]" @click="save(p)">保存</AppButton>
            </td>
          </tr>
        </tbody>
      </table>
    </AppCard>
  </div>
</template>
