import { defineStore } from 'pinia'

/** 管理员认证 store。登录后检查 role === 'admin'，非管理员拒绝。 */
export const useAuthStore = defineStore('admin-auth', {
  state: () => ({
    token: localStorage.getItem('admin_token') || '',
    user: null,
  }),
  getters: {
    isLogin: (s) => !!s.token,
    isAdmin: (s) => s.user?.role === 'admin',
    username: (s) => s.user?.username || s.user?.email || '',
  },
  actions: {
    setAuth(token, user) {
      this.token = token
      this.user = user
      if (token) localStorage.setItem('admin_token', token)
      else localStorage.removeItem('admin_token')
    },
    setUser(user) {
      this.user = user
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('admin_token')
    },
  },
})
