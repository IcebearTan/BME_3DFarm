<script setup>
import { ref, reactive } from 'vue'
import { Coins } from 'lucide-vue-next'
import AppCard from '@/components/AppCard.vue'
import AppInput from '@/components/AppInput.vue'
import AppButton from '@/components/AppButton.vue'
import { adminApi } from '@/api/admin'
import { useForm } from '@/composables/useForm'
import { toast } from '@/composables/useToast'

const form = reactive({ user_id: '', amount: '', reason: '管理员发放' })
const loading = ref(false)

const { errors, validate } = useForm(form, {
  user_id: [(v) => !!v || '请输入用户 ID', (v) => Number(v) > 0 || 'ID 必须为正数'],
  amount: [(v) => !!v || '请输入金额', (v) => Number(v) > 0 || '金额必须为正数'],
})

async function submit() {
  if (!validate()) return
  loading.value = true
  try {
    const res = await adminApi.grantCredit({
      user_id: Number(form.user_id),
      amount: Number(form.amount),
      reason: form.reason,
    })
    if (res.code === 200) {
      toast.success(res.message || '发放成功')
      form.amount = ''
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
      <p class="text-sm text-zinc-400 mt-1">手动给用户充值（本平台 credit 唯一增加途径）</p>
    </div>

    <AppCard>
      <form @submit.prevent="submit" class="space-y-4">
        <AppInput
          v-model="form.user_id" label="用户 ID" type="number" placeholder="数字 ID"
          :error="errors.user_id" required
        />
        <AppInput
          v-model="form.amount" label="金额（credit）" type="number" placeholder="正数"
          :error="errors.amount" required
        />
        <AppInput v-model="form.reason" label="原因" placeholder="可选备注" />
        <div class="flex justify-end pt-2">
          <AppButton type="submit" :loading="loading">
            <Coins class="w-4 h-4" />发放
          </AppButton>
        </div>
      </form>
    </AppCard>
  </div>
</template>
