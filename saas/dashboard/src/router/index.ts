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
      path: '/:projectId/health',
      name: 'health',
      component: () => import('../views/HealthView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/dashboard',
      redirect: (to) => `/${to.params.projectId}/health`,
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
  ],
})

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !localStorage.getItem('access_token')) {
    return { name: 'login' }
  }
})

export default router
