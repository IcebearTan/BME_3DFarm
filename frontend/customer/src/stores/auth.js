import { defineStore } from 'pinia'

/**
 * 认证 store —— token 真相源统一在此（localStorage 与 state 双写一致）。
 * 登录/注册成功调 setAuth；登出/401 调 logout。
 */
export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: null,
  }),
  getters: {
    isLogin: (s) => !!s.token,
    role: (s) => s.user?.role || 'customer',
    username: (s) => s.user?.username || s.user?.email || '',
  },
  actions: {
    setAuth(token, user) {
      this.token = token
      this.user = user
      if (token) localStorage.setItem('token', token)
      else localStorage.removeItem('token')
    },
    setUser(user) {
      this.user = user
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
    },
  },
})
