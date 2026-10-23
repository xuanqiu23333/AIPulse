import { describe, expect, it, vi } from 'vitest'
import { api, ApiError, errorMessage, request } from '../src/api/client'
import { safeUrl } from '../src/lib/format'
describe('bounded API client', () => {
 it.each([200, 201, 202])('accepts JSON HTTP %s', async status => { vi.mocked(fetch).mockResolvedValue(new Response('{"ok":true}', { status })); expect(await request('/api/test')).toEqual({ ok: true }) })
 it.each([403, 404, 409, 422, 429, 500, 502, 503])('translates HTTP %s without exposing server details', async status => {
  vi.mocked(fetch).mockResolvedValue(new Response('{"code":"http_error","message":"private-server-secret"}', { status }))
  try { await request('/api/test'); throw new Error('Expected rejection') } catch (error) { expect(error).toBeInstanceOf(ApiError); expect(errorMessage(error)).not.toContain('private-server-secret'); expect((error as ApiError).status).toBe(status) }
  expect(fetch).toHaveBeenCalledTimes(1)
 })
 it('does not retry an uncertain POST', async () => { vi.mocked(fetch).mockRejectedValue(new TypeError('offline')); await expect(api.createResearch({ topic: '离线测试主题' })).rejects.toMatchObject({ code: 'network_error', uncertain: true }); expect(fetch).toHaveBeenCalledTimes(1) })
 it('times out and distinguishes caller cancellation', async () => {
  vi.useFakeTimers()
  vi.mocked(fetch).mockImplementation((_url, options) => new Promise((_resolve, reject) => options?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))))
  const result = request('/api/test', { timeout: 10 }).catch(error => error)
  await vi.advanceTimersByTimeAsync(20); expect(await result).toMatchObject({ code: 'timeout' })
  const controller = new AbortController(), cancelled = request('/api/test', { signal: controller.signal }).catch(error => error)
  controller.abort(); expect(await cancelled).toBeInstanceOf(DOMException); expect((await cancelled as DOMException).name).toBe('AbortError')
 })
 it('keeps real news pagination parameters and rejects malformed JSON', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response('{"items":[]}')).mockResolvedValueOnce(new Response('<html>proxy error</html>'))
  await api.news(new URLSearchParams({ category: 'paper', min_score: '70', sort: 'latest', page: '2', page_size: '10' }))
  expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe('/api/v1/news/events?category=paper&min_score=70&sort=latest&page=2&page_size=10')
  await expect(request('/api/test')).rejects.toMatchObject({ code: 'invalid_response' })
 })
 it('does not allow unsafe source links', () => { expect(safeUrl('javascript:alert(1)')).toBeNull(); expect(safeUrl('http://127.0.0.1/private')).toBeNull(); expect(safeUrl('https://user:secret@example.com/')).toBeNull(); expect(safeUrl('https://arxiv.org/abs/2601.00001')).toBe('https://arxiv.org/abs/2601.00001') })
})
