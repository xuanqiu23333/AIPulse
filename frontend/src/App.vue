<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onErrorCaptured, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Activity, ArrowUpRight, CalendarDays, ChevronRight, FlaskConical, LayoutDashboard, Menu, Newspaper, PanelsTopLeft, RefreshCw, ShieldCheck, X } from '@lucide/vue'
import { api } from './api/client'
import type { SchedulerStatus } from './api/types'
import { dateTime } from './lib/format'
import UiState from './components/UiState.vue'
const route = useRoute(), mobileOpen = ref(false), schedule = ref<SchedulerStatus | null>(null), offline = ref(false), checking = ref(false), viewError = ref(false)
const mobileQuery = window.matchMedia('(max-width: 1023px)'), isMobile = ref(mobileQuery.matches), menuButton = ref<HTMLButtonElement>()
function resizeNavigation() { isMobile.value = mobileQuery.matches; if (!isMobile.value) mobileOpen.value = false }
function escapeNavigation(event: KeyboardEvent) { if (event.key === 'Escape' && mobileOpen.value) { mobileOpen.value = false; menuButton.value?.focus() } }
mobileQuery.addEventListener('change', resizeNavigation)
document.addEventListener('keydown', escapeNavigation)
watch(mobileOpen, async open => { document.body.style.overflow = open ? 'hidden' : ''; await nextTick(); if (open) document.querySelector<HTMLElement>('.sidebar .nav-item')?.focus() })
onBeforeUnmount(() => { mobileQuery.removeEventListener('change', resizeNavigation); document.removeEventListener('keydown', escapeNavigation); document.body.style.overflow = '' })
const navigation = [{ to: '/', title: '情报总览', icon: LayoutDashboard, hint: 'Overview' }, { to: '/news', title: 'AI 新闻中心', icon: Newspaper, hint: 'News' }, { to: '/briefs', title: '每日情报简报', icon: CalendarDays, hint: 'Daily brief' }, { to: '/research', title: 'Deep Research', icon: FlaskConical, hint: 'Workspace' }]
const currentDate = computed(() => dateTime(new Date().toISOString()).slice(0, 10))
async function check() { checking.value = true; try { schedule.value = await api.scheduler(); offline.value = false } catch { offline.value = true; schedule.value = null } finally { checking.value = false } }
onMounted(check)
watch(() => route.fullPath, () => { mobileOpen.value = false; viewError.value = false })
onErrorCaptured(() => { viewError.value = true; return false })
</script>
<template>
 <a class="skip-link" href="#main-content">跳到主要内容</a>
 <button v-if="mobileOpen" class="sidebar-scrim" aria-label="关闭导航" @click="mobileOpen = false"></button>
 <aside id="app-navigation" class="sidebar" :class="{ 'is-open': mobileOpen }" :inert="isMobile && !mobileOpen">
  <RouterLink to="/" class="brand"><span class="brand-mark"><Activity :size="25" aria-hidden="true" /></span><span>AIPulse<small>AI INTELLIGENCE</small></span></RouterLink>
  <button class="icon-button mobile-close" aria-label="关闭导航" @click="mobileOpen = false"><X :size="21" /></button>
  <div class="workspace-label">个人工作空间<span class="badge neutral">LOCAL</span></div>
  <nav aria-label="主要导航"><RouterLink v-for="item in navigation" :key="item.to" :to="item.to" class="nav-item" :class="{ selected: item.to === '/' ? route.path === '/' : route.path.startsWith(item.to) }" @click="mobileOpen = false"><component :is="item.icon" :size="19" aria-hidden="true" /><span>{{ item.title }}<small>{{ item.hint }}</small></span><ChevronRight :size="14" class="nav-chevron" aria-hidden="true" /></RouterLink></nav>
  <div class="sidebar-note"><span class="icon-box blue"><ShieldCheck :size="21" aria-hidden="true" /></span><strong>从信息，到有据可循</strong><p>阅读真实来源与已保存缓存。模型操作需主动确认，默认关闭。</p><RouterLink to="/research" class="section-link">了解研究工作台 <ArrowUpRight :size="13" /></RouterLink></div>
  <div class="sidebar-bottom"><span class="status-dot"></span><div><strong>本地个人工作区</strong><small>Asia/Shanghai · 单实例</small></div><span class="badge neutral">LOCAL</span></div>
 </aside>
 <div class="app-body" :inert="isMobile && mobileOpen">
  <header class="topbar"><div class="toolbar-context"><button ref="menuButton" class="icon-button menu-toggle" aria-label="打开导航" aria-controls="app-navigation" :aria-expanded="mobileOpen" @click="mobileOpen = true"><Menu :size="22" /></button><span class="toolbar-context-icon"><PanelsTopLeft :size="19" aria-hidden="true" /></span><div class="breadcrumb"><span>AI 情报工作台</span><ChevronRight :size="13" aria-hidden="true" /><strong>{{ route.meta.title }}</strong></div></div><div class="topbar-right"><span class="connection" :class="{ offline }" role="status"><span class="status-dot"></span>{{ offline ? '后端未连接' : schedule ? '本机服务已连接' : '正在连接' }}</span><span class="top-date"><CalendarDays :size="16" aria-hidden="true" />{{ currentDate }}</span><button type="button" class="icon-button toolbar-refresh" aria-label="重新检查后端连接" title="重新检查本机服务连接" :disabled="checking" @click="check"><RefreshCw :size="18" :class="{ spin: checking }" /></button><RouterLink to="/research" class="toolbar-shortcut" aria-label="进入 Deep Research 工作台"><FlaskConical :size="18" /><span>研究工作台</span></RouterLink></div></header>
  <main id="main-content" class="page-container" :class="{ 'is-dashboard': route.path === '/', 'is-reading': route.path === '/briefs' || /^\/news\/\d+/.test(route.path) }" tabindex="-1">
   <h1 v-if="route.path === '/'" class="sr-only">情报总览</h1><div v-else class="page-heading"><div><p class="eyebrow">AIPULSE / {{ route.meta.eyebrow }}</p><h1>{{ route.meta.title }}</h1></div><span class="badge neutral desktop-only"><ShieldCheck :size="13" aria-hidden="true" />本机访问</span></div>
   <div v-if="schedule?.enabled" class="notice warning" role="status">后端定时采集已启用。只读验收前，请在服务端关闭定时采集。</div>
   <UiState v-if="viewError" kind="error" title="页面数据暂时无法展示" detail="返回其他页面后重试，或检查后端返回的数据结构。" />
   <RouterView v-else />
   <footer class="page-footer"><span>AIPulse · 把信息变成判断</span><span>来源可追溯 · AI 内容需核验 <ArrowUpRight :size="12" aria-hidden="true" /></span></footer>
  </main>
 </div>
</template>
