import service from './request'

/** 站内通知（公告 + 订单提醒），对应后端 /notifications/*。 */
export const notificationsApi = {
  list: (params) => service.get('/notifications/list', { params }),
  unreadCount: () => service.get('/notifications/unread_count'),
  markRead: (id) => service.post('/notifications/mark_read', { id }),
  markAllRead: (data) => service.post('/notifications/mark_all_read', data || {}),
  delete: (ids) => service.delete('/notifications/delete', { data: { ids } }),
}
