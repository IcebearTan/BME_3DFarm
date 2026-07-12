import axios from 'axios'
import { useAuthStore } from '@/stores/auth'
import { toast } from '@/composables/useToast'
import router from '@/router'

const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 30000,
})

// 请求拦截：注入 JWT
service.interceptors.request.use((cfg) => {
  const auth = useAuthStore()
  if (auth.token) cfg.headers.Authorization = `Bearer ${auth.token}`
  return cfg
})

// 响应拦截：解包 res.data；401 统一处理（去重，防连弹）
let expiredShown = false
service.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const status = err.response?.status
    if (status === 401 && !expiredShown) {
      expiredShown = true
      const auth = useAuthStore()
      auth.logout()
      toast.error('登录已失效，请重新登录')
      const redirect = router.currentRoute.value.fullPath
      setTimeout(() => {
        expiredShown = false
        router.push({ name: 'login', query: { redirect } })
      }, 800)
    }
    return Promise.reject(err)
  }
)

export default service
