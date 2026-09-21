import createClient from 'openapi-fetch'
import type { paths } from './schema'
import { authStore } from '@/auth/session'
import { toApiError } from './errors'

export const api = createClient<paths>({ baseUrl: '' })
let redirecting = false
export function expireSession() {
  authStore.clear()
  if (!redirecting && location.pathname !== '/login') {
    redirecting = true
    window.location.assign(
      '/login?next=' + encodeURIComponent(location.pathname + location.search),
    )
  }
}
api.use({
  onRequest({ request }) {
    const token = authStore.getToken()
    if (token) request.headers.set('Authorization', 'Bearer ' + token)
    return request
  },
  async onResponse({ request, response }) {
    if (
      response.status === 401 &&
      new URL(request.url).pathname !== '/v1/auth/login'
    )
      expireSession()
    if (!response.ok) throw await toApiError(response)
    return response
  },
})
export async function dataOf<T>(request: Promise<{ data?: T }>): Promise<T> {
  const result = await request
  if (result.data === undefined) throw new Error('Missing response data')
  return result.data
}
