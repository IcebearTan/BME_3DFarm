<script setup>
import { useToast } from '@/composables/useToast'
import { CheckCircle2, XCircle, Info, AlertTriangle, X } from 'lucide-vue-next'

const { state, toast } = useToast()

const iconOf = { success: CheckCircle2, error: XCircle, info: Info, warning: AlertTriangle }
const colorOf = {
  success: 'text-emerald-500',
  error: 'text-rose-500',
  info: 'text-zinc-500',
  warning: 'text-amber-500',
}
</script>

<template>
  <Teleport to="body">
    <div class="fixed top-4 right-4 z-50 flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]">
      <div
        v-for="t in state.items"
        :key="t.id"
        class="flex items-start gap-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-lg px-4 py-3"
      >
        <component :is="iconOf[t.type]" :class="['w-5 h-5 mt-0.5 shrink-0', colorOf[t.type]]" />
        <p class="flex-1 text-sm text-zinc-800 dark:text-zinc-200">{{ t.message }}</p>
        <button @click="toast.remove(t.id)" class="text-zinc-400 hover:text-zinc-600">
          <X class="w-4 h-4" />
        </button>
      </div>
    </div>
  </Teleport>
</template>
