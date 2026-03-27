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
    // Demo routes (no auth, for UX prototyping)
    {
      path: '/demo/feed',
      name: 'demo-feed',
      component: () => import('../views/FeedView.vue'),
    },
    {
      path: '/demo/feed/:insightId',
      name: 'demo-insight',
      component: () => import('../views/InsightView.vue'),
    },
    {
      path: '/demo/map',
      name: 'demo-map',
      component: () => import('../views/MapView.vue'),
    },
    {
      path: '/demo/map/:sectionId/:blockId',
      name: 'demo-block',
      component: () => import('../views/BlockView.vue'),
    },
    // Project-scoped routes
    {
      path: '/:projectId/map',
      name: 'map',
      component: () => import('../views/MapView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/map/:sectionId/:blockId',
      name: 'block',
      component: () => import('../views/BlockView.vue'),
      meta: { requiresAuth: true },
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
      path: '/:projectId/dashboard',
      redirect: (to) => `/${to.params.projectId}/map`,
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
    {
      path: '/:projectId/coverage',
      name: 'coverage',
      component: () => import('../views/CoverageView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/outline',
      name: 'outline',
      component: () => import('../views/OutlineView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/research',
      name: 'research',
      component: () => import('../views/ResearchView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/research/:section',
      name: 'research-report',
      component: () => import('../views/ReportView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/draft',
      redirect: (to) => `/${to.params.projectId}/map`,
    },
    {
      path: '/:projectId/edit',
      name: 'edit',
      component: () => import('../views/FileEditorView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/edit/:filename',
      name: 'edit-file',
      component: () => import('../views/FileEditorView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.requiresAuth && !token) return { name: 'login' }
  // Авторизованным пользователям на /login или /register — в библиотеку
  if ((to.name === 'login' || to.name === 'register') && token) return { path: '/library' }
})

router.afterEach((to) => {
  if (typeof window.ym === 'function') {
    window.ym(108182650, 'hit', window.location.origin + to.fullPath)
  }
})

export default router
