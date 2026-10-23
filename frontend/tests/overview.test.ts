import { expect, it, vi } from 'vitest'
import { api, ApiError } from '../src/api/client'
import { briefCandidates, findSavedBrief } from '../src/lib/overview'
import { brief, detail } from './fixtures'

it('derives at most three Shanghai dates from real records without a fixed historical date', () => {
 const events = [detail, { ...detail, created_at: '2026-10-18T18:00:00Z', updated_at: '2026-10-19T18:00:00Z' }]
 const dates = briefCandidates(events, '2026-10-22')
 expect(dates[0]).toBe('2026-10-22'); expect(dates.length).toBeLessThanOrEqual(3)
 expect(new Set(dates).size).toBe(dates.length)
})
it('reads saved briefs using GET only and stops on the first complete cache', async () => {
 const get = vi.spyOn(api, 'brief').mockRejectedValueOnce(new ApiError(404, 'http_error')).mockResolvedValue(brief)
 const post = vi.spyOn(api, 'generateBrief')
 expect(await findSavedBrief(['2026-10-22', '2026-10-20', '2026-10-19'], new AbortController().signal)).toEqual(brief)
 expect(get).toHaveBeenCalledTimes(2); expect(post).not.toHaveBeenCalled()
})
it('never expands discovery beyond three dates or creates missing briefs', async () => {
 const get = vi.spyOn(api, 'brief').mockRejectedValue(new ApiError(404, 'http_error'))
 const post = vi.spyOn(api, 'generateBrief')
 expect(await findSavedBrief(['a', 'b', 'c', 'd'], new AbortController().signal)).toBeNull()
 expect(get).toHaveBeenCalledTimes(3); expect(post).not.toHaveBeenCalled()
})
it('does not hide a storage/network failure as absence', async () => {
 const get = vi.spyOn(api, 'brief').mockRejectedValue(new ApiError(503, 'service_unavailable'))
 await expect(findSavedBrief(['2026-10-22'], new AbortController().signal)).rejects.toBeInstanceOf(ApiError)
 expect(get).toHaveBeenCalledTimes(1)
})
it('aborts discovery before making another request', async () => {
 const controller = new AbortController(); controller.abort()
 const get = vi.spyOn(api, 'brief')
 await expect(findSavedBrief(['2026-10-22'], controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
 expect(get).not.toHaveBeenCalled()
})
