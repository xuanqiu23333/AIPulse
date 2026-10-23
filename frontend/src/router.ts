import { createRouter, createWebHistory } from 'vue-router'
export const router = createRouter({ history: createWebHistory(), routes: [
 { path: '/', component: () => import('./pages/DashboardPage.vue'), meta: { title: '情报总览', eyebrow: 'INTELLIGENCE OVERVIEW' } },
 { path: '/news', component: () => import('./pages/NewsPage.vue'), meta: { title: 'AI 新闻中心', eyebrow: 'NEWS EXPLORER' } },
 { path: '/news/:eventId', component: () => import('./pages/NewsDetailPage.vue'), meta: { title: '新闻详情', eyebrow: 'NEWS / DETAIL' } },
 { path: '/briefs', component: () => import('./pages/BriefsPage.vue'), meta: { title: '每日 AI 情报简报', eyebrow: 'DAILY INTELLIGENCE' } },
 { path: '/research', component: () => import('./pages/ResearchPage.vue'), meta: { title: 'Deep Research 工作台', eyebrow: 'RESEARCH WORKSPACE' } },
 { path: '/research/:taskId', component: () => import('./pages/ResearchTaskPage.vue'), meta: { title: '研究任务', eyebrow: 'RESEARCH / TASK' } },
 { path: '/:pathMatch(.*)*', component: () => import('./pages/NotFoundPage.vue'), meta: { title: '页面未找到', eyebrow: 'NOT FOUND' } },
 ], scrollBehavior(to, from, saved) { if (saved) return saved; if (to.hash) return { el: to.hash, top: 24 }; return to.path !== from.path ? { top: 0 } : {} } })
router.afterEach(to => { document.title = `${String(to.meta.title)} · AIPulse` })
