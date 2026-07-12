<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import AppInput from '@/components/AppInput.vue'
import AppButton from '@/components/AppButton.vue'
import AppCard from '@/components/AppCard.vue'
import FileDrop from '@/components/FileDrop.vue'
import { ordersApi } from '@/api/orders'
import { useForm } from '@/composables/useForm'
import { toast } from '@/composables/useToast'

const router = useRouter()
const form = reactive({
  material: '',
  color: '',
  quantity: 1,
  layer_height: '',
  nozzle_size: '',
  customer_note: '',
})
const file = ref(null)
const loading = ref(false)

const { errors, validate } = useForm(form, {
  material: [(v) => !!v || '请填写材料'],
  quantity: [(v) => Number(v) >= 1 || '至少 1 件'],
})

async function submit() {
  if (!validate()) return
  loading.value = true
  try {
    const fd = new FormData()
    fd.append('material', form.material)
    if (form.color) fd.append('color', form.color)
    fd.append('quantity', form.quantity)
    if (form.layer_height) fd.append('layer_height', form.layer_height)
    if (form.nozzle_size) fd.append('nozzle_size', form.nozzle_size)
    if (form.customer_note) fd.append('customer_note', form.customer_note)
    if (file.value) fd.append('file', file.value.file, file.value.name)

    const res = await ordersApi.create(fd)
    if (res.code === 200) {
      toast.success(res.message || '订单已创建')
      router.push({ name: 'orders' })
    } else {
      toast.error(res.message || '创建失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '网络错误')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-2xl mx-auto space-y-6">
    <div>
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">新建打印订单</h1>
      <p class="text-sm text-zinc-400 mt-1">填写需求并上传模型，管理员审核报价</p>
    </div>

    <AppCard>
      <form @submit.prevent="submit" class="space-y-5">
        <div class="grid sm:grid-cols-2 gap-4">
          <AppInput
            v-model="form.material" label="材料" placeholder="PLA / PETG / ABS"
            :error="errors.material" required
          />
          <AppInput v-model="form.color" label="颜色" placeholder="可选" />
          <AppInput
            v-model="form.quantity" type="number" label="数量" :error="errors.quantity"
          />
          <AppInput v-model="form.layer_height" label="层高" placeholder="0.2" />
          <AppInput v-model="form.nozzle_size" label="喷嘴" placeholder="0.4" />
        </div>

        <div>
          <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">备注</label>
          <textarea
            v-model="form.customer_note" rows="3" placeholder="给管理员的说明…"
            class="w-full px-3.5 py-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10 focus:border-zinc-400"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">模型文件</label>
          <FileDrop v-model="file" />
        </div>

        <div class="flex justify-end gap-2 pt-2">
          <AppButton variant="ghost" type="button" @click="router.back()">取消</AppButton>
          <AppButton type="submit" :loading="loading">提交订单</AppButton>
        </div>
      </form>
    </AppCard>
  </div>
</template>
