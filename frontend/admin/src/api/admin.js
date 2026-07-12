import service from './request'

/** 管理员订单生命周期 + credit 发放接口（对应后端 /admin/*）。 */
export const adminApi = {
  // 订单列表/详情
  orders: (params) => service.get('/admin/orders', { params }),
  orderDetail: (id) => service.get(`/admin/orders/${id}`),

  // 报价 / 审核 / 开打
  quote: (id, data) => service.post(`/admin/orders/${id}/quote`, data),
  approve: (id) => service.post(`/admin/orders/${id}/approve`),
  start: (id) => service.post(`/admin/orders/${id}/start`),

  // 完成 / 失败 / 退款 / 取消（credit 联动）
  complete: (id, data) => service.post(`/admin/orders/${id}/complete`, data),
  fail: (id) => service.post(`/admin/orders/${id}/fail`),
  refund: (id, data) => service.post(`/admin/orders/${id}/refund`, data),
  cancel: (id) => service.post(`/admin/orders/${id}/cancel`),

  // 发 credit
  grantCredit: (data) => service.post('/admin/credit/grant', data),
}
