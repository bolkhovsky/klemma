import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'landing',
      component: () => import('../views/LandingView.vue'),
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('../views/RegisterView.vue'),
    },
    // Global library — all sources across projects (top-level, no projectId)
    {
      path: '/library',
      name: 'global-library',
      component: () => import('../views/GlobalLibraryView.vue'),
      meta: { requiresAuth: true },
    },
    // Project-scoped routes
    {
      path: '/:projectId/map',
      redirect: (to) => `/${to.params.projectId}/write`,
    },
    {
      path: '/:projectId/dashboard',
      redirect: (to) => `/${to.params.projectId}/write`,
    },
    {
      path: '/:projectId/draft',
      redirect: (to) => `/${to.params.projectId}/write`,
    },
    {
      path: '/:projectId/feed',
      name: 'feed',
      component: () => import('../views/FeedView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/feed/:insightId',
      name: 'insight',
      component: () => import('../views/InsightView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/health',
      name: 'health',
      component: () => import('../views/HealthView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/library',
      name: 'library',
      component: () => import('../views/LibraryView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/library/:citekey',
      name: 'source',
      component: () => import('../views/SourceView.vue'),
      meta: { requiresAuth: true },
    },
    // Removed views redirect to /write
    { path: '/:projectId/coverage', redirect: (to) => `/${to.params.projectId}/write` },
    { path: '/:projectId/outline', redirect: (to) => `/${to.params.projectId}/write` },
    { path: '/:projectId/research', redirect: (to) => `/${to.params.projectId}/write` },
    { path: '/:projectId/research/:section', redirect: (to) => `/${to.params.projectId}/write` },
    {
      path: '/:projectId/write',
      name: 'write',
      component: () => import('../views/SectionEditorView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.requiresAuth && !token) return { name: 'login' }
  if ((to.name === 'login' || to.name === 'register') && token) return { path: '/library' }
})

router.afterEach((to) => {
  if (typeof window.ym === 'function') {
    window.ym(108182650, 'hit', window.location.origin + to.fullPath)
  }
})

export default router
