import { createRouter, createWebHistory } from 'vue-router'

/** Portal-only build (Bonum) lands on the meeting portal instead of the
 *  academic library after login. Toggled by VITE_PORTAL_ONLY at build time. */
export const PORTAL_ONLY =
  import.meta.env.VITE_PORTAL_ONLY === '1' || import.meta.env.VITE_PORTAL_ONLY === 'true'

export function postLoginPath(projectId: string): string {
  return PORTAL_ONLY ? `/${projectId}/portal/meetings` : `/${projectId}/library`
}

/**
 * Санитайзер для `?redirect=` — возвращает путь только если он ведёт внутрь SPA.
 *
 * Страница логина принимает адрес возврата из query, поэтому без проверки она
 * становится open-redirect: `/login?redirect=https://evil.example` увёл бы
 * пользователя на чужой домен сразу после ввода пароля. Пропускаем только
 * абсолютный путь с одним ведущим слэшем: `//host` браузер трактует как
 * protocol-relative URL на другой хост, а `\` в некоторых движках нормализуется
 * в `/` — обе формы отсекаем явно.
 */
export function safeRedirect(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw) return null
  if (!raw.startsWith('/')) return null
  if (raw.startsWith('//') || raw.startsWith('/\\')) return null
  return raw
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
    // External deep link to one meeting (mobile app, email). Resolves the
    // user's project, then replaces itself with the real portal route.
    {
      path: '/meetings/:sourceId',
      name: 'meeting-deeplink',
      component: () => import('../views/portal/MeetingDeepLinkView.vue'),
      meta: { requiresAuth: true, standalone: true },
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
          path: 'analytics',
          name: 'portal-analytics',
          component: () => import('../views/portal/PortalAnalyticsView.vue'),
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
  // Куда пользователь шёл — в query, иначе внешний диплинк (напр. /meetings/<id>
  // из мобильного приложения) с непрогретой сессией теряется на логине.
  if (to.meta.requiresAuth && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if ((to.name === 'login' || to.name === 'register') && token) {
    // Строкой, а не { path }: fullPath несёт query (`?open=<id>`), а объектная
    // форма трактует path как чистый путь.
    const back = safeRedirect(to.query.redirect)
    if (back) return back
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
