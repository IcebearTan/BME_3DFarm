import service from './request'

export const printersApi = {
  list: () => service.get('/printers/'),
}
