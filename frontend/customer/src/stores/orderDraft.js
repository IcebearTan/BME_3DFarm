import { defineStore } from 'pinia'

/**
 * 跨页面携带「待下单文件」：首页上传框选中文件后存入，NewOrderView 挂载时取走。
 * 文件对象只在内存里（不序列化），刷新即丢——符合「草稿」语义。
 */
export const useOrderDraftStore = defineStore('orderDraft', {
  state: () => ({
    pendingFile: null, // { file, name, size }
  }),
  actions: {
    set(file) {
      this.pendingFile = file
    },
    take() {
      const f = this.pendingFile
      this.pendingFile = null
      return f
    },
  },
})
