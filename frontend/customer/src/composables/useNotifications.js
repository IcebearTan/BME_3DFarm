// 通知 Composable — 铃铛与通知中心共享的响应式状态（模块级单例）。
// 照搬 BME useNotifications 的单例 + 30s 轮询 + 乐观更新模式，适配 3DFarm api。
import { ref, computed } from 'vue'
import { notificationsApi } from '@/api/notifications'

// 模块级单例：所有 useNotifications() 实例共享同一份数据
const notificationList = ref([])
const loading = ref(false)
let pollingTimer = null

/** 相对时间格式化 */
export function formatRelativeTime(dateStr) {
  if (!dateStr) return ''
  const diff = Date.now() - new Date(dateStr).getTime()
  const d = Math.floor(diff / 86400000)
  const h = Math.floor(diff / 3600000)
  const m = Math.floor(diff / 60000)
  return d > 0 ? `${d}天前` : h > 0 ? `${h}小时前` : m > 0 ? `${m}分钟前` : '刚刚'
}

export function useNotifications() {
  const unreadCount = computed(() => notificationList.value.filter(n => !n.is_read).length)

  async function fetchNotifications(params = {}) {
    loading.value = true
    try {
      const r = await notificationsApi.list(params)
      if (r.code === 200) notificationList.value = r.data.items
    } catch (e) {
      console.error('[useNotifications] fetch 失败:', e)
    } finally {
      loading.value = false
    }
  }

  /** 标记单条已读（乐观更新，失败回滚） */
  async function markAsRead(id) {
    const t = notificationList.value.find(n => n.id === id)
    if (t) t.is_read = true
    try {
      await notificationsApi.markRead(id)
    } catch (e) {
      if (t) t.is_read = false
      console.error('[useNotifications] markAsRead 失败:', e)
    }
  }

  /** 全部已读（乐观更新） */
  async function markAllAsRead(category = null) {
    const targets = notificationList.value.filter(n => !n.is_read)
    targets.forEach(n => { n.is_read = true })
    try {
      await notificationsApi.markAllRead(category ? { category } : null)
    } catch (e) {
      targets.forEach(n => { n.is_read = false })
      console.error('[useNotifications] markAllAsRead 失败:', e)
    }
  }

  /** 删除已读通知（乐观更新，失败回滚） */
  async function deleteNotifications(ids) {
    const backup = [...notificationList.value]
    notificationList.value = notificationList.value.filter(n => !ids.includes(n.id))
    try {
      await notificationsApi.delete(ids)
    } catch (e) {
      notificationList.value = backup
      console.error('[useNotifications] delete 失败:', e)
    }
  }

  /** 开启轮询（铃铛用，默认 30s） */
  function startPolling(interval = 30000) {
    stopPolling()
    fetchNotifications()
    pollingTimer = setInterval(() => fetchNotifications(), interval)
  }

  function stopPolling() {
    if (pollingTimer) {
      clearInterval(pollingTimer)
      pollingTimer = null
    }
  }

  return {
    notificationList, unreadCount, loading,
    fetchNotifications, markAsRead, markAllAsRead, deleteNotifications,
    startPolling, stopPolling, formatRelativeTime,
  }
}
