# CiteQ Dashboard

Vue 3 SaaS frontend for the Klemma API (Epic #191, ADR-015 proprietary).

## Stack

Vue 3 + TypeScript + Vue Router + Pinia + Tailwind CSS v4 + Vite

## Structure

```
saas/dashboard/
├── src/
│   ├── api/client.ts      — typed API client, JWT management, token refresh
│   ├── router/index.ts    — routes + auth guard
│   ├── views/             — page components
│   │   ├── LandingView    — public landing page (Russian)
│   │   ├── LoginView      — email/password login
│   │   ├── RegisterView   — registration form
│   │   └── DashboardView  — status cards + coverage
│   └── assets/main.css    — Tailwind entry point
├── vite.config.ts         — Tailwind plugin + /api proxy
└── package.json
```

## Development

```bash
npm install
npm run dev          # http://localhost:5173
# Backend must run separately:
uvicorn klemma.api.app:create_app --factory  # http://localhost:8000
```

Vite proxies `/api/*` to `localhost:8000` (strips `/api` prefix).

## API Client

`src/api/client.ts` handles:
- JWT token storage (localStorage)
- Auto-refresh on 401 (tries POST /auth/refresh before redirecting)
- Auth endpoints (login/register) return 401 as ApiError (no redirect)
- Typed wrappers for all backend endpoints

## Adding a new page

1. Create `src/views/MyView.vue`
2. Add route in `src/router/index.ts` (add `meta: { requiresAuth: true }` if protected)
3. Use `api/client.ts` for backend calls

## Maintaining this file

Update when adding views, changing the API client, or modifying the build setup.
