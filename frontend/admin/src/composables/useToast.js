import { reactive } from 'vue'

const state = reactive({ items: [] })
let seq = 0

function push(type, message, duration = 3000) {
  const id = ++seq
  state.items.push({ id, type, message })
  if (duration > 0) setTimeout(() => remove(id), duration)
  return id
}

function remove(id) {
  const i = state.items.findIndex((t) => t.id === id)
  if (i > -1) state.items.splice(i, 1)
}

export const toast = {
  success: (m, d) => push('success', m, d),
  error: (m, d) => push('error', m, d ?? 4000),
  info: (m, d) => push('info', m, d),
  warning: (m, d) => push('warning', m, d),
  remove,
}

export function useToast() {
  return { state, toast, remove }
}
