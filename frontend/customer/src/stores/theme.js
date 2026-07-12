import { defineStore } from 'pinia'

/** 主题 store：暗色持久化到 localStorage，首次跟随系统偏好。 */
export const useThemeStore = defineStore('theme', {
  state: () => ({
    isDark: localStorage.getItem('theme') === 'dark',
  }),
  actions: {
    toggle() {
      this.isDark = !this.isDark
      this.persist()
    },
    init() {
      // 首次（未设过）跟随系统偏好
      if (!localStorage.getItem('theme')) {
        this.isDark = window.matchMedia('(prefers-color-scheme: dark)').matches
        this.persist()
      }
    },
    persist() {
      localStorage.setItem('theme', this.isDark ? 'dark' : 'light')
    },
  },
})
