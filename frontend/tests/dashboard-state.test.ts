import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { api, ApiError } from '../src/api/client'
import { recentTasks } from '../src/lib/history'
import DashboardPage from '../src/pages/DashboardPage.vue'

const global = { stubs: { RouterLink: { template: '<a><slot /></a>' } } }

it('keeps brief discovery in loading state while news dates are unavailable', async () => {
 recentTasks.value = []
 vi.spyOn(api, 'news').mockImplementation(() => new Promise(() => {}))
 const brief = vi.spyOn(api, 'brief')
 const post = vi.spyOn(api, 'generateBrief')
 const wrapper = mount(DashboardPage, { global })
 try {
  await flushPromises()
  expect(wrapper.text()).toContain('正在查找已保存日报')
  expect(wrapper.text()).not.toContain('已检查日期内暂无有效日报')
  expect(brief).not.toHaveBeenCalled()
  expect(post).not.toHaveBeenCalled()
 } finally { wrapper.unmount() }
})

it('reports a backend failure without claiming that saved briefs are absent', async () => {
 recentTasks.value = []
 vi.spyOn(api, 'news').mockRejectedValue(new ApiError(503, 'service_unavailable'))
 const brief = vi.spyOn(api, 'brief')
 const post = vi.spyOn(api, 'generateBrief')
 const wrapper = mount(DashboardPage, { global })
 try {
  await flushPromises()
  expect(wrapper.text()).toContain('日报暂不可读取')
  expect(wrapper.text()).toContain('尚未检查日报日期')
  expect(wrapper.text()).not.toContain('已检查日期内暂无有效日报')
  expect(brief).not.toHaveBeenCalled()
  expect(post).not.toHaveBeenCalled()
 } finally { wrapper.unmount() }
})
