<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import ToastContainer from '@/components/ToastContainer.vue'
import { useThemeStore } from '@/stores/theme'

const route = useRoute()
const theme = useThemeStore()

theme.init()

const isAuthPage = computed(() => !!route.meta.authPage)

// isDark 变化时切 <html> 的 .dark class（Tailwind dark: 变体由此驱动）
watch(
  () => theme.isDark,
  (dark) => document.documentElement.classList.toggle('dark', dark),
  { immediate: true }
)
</script>

<template>
  <div class="min-h-screen flex flex-col">
    <AppHeader v-if="!isAuthPage" />
    <main :class="isAuthPage ? 'flex-1' : 'flex-1 max-w-5xl w-full mx-auto px-4 py-8'">
      <router-view />
    </main>
    <ToastContainer />
  </div>
</template>
