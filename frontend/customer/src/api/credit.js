import service from './request'

export const creditApi = {
  /** 余额 { available, frozen, total } */
  balance: () => service.get('/credit/me'),
  /** 流水分页 { items, total, page, per_page, pages } */
  transactions: (params) => service.get('/credit/me/transactions', { params }),
}
