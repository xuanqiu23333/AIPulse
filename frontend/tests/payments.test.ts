import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api, ApiError } from '../src/api/client'
import ResearchPage from '../src/pages/ResearchPage.vue'
import NewsDetailPage from '../src/pages/NewsDetailPage.vue'
import BriefsPage from '../src/pages/BriefsPage.vue'
import { brief, detail, summary, TASK_ID } from './fixtures'
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })
async function page(component: typeof ResearchPage | typeof NewsDetailPage | typeof BriefsPage, path: string, pattern: string) {
 const routes = [{ path: pattern, component }, { path: '/research/:taskId', component: { template: '<p>任务详情占位，仅供离线测试</p>' } }]
 if (pattern !== '/news') routes.push({ path: '/news', component: { template: '<p>离线新闻列表</p>' } })
 if (pattern !== '/research') routes.push({ path: '/research', component: { template: '<p>离线研究入口</p>' } })
 const router = createRouter({ history: createMemoryHistory(), routes })
 await router.push(path); await router.isReady()
 const wrapper = mount(component, { attachTo: document.body, global: { plugins: [router] } }); wrappers.push(wrapper); await flushPromises(); return { wrapper, router }
}
it('creates research only after confirmation, once even on double click', async () => {
 let resolve!: (value: { task_id: string; status: 'queued' }) => void
 const create = vi.spyOn(api, 'createResearch').mockImplementation(() => new Promise(done => { resolve = done }))
 const { wrapper, router } = await page(ResearchPage, '/research', '/research')
 expect(create).not.toHaveBeenCalled()
 await wrapper.get('#research-topic').setValue('离线研究主题')
 await wrapper.get('.research-form').trigger('submit'); expect(create).not.toHaveBeenCalled()
 await wrapper.get('[data-testid="confirm-action"]').trigger('click'); await wrapper.get('[data-testid="confirm-action"]').trigger('click')
 expect(create).toHaveBeenCalledTimes(1); expect(create).toHaveBeenCalledWith({ topic: '离线研究主题' })
 resolve({ task_id: TASK_ID, status: 'queued' }); await flushPromises(); expect(router.currentRoute.value.path).toBe(`/research/${TASK_ID}`)
})
it('canceling confirmation sends no POST; disabled backend gives a clear message', async () => {
 const create = vi.spyOn(api, 'createResearch').mockRejectedValue(new ApiError(403, 'http_error'))
 const { wrapper } = await page(ResearchPage, '/research', '/research')
 await wrapper.get('#research-topic').setValue('离线测试主题'); await wrapper.get('.research-form').trigger('submit')
 await wrapper.get('.dialog-actions .secondary').trigger('click'); expect(create).not.toHaveBeenCalled()
 await wrapper.get('.research-form').trigger('submit'); await wrapper.get('[data-testid="confirm-action"]').trigger('click'); await flushPromises()
 expect(wrapper.text()).toContain('功能未启用'); expect(create).toHaveBeenCalledTimes(1)
})
it.each(['summary', 'brief'] as const)('%s generation stays behind confirmation and preserves cached content on failure', async kind => {
 vi.spyOn(api, 'detail').mockResolvedValue(detail); vi.spyOn(api, 'summary').mockResolvedValue(summary); vi.spyOn(api, 'brief').mockResolvedValue(brief)
 const post = kind === 'summary' ? vi.spyOn(api, 'generateSummary').mockRejectedValue(new ApiError(503, 'service_unavailable')) : vi.spyOn(api, 'generateBrief').mockRejectedValue(new ApiError(503, 'service_unavailable'))
 const { wrapper } = kind === 'summary' ? await page(NewsDetailPage, '/news/1', '/news/:eventId') : await page(BriefsPage, '/briefs?date=2026-10-20', '/briefs')
 expect(post).not.toHaveBeenCalled()
 const generate = wrapper.findAll('button').find(button => button.text().includes('生成 / 更新'))!
 await generate.trigger('click'); expect(post).not.toHaveBeenCalled(); await wrapper.get('[data-testid="confirm-action"]').trigger('click'); await flushPromises()
 expect(post).toHaveBeenCalledTimes(1); expect(wrapper.text()).toContain(kind === 'summary' ? '离线测试摘要' : '离线日报概览')
 if (kind === 'brief') expect(wrapper.text()).toContain('旧版本日报缓存')
})
