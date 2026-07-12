import service from './request'

export const authApi = {
  login: (data) => service.post('/auth/login', data),
  me: () => service.get('/auth/me'),
}
