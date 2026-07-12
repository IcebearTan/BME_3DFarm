import service from './request'

export const ordersApi = {
  list: (params) => service.get('/orders/', { params }),
  detail: (id) => service.get(`/orders/${id}`),
  /** 创建订单（multipart：表单字段 + 可选 file） */
  create: (formData) =>
    service.post('/orders/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  confirm: (id) => service.post(`/orders/${id}/confirm`),
  cancel: (id) => service.post(`/orders/${id}/cancel`),
}
