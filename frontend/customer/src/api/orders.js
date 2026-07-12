import service from './request'

export const ordersApi = {
  list: (params) => service.get('/orders/', { params }),
  detail: (id) => service.get(`/orders/${id}`),
  /** 预览 .gcode.3mf：解析返回材料/多色/克重/时长/报价，不落库不冻 credit */
  preview: (formData) =>
    service.post('/orders/preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  /** 创建订单（multipart：表单字段 + 可选 file）。material 由 gcode 推导，无需手填 */
  create: (formData) =>
    service.post('/orders/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  confirm: (id) => service.post(`/orders/${id}/confirm`),
  cancel: (id) => service.post(`/orders/${id}/cancel`),
}
