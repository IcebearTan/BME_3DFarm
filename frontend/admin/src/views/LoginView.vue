<script setup>
import { ref, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ShieldCheck } from 'lucide-vue-next'
import AppInput from '@/components/AppInput.vue'
import AppButton from '@/components/AppButton.vue'
import { useForm } from '@/composables/useForm'
import { authApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { toast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const form = reactive({ email: '', password: '' })
const loading = ref(false)

const { errors, validate } = useForm(form, {
  email: [(v) => !!v || '请输入邮箱'],
  password: [(v) => !!v || '请输入密码'],
})

async function submit() {
  if (!validate()) return
  loading.value = true
  try {
    const res = await authApi.login({ email: form.email, password: form.password })
    if (res.code !== 200) {
      toast.error(res.message || '登录失败')
      return
    }
    if (res.role !== 'admin') {
      toast.error('该账号非管理员，无权登录后台')
      return
    }
    auth.setAuth(res.token, {
      email: form.email,
      role: res.role,
      username: res.User_Name || form.email,
    })
    // 拉完整 user（含 id 等）
    try {
      auth.setUser((await authApi.me()).data)
    } catch {
      /* 忽略 */
    }
    toast.success('登录成功')
    router.push(route.query.redirect || { name: 'orders' })
  } catch (e) {
    toast.error(e.response?.data?.message || '网络错误')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center px-4">
    <div class="w-full max-w-sm">
      <div class="text-center mb-8">
        <div
          class="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-zinc-900 text-white dark:bg-white dark:text-zinc-900 mb-3"
        >
          <ShieldCheck class="w-6 h-6" />
        </div>
        <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">BME 3DFarm 管理后台</h1>
        <p class="text-sm text-zinc-400 mt-1">仅管理员可登录</p>
      </div>

      <div
        class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-sm p-6"
      >
        <form @submit.prevent="submit" class="space-y-4">
          <AppInput v-model="form.email" label="邮箱" type="email" placeholder="admin@example.com" :error="errors.email" required />
          <AppInput v-model="form.password" label="密码" type="password" placeholder="••••••" :error="errors.password" required />
          <AppButton type="submit" block size="lg" :loading="loading">登录</AppButton>
        </form>
        <p class="mt-4 text-xs text-zinc-400 text-center">
          首次使用需在数据库将 user.role 改为 'admin'
        </p>
      </div>
    </div>
  </div>
</template>
