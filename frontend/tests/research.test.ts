import { afterEach, expect, it, vi } from 'vitest'
import { effectScope, ref, type EffectScope } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { api, ApiError } from '../src/api/client'
import { useResearchTask } from '../src/composables/useResearchTask'
import ReportViewer from '../src/components/ReportViewer.vue'
import { report, snapshot, TASK_ID } from './fixtures'
const scopes: EffectScope[] = []
afterEach(() => scopes.splice(0).forEach(scope => scope.stop()))
function start() { const scope = effectScope(); scopes.push(scope); return { scope, state: scope.run(() => useResearchTask(ref(TASK_ID)))! } }
function visibility(value: string) { Object.defineProperty(document, 'visibilityState', { configurable: true, value }); document.dispatchEvent(new Event('visibilitychange')) }
function mocks() { vi.spyOn(api, 'evidence').mockResolvedValue({ task_id: TASK_ID, evidence: [] }); vi.spyOn(api, 'report').mockResolvedValue(report()); return vi.spyOn(api, 'createResearch') }
it('HTTP-successful failed report hides conclusions and retains unknown usage', () => {
 const wrapper = mount(ReportViewer, { props: { report: report(), evidenceIds: [] } })
 expect(wrapper.text()).toContain('报告生成失败'); expect(wrapper.text()).toContain('report_output_truncated')
 expect(wrapper.text()).not.toContain('此无效结论不应在失败报告中展示'); expect(wrapper.text()).toContain('1 次调用用量未知')
 expect(wrapper.text()).toContain('不是完整账单'); wrapper.unmount()
})
it('links successful report citations only to evidence actually loaded', () => {
 const value = report(); value.status = 'completed'; value.error_code = null
 value.conclusions[0]!.citations = ['E001', 'E999']
 const wrapper = mount(ReportViewer, { props: { report: value, evidenceIds: ['E001'] } })
 expect(wrapper.find('a[href="#evidence-E001"]').exists()).toBe(true)
 expect(wrapper.find('a[href="#evidence-E999"]').exists()).toBe(false)
 expect(wrapper.text()).toContain('E999 · 证据未读取'); wrapper.unmount()
})
it('keeps partial content readable and presents GitHub as a source warning', () => {
 const value = report(); value.status = 'partial'; value.error_code = null
 value.overview = value.conclusions; value.conclusions = []
 value.source_warnings = ['github_rate_limited']
 const wrapper = mount(ReportViewer, { props: { report: value, evidenceIds: ['E001'] } })
 expect(wrapper.find('.notice.error').exists()).toBe(false)
 expect(wrapper.text()).toContain('GitHub 检索限流')
 expect(wrapper.text()).toContain('已验证的概览和证据仍可阅读')
 expect(wrapper.find('a[href="#evidence-E001"]').exists()).toBe(true)
 expect(wrapper.find('.status-badge.amber').exists()).toBe(true)
 wrapper.unmount()
})
it('stops polling on a terminal state and never recreates the task', async () => {
 vi.useFakeTimers(); const create = mocks(), task = vi.spyOn(api, 'task').mockResolvedValueOnce(snapshot('running')).mockResolvedValue(snapshot('failed'))
 const { state } = start(); await flushPromises(); await vi.advanceTimersByTimeAsync(5000); await flushPromises()
 expect(state.snapshot.value?.status).toBe('failed'); expect(state.report.value?.status).toBe('failed')
 await vi.advanceTimersByTimeAsync(60000); expect(task).toHaveBeenCalledTimes(2); expect(create).not.toHaveBeenCalled()
})
it('pauses on hidden pages and cancels polling on unmount', async () => {
 vi.useFakeTimers(); mocks(); const task = vi.spyOn(api, 'task').mockResolvedValue(snapshot('running'))
 const { scope } = start(); await flushPromises(); visibility('hidden'); await vi.advanceTimersByTimeAsync(20000); expect(task).toHaveBeenCalledTimes(1)
 visibility('visible'); await flushPromises(); expect(task).toHaveBeenCalledTimes(2); scope.stop(); await vi.advanceTimersByTimeAsync(60000); expect(task).toHaveBeenCalledTimes(2)
})
it('bounds repeated GET errors without changing real task state', async () => {
 vi.useFakeTimers(); const create = mocks(), task = vi.spyOn(api, 'task').mockRejectedValue(new ApiError(503, 'research_storage_error'))
 const { state } = start(); await flushPromises(); await vi.advanceTimersByTimeAsync(60000); await flushPromises()
 expect(task).toHaveBeenCalledTimes(3); expect(state.snapshot.value).toBeNull(); expect(state.pollingNote.value).toContain('暂停'); expect(create).not.toHaveBeenCalled()
})
it('does not reset the observation deadline when a hidden page is restored', async () => {
 vi.useFakeTimers(); mocks(); const task = vi.spyOn(api, 'task').mockResolvedValue(snapshot('running'))
 const { state } = start(); await flushPromises(); visibility('hidden'); await vi.advanceTimersByTimeAsync(421000); visibility('visible'); await flushPromises()
 expect(task).toHaveBeenCalledTimes(1); expect(state.pollingNote.value).toContain('暂停'); expect(state.snapshot.value?.status).toBe('running')
})
