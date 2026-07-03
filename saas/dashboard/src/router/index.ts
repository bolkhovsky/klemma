import { createRouter, createWebHistory } from 'vue-router'

/** Portal-only build (Bonum) lands on the meeting portal instead of the
 *  academic library after login. Toggled by VITE_PORTAL_ONLY at build time. */
export const PORTAL_ONLY =
  import.meta.env.VITE_PORTAL_ONLY === '1' || import.meta.env.VITE_PORTAL_ONLY === 'true'

export function postLoginPath(projectId: string): string {
  return PORTAL_ONLY ? `/${projectId}/portal/meetings` : `/${projectId}/library`
}

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
      // Bonum meeting-analytics portal — standalone (own shell, no app chrome)
      path: '/:projectId/portal',
      component: () => import('../views/portal/PortalLayout.vue'),
      meta: { requiresAuth: true, standalone: true },
      children: [
        { path: '', redirect: (to) => `/${to.params.projectId}/portal/meetings` },
        {
          path: 'meetings',
          name: 'portal-meetings',
          component: () => import('../views/portal/PortalMeetingsView.vue'),
        },
        {
          path: 'tasks',
          name: 'portal-tasks',
          component: () => import('../views/portal/PortalTasksView.vue'),
        },
        {
          path: 'search',
          name: 'portal-search',
          component: () => import('../views/portal/PortalSearchView.vue'),
        },
        {
          path: 'question',
          name: 'portal-question',
          component: () => import('../views/portal/PortalQuestionView.vue'),
        },
      ],
    },
    {
      path: '/:projectId/map',
      name: 'map',
      component: () => import('../views/MapView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/:projectId/dashboard',
      redirect: (to) => `/${to.params.projectId}/library`,
    },
    {
      path: '/:projectId/draft',
      redirect: (to) => `/${to.params.projectId}/library`,
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
      redirect: (to) => `/${to.params.projectId}/library/${to.params.citekey}/review`,
    },
    {
      path: '/:projectId/library/:citekey/review',
      name: 'fragment-review',
      component: () => import('../views/FragmentReviewView.vue'),
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

router.beforeEach(async (to) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.requiresAuth && !token) return { name: 'login' }
  if ((to.name === 'login' || to.name === 'register') && token) {
    try {
      const { userProjects } = await import('../api/client')
      const data = await userProjects.list()
      const first = data.projects[0]
      if (first) return { path: postLoginPath(first.project_id) }
    } catch { /* fall through */ }
    return { path: '/library' }
  }
})

router.afterEach((to) => {
  if (typeof window.ym === 'function') {
    window.ym(108182650, 'hit', window.location.origin + to.fullPath)
  }
})

export default router
