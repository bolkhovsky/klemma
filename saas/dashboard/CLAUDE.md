# Klemma Dashboard

Vue 3 SaaS frontend for the Klemma API (Epic #191, ADR-015 proprietary).

## Stack

Vue 3 + TypeScript + Vue Router + Pinia + Tailwind CSS v4 + Vite

## Structure

```
saas/dashboard/
├── src/
│   ├── api/client.ts      — typed API client, JWT management, token refresh
│   ├── router/index.ts    — routes + auth guard
│   ├── components/        — shared components
│   │   └── AppLayout      — sidebar nav (Карта / Лента / Библиотека / Редактор→/write) + project switcher + token meter
│   ├── views/             — page components
│   │   ├── LandingView    — public landing page (Russian)
│   │   ├── LoginView      — email/password login
│   │   ├── RegisterView   — registration form
│   │   ├── HealthView     — library health diagnostics: health score, chapter verdicts, gaps, stats
│   │   ├── LibraryView    — source table + PDF upload + reference gaps
│   │   ├── SourceView     — source detail: fragments, section assignment, processing
│   │   ├── ResearchView   — research roadmap: section readiness statuses + generation
│   │   ├── ReportView     — structured research report: argument blocks, citations
│   │   ├── OutlineView    — structure editor (accessible via settings icon, not in primary nav)
│   │   ├── CoverageView   — coverage heatmap (accessible via direct URL, not in primary nav)
│   │   ├── SectionEditorView — /:projectId/write — section-card write paradigm (Phase 5A, #273)
│   │   │     standalone layout (no AppLayout), topbar, doc-structure sidebar, section cards
│   │   │     3-state draft machine (0/1-4/5+ sources), prompt+presets, polling draft gen, diff review
│   │   ├── FileEditorView — /:projectId/edit/:filename — raw markdown editor (fallback)
│   │   ├── DashboardView  — legacy, redirects to MapView
│   │   └── GlobalLibraryView — all sources across projects
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
