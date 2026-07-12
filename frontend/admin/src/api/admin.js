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

  // 用户搜索（发 credit 选用户，Phase 1.5）
  users: (q) => service.get('/admin/users', { params: { q } }),

  // 费率配置（自动报价，Phase 1.5）
  getPricing: () => service.get('/admin/pricing'),
  addPricing: (data) => service.post('/admin/pricing', data),
  updatePricing: (id, data) => service.put(`/admin/pricing/${id}`, data),

  // 打印机监控（Phase 4.5，Poller 同步）
  getPrinters: () => service.get('/admin/printers'),

  // Bambuddy 任务绑定（Phase 2）
  getBambuddyJob: (id) => service.get(`/admin/orders/${id}/bambuddy-job`),
  bindBambuddy: (id, data) => service.post(`/admin/orders/${id}/bind-bambuddy`, data),

  // 下发 Bambuddy（Phase 3 半自动调度）
  dispatchOrder: (id, printerId) =>
    service.post(`/admin/orders/${id}/dispatch`, { bambuddy_printer_id: printerId }),
  cancelDispatch: (id) => service.post(`/admin/orders/${id}/cancel-dispatch`),

  // 文件下载 + 切片产物上传（Phase 4 路径 B）
  downloadOrderFile: (orderId, fileId) =>
    service.get(`/admin/orders/${orderId}/files/${fileId}/download`, { responseType: "blob" }),
  uploadSliced: (orderId, formData) =>
    service.post(`/admin/orders/${orderId}/upload-sliced`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }),
}
