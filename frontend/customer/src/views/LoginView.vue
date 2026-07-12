<script setup>
import { ref, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Boxes } from 'lucide-vue-next'
import AppInput from '@/components/AppInput.vue'
import AppButton from '@/components/AppButton.vue'
import { useForm } from '@/composables/useForm'
import { authApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { toast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const mode = ref('login') // login / register
const form = reactive({ email: '', password: '', username: '' })
const loading = ref(false)

const { errors, validate } = useForm(form, {
  email: [
    (v) => !!v || '请输入邮箱',
    (v) => /^[^@]+@[^@]+\.[^@]+$/.test(v) || '邮箱格式不正确',
  ],
  password: [
    (v) => !!v || '请输入密码',
    (v) => (v && v.length >= 3) || '密码至少 3 位',
  ],
})

async function submit() {
  if (mode.value === 'register' && !form.username.trim()) {
    toast.error('请输入用户名')
    return
  }
  if (!validate()) return
  loading.value = true
  try {
    const api = mode.value === 'login' ? authApi.login : authApi.register
    const payload = { email: form.email, password: form.password }
    if (mode.value === 'register') payload.username = form.username
    const res = await api(payload)
    if (res.code === 200) {
      auth.setAuth(res.token, {
        email: form.email,
        role: res.role,
        username: res.User_Name || form.username || form.email,
      })
      toast.success(mode.value === 'login' ? '登录成功' : '注册成功')
      router.push(route.query.redirect || { name: 'dashboard' })
    } else {
      toast.error(res.message || '操作失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '网络错误')
  } finally {
    loading.value = false
  }
}

function switchMode(m) {
  mode.value = m
  for (const k in errors) errors[k] = ''
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center px-4">
    <div class="w-full max-w-sm">
      <div class="text-center mb-8">
        <div
          class="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-zinc-900 text-white dark:bg-white dark:text-zinc-900 mb-3"
        >
          <Boxes class="w-6 h-6" />
        </div>
        <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">3D 打印农场</h1>
        <p class="text-sm text-zinc-400 mt-1">用 credit 下单，打印你的模型</p>
      </div>

      <div
        class="rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-sm p-6"
      >
        <div class="flex gap-1 p-1 mb-6 rounded-xl bg-zinc-100 dark:bg-zinc-800">
          <button
            v-for="m in [['login', '登录'], ['register', '注册']]"
            :key="m[0]"
            @click="switchMode(m[0])"
            :class="[
              'flex-1 h-9 rounded-lg text-sm font-medium transition-colors',
              mode === m[0]
                ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-sm'
                : 'text-zinc-500',
            ]"
          >
            {{ m[1] }}
          </button>
        </div>

        <form @submit.prevent="submit" class="space-y-4">
          <AppInput
            v-model="form.email" label="邮箱" type="email" placeholder="you@example.com"
            :error="errors.email" required
          />
          <AppInput
            v-if="mode === 'register'" v-model="form.username" label="用户名"
            placeholder="显示名称" required
          />
          <AppInput
            v-model="form.password" label="密码" type="password" placeholder="••••••"
            :error="errors.password" required
          />
          <AppButton type="submit" block size="lg" :loading="loading">
            {{ mode === 'login' ? '登录' : '注册' }}
          </AppButton>
        </form>
      </div>
    </div>
  </div>
</template>
