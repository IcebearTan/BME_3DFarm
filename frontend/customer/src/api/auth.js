import service from './request'

export const authApi = {
  register: (data) => service.post('/auth/register', data),
  login: (data) => service.post('/auth/login', data),
  me: () => service.get('/auth/me'),
}
