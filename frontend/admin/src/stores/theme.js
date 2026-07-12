import { defineStore } from 'pinia'

export const useThemeStore = defineStore('admin-theme', {
  state: () => ({
    isDark: localStorage.getItem('admin_theme') === 'dark',
  }),
  actions: {
    toggle() {
      this.isDark = !this.isDark
      this.persist()
    },
    init() {
      if (!localStorage.getItem('admin_theme')) {
        this.isDark = window.matchMedia('(prefers-color-scheme: dark)').matches
        this.persist()
      }
    },
    persist() {
      localStorage.setItem('admin_theme', this.isDark ? 'dark' : 'light')
    },
  },
})
