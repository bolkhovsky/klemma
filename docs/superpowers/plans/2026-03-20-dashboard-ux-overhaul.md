# Dashboard UX Overhaul — Health Screen + Research Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CRUD-focused dashboard with a diagnostic-first experience — Health screen with chapter verdicts + Research Roadmap with readiness statuses, navigation consolidated from 5 to 3 items.

**Architecture:** Frontend-only changes. No new backend endpoints. All diagnostic logic (health scores, verdicts, readiness) is computed on the frontend from existing API responses (`GET /analyze/status`, `GET /projects/coverage`, `GET /library/gaps`, `GET /projects/{id}/research`). CoverageView content is embedded into HealthView as expandable chapter rows. OutlineView becomes accessible via a modal triggered from project settings.

**Tech Stack:** Vue 3 + TypeScript + Tailwind CSS v4 (existing stack, no new deps)

**GitHub Issue:** #223

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/views/HealthView.vue` | New Health screen: health banner, chapter table, gaps, stats |
| Modify | `src/views/ResearchView.vue` | Roadmap with readiness statuses replacing flat list |
| Modify | `src/components/AppLayout.vue:183-244` | Navigation: 5 links → 3 (Здоровье, Библиотека, Исследование) |
| Modify | `src/router/index.ts:30-52` | Rename dashboard route → health, remove coverage route |
| Keep | `src/views/CoverageView.vue` | Keep file (route removed but code reusable for reference) |
| Keep | `src/views/OutlineView.vue` | Keep file (route kept but hidden from primary nav) |

---

### Task 1: Create HealthView.vue — skeleton with API calls

**Files:**
- Create: `saas/dashboard/src/views/HealthView.vue`

- [ ] **Step 1: Create HealthView with data fetching**

This view replaces DashboardView. It fetches the same data plus gaps and coverage, then computes diagnostics.

```vue
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { analyze, library, projects as projectsApi } from '@/api/client'
import { useProjectStore } from '@/stores/project'
import AppLayout from '@/components/AppLayout.vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const loading = ref(true)

// Raw API data
const status = ref<{
  sources: { total: number; completed: number; pending: number; failed: number }
  coverage: { section: string; source_count: number }[]
  total_fragments: number
} | null>(null)

const gaps = ref<{ title: string; authors: string; year: number | null; cited_by_count: number }[]>([])

// NOTE: projects.coverage() returns { total_sources: number, sections: Record<string, number>, chapters: Record<string, number> }
// where sections maps section_id → source_count, and chapters maps chapter_number → source_count.
// Chapter names and section groupings come from projectStore.activeOutline, NOT from the API.
const sectionCounts = ref<Record<string, number>>({})

const MIN_SOURCES_COVERED = 3

// Computed: chapter assessment — built from outline (names) + coverage (counts)
// Follows the same pattern as CoverageView.vue's sectionsByChapter computed.
interface ChapterAssessment {
  number: string
  name: string
  sourceCount: number
  sections: { id: string; name: string; count: number; verdict: 'empty' | 'low' | 'covered' }[]
  verdict: 'empty' | 'low' | 'covered'
}

const expandedChapters = ref<Set<string>>(new Set())

const chapterAssessment = computed<ChapterAssessment[]>(() => {
  const outline = projectStore.activeOutline
  if (!outline || outline.length === 0) return []

  // Group outline sections by chapter prefix (split on '.')
  const groups = new Map<string, { id: string; name: string }[]>()
  const chapterNames = new Map<string, string>()

  // Sort outline sections numerically
  const sorted = [...outline].sort((a, b) => {
    const ap = a.id.split('.').map(Number)
    const bp = b.id.split('.').map(Number)
    for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
      const diff = (ap[i] || 0) - (bp[i] || 0)
      if (diff !== 0) return diff
    }
    return 0
  })

  for (const s of sorted) {
    const ch = s.id.split('.')[0]
    // Sections without a dot (e.g. "1", "2") are chapter-level entries — use as chapter name
    if (!s.id.includes('.')) {
      chapterNames.set(ch, s.name)
    } else {
      if (!groups.has(ch)) groups.set(ch, [])
      groups.get(ch)!.push({ id: s.id, name: s.name })
    }
  }

  return Array.from(groups.entries())
    .sort(([a], [b]) => parseFloat(a) - parseFloat(b))
    .map(([num, secs]) => {
      const sections = secs.map(s => {
        const count = sectionCounts.value[s.id] ?? 0
        return {
          id: s.id,
          name: s.name,
          count,
          verdict: count === 0 ? 'empty' as const : count < MIN_SOURCES_COVERED ? 'low' as const : 'covered' as const,
        }
      })
      const totalSources = sections.reduce((sum, s) => sum + s.count, 0)
      const allEmpty = sections.length > 0 && sections.every(s => s.count === 0)
      const anyLow = sections.some(s => s.verdict !== 'covered')

      return {
        number: num,
        name: chapterNames.get(num) ?? `Глава ${num}`,
        sourceCount: totalSources,
        sections,
        verdict: allEmpty ? 'empty' as const : anyLow ? 'low' as const : 'covered' as const,
      }
    })
})

// Computed: health score (% of sections with 3+ sources)
const healthScore = computed(() => {
  const allSections = chapterAssessment.value.flatMap(ch => ch.sections)
  if (allSections.length === 0) return 0
  const covered = allSections.filter(s => s.verdict === 'covered').length
  return Math.round((covered / allSections.length) * 100)
})

// Computed: diagnosis text
const diagnosis = computed(() => {
  const emptyChapters = chapterAssessment.value.filter(ch => ch.verdict === 'empty')
  const lowChapters = chapterAssessment.value.filter(ch => ch.verdict === 'low')

  if (chapterAssessment.value.length === 0) return 'Загрузите источники и создайте структуру проекта.'

  if (emptyChapters.length > 0) {
    const names = emptyChapters.map(ch => `Гл. ${ch.number}`).join(', ')
    return `${names} ${emptyChapters.length === 1 ? 'не имеет' : 'не имеют'} источников. Критическая проблема.`
  }

  if (lowChapters.length > 0) {
    const names = lowChapters.map(ch => `Гл. ${ch.number}`).join(', ')
    return `${names} — недостаточно источников (менее 3 на раздел).`
  }

  return 'Библиотека хорошо сбалансирована по всем главам.'
})

const diagnosisColor = computed(() => {
  const empty = chapterAssessment.value.some(ch => ch.verdict === 'empty')
  const low = chapterAssessment.value.some(ch => ch.verdict === 'low')
  if (empty) return 'text-[var(--color-err)]'
  if (low) return 'text-[var(--color-warn)]'
  return 'text-[var(--color-ok)]'
})

function toggleChapter(num: string) {
  if (expandedChapters.value.has(num)) {
    expandedChapters.value.delete(num)
  } else {
    expandedChapters.value.add(num)
  }
}

function verdictLabel(v: 'empty' | 'low' | 'covered') {
  return v === 'empty' ? 'Пусто' : v === 'low' ? 'Мало' : 'Покрыта'
}

function verdictColor(v: 'empty' | 'low' | 'covered') {
  return v === 'empty'
    ? 'text-[var(--color-err)] bg-[var(--color-err-bg)]'
    : v === 'low'
      ? 'text-[var(--color-warn)] bg-[var(--color-warn-bg)]'
      : 'text-[var(--color-ok)] bg-[var(--color-ok-bg)]'
}

function verdictDot(v: 'empty' | 'low' | 'covered') {
  return v === 'empty'
    ? 'bg-[var(--color-err)]'
    : v === 'low'
      ? 'bg-[var(--color-warn)]'
      : 'bg-[var(--color-ok)]'
}

// Max bar width reference
const maxSectionCount = computed(() => {
  const all = chapterAssessment.value.flatMap(ch => ch.sections)
  return Math.max(1, ...all.map(s => s.count))
})

onMounted(async () => {
  try {
    // Load outline first (needed for chapter names in assessment)
    if (!projectStore.activeOutline || projectStore.activeOutline.length === 0) {
      await projectStore.loadProjects()
    }

    // Load all data in parallel
    const [s, g, c] = await Promise.all([
      analyze.status(),
      library.gaps().catch(() => []),
      projectsApi.coverage().catch(() => null),
    ])
    status.value = s

    // Parse gaps — API may return {gaps: [...]} or [...] or {detail: "..."}
    const rawGaps = (g as any)?.gaps ?? g
    gaps.value = Array.isArray(rawGaps) ? rawGaps : []

    // Extract section counts from coverage response
    if (c && typeof c === 'object' && 'sections' in c) {
      sectionCounts.value = (c as any).sections ?? {}
    }
  } catch {
    router.push('/login')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <AppLayout>
    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <div v-else-if="status" class="space-y-8">
      <!-- Page title -->
      <div class="animate-in">
        <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
          Здоровье библиотеки
        </h1>
        <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
          Диагностика и рекомендации
        </p>
      </div>

      <!-- Empty state: no sources at all -->
      <div
        v-if="status.sources.total === 0"
        class="animate-in animate-in-delay-1 rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center"
      >
        <div class="mx-auto w-16 h-16 rounded-2xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-5">
          <svg class="w-8 h-8 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.331 0 4.512.89 6.148 2.354M12 6.042c1.985-1.392 4.37-2.292 7.025-2.292.944 0 1.857.14 2.725.4v14.25A9.001 9.001 0 0018 18c-2.331 0-4.512.89-6.148 2.354M12 6.042V20.354" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)]">
          Начните исследование
        </h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)] max-w-md mx-auto leading-relaxed">
          Загрузите PDF-статьи в библиотеку. LitResearch извлечёт ключевые фрагменты,
          оценит покрытие по разделам и предложит, как закрыть пробелы.
        </p>
        <RouterLink
          :to="`/${route.params.projectId}/library`"
          class="mt-6 inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] transition-colors shadow-sm"
        >
          Перейти в библиотеку
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
          </svg>
        </RouterLink>
      </div>

      <!-- Main content: has sources -->
      <template v-else>
        <!-- Health Banner -->
        <div class="animate-in animate-in-delay-1 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-6">
          <div class="flex items-center justify-between mb-3">
            <span class="text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">Общая оценка</span>
            <span class="font-[var(--font-mono)] text-2xl font-bold text-[var(--color-ink)]">{{ healthScore }}<span class="text-sm font-normal text-[var(--color-ink-muted)]">/100</span></span>
          </div>
          <div class="h-2 w-full rounded-full bg-[var(--color-rule-light)] overflow-hidden mb-3">
            <div
              class="h-full rounded-full transition-all duration-700"
              :class="healthScore >= 70 ? 'bg-[var(--color-ok)]' : healthScore >= 30 ? 'bg-[var(--color-warn)]' : 'bg-[var(--color-err)]'"
              :style="{ width: `${healthScore}%` }"
            ></div>
          </div>
          <div class="flex items-center justify-between">
            <p class="text-sm font-medium" :class="diagnosisColor">{{ diagnosis }}</p>
            <div class="flex gap-4 text-xs text-[var(--color-ink-muted)]">
              <span><span class="font-[var(--font-mono)] text-sm text-[var(--color-ink)]">{{ status.sources.total }}</span> источников</span>
              <span><span class="font-[var(--font-mono)] text-sm text-[var(--color-ink)]">{{ status.total_fragments }}</span> фрагментов</span>
              <span v-if="gaps.length > 0"><span class="font-[var(--font-mono)] text-sm text-[var(--color-warn)]">{{ gaps.length }}</span> ref-gaps</span>
            </div>
          </div>
        </div>

        <!-- Chapter Assessment + Quick Stats row -->
        <div class="grid grid-cols-3 gap-6 animate-in animate-in-delay-2">
          <!-- Chapter Assessment table (2 cols) -->
          <div class="col-span-2 space-y-4">
            <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
              Оценка по главам
            </h2>

            <div v-if="chapterAssessment.length === 0" class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-8 text-center">
              <p class="text-sm text-[var(--color-ink-muted)]">Создайте структуру проекта для отображения оценки.</p>
              <RouterLink
                :to="`/${route.params.projectId}/outline`"
                class="mt-3 inline-flex text-sm text-[var(--color-accent)] hover:text-[var(--color-accent-deep)]"
              >
                Настроить структуру &rarr;
              </RouterLink>
            </div>

            <div v-else class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] divide-y divide-[var(--color-rule-light)]">
              <div
                v-for="ch in chapterAssessment"
                :key="ch.number"
              >
                <!-- Chapter row -->
                <button
                  @click="toggleChapter(ch.number)"
                  class="flex w-full items-center justify-between px-5 py-4 hover:bg-[var(--color-paper-warm)] transition-colors text-left"
                >
                  <div class="flex items-center gap-3 min-w-0">
                    <div class="w-2 h-2 rounded-full flex-shrink-0" :class="verdictDot(ch.verdict)"></div>
                    <span class="font-[var(--font-mono)] text-xs text-[var(--color-accent)] flex-shrink-0">Гл. {{ ch.number }}</span>
                    <span class="text-sm font-medium text-[var(--color-ink)] truncate">{{ ch.name }}</span>
                  </div>
                  <div class="flex items-center gap-3 flex-shrink-0">
                    <span class="font-[var(--font-mono)] text-sm text-[var(--color-ink)]">{{ ch.sourceCount }}</span>
                    <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium" :class="verdictColor(ch.verdict)">
                      {{ verdictLabel(ch.verdict) }}
                    </span>
                    <svg
                      class="w-4 h-4 text-[var(--color-ink-muted)] transition-transform"
                      :class="{ 'rotate-180': expandedChapters.has(ch.number) }"
                      fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                    </svg>
                  </div>
                </button>

                <!-- Expanded sections -->
                <div v-if="expandedChapters.has(ch.number)" class="bg-[var(--color-paper)] px-5 pb-4">
                  <div
                    v-for="sec in ch.sections"
                    :key="sec.id"
                    class="flex items-center gap-3 py-2"
                  >
                    <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)] w-8 flex-shrink-0">{{ sec.id }}</span>
                    <span class="text-sm text-[var(--color-ink)] truncate flex-1 min-w-0">{{ sec.name }}</span>
                    <div class="w-32 h-1.5 rounded-full bg-[var(--color-rule-light)] overflow-hidden flex-shrink-0">
                      <div
                        class="h-full rounded-full transition-all"
                        :class="sec.verdict === 'covered' ? 'bg-[var(--color-ok)]' : sec.verdict === 'low' ? 'bg-[var(--color-warn)]' : 'bg-[var(--color-err)]'"
                        :style="{ width: `${Math.max(sec.count > 0 ? 10 : 0, (sec.count / maxSectionCount) * 100)}%` }"
                      ></div>
                    </div>
                    <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)] w-6 text-right flex-shrink-0">{{ sec.count }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Quick Stats (1 col) -->
          <div class="col-span-1 space-y-4">
            <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
              Статистика
            </h2>
            <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] divide-y divide-[var(--color-rule-light)]">
              <div class="flex items-center justify-between px-5 py-4">
                <span class="text-sm text-[var(--color-ink-muted)]">Источников</span>
                <span class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-ink)]">{{ status.sources.total }}</span>
              </div>
              <div class="flex items-center justify-between px-5 py-4">
                <span class="text-sm text-[var(--color-ink-muted)]">Обработано</span>
                <span class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-ok)]">{{ status.sources.completed }}</span>
              </div>
              <div class="flex items-center justify-between px-5 py-4">
                <span class="text-sm text-[var(--color-ink-muted)]">В очереди</span>
                <span class="font-[var(--font-mono)] text-xl font-medium" :class="status.sources.pending > 0 ? 'text-[var(--color-warn)]' : 'text-[var(--color-ink-muted)]'">{{ status.sources.pending }}</span>
              </div>
              <div class="flex items-center justify-between px-5 py-4">
                <span class="text-sm text-[var(--color-ink-muted)]">Фрагментов</span>
                <span class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-accent)]">{{ status.total_fragments }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Top Gaps -->
        <div v-if="gaps.length > 0" class="space-y-4 animate-in animate-in-delay-3">
          <div class="flex items-center justify-between">
            <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
              Пропущенные работы
            </h2>
            <span class="text-xs text-[var(--color-ink-muted)]">{{ gaps.length }} найдено</span>
          </div>

          <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] divide-y divide-[var(--color-rule-light)]">
            <div
              v-for="(gap, i) in gaps.slice(0, 7)"
              :key="i"
              class="flex items-center justify-between px-5 py-3.5 hover:bg-[var(--color-paper-warm)] transition-colors"
            >
              <div class="min-w-0 flex-1">
                <p class="text-sm font-medium text-[var(--color-ink)] truncate">{{ gap.title }}</p>
                <p class="text-xs text-[var(--color-ink-muted)] mt-0.5">
                  {{ gap.authors }}<span v-if="gap.year"> ({{ gap.year }})</span>
                </p>
              </div>
              <div class="flex items-center gap-3 flex-shrink-0 ml-4">
                <span class="inline-flex items-center rounded-full bg-[var(--color-warn-bg)] px-2 py-0.5 text-xs font-medium text-[var(--color-warn)]">
                  x{{ gap.cited_by_count }}
                </span>
                <a
                  :href="`https://scholar.google.com/scholar?q=${encodeURIComponent(gap.title)}`"
                  target="_blank"
                  rel="noopener"
                  class="text-xs text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] transition-colors whitespace-nowrap"
                >
                  Найти &nearr;
                </a>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </AppLayout>
</template>
```

- [ ] **Step 2: Verify the file compiles**

Run: `cd saas/dashboard && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No errors related to HealthView.vue (warnings in other files acceptable)

- [ ] **Step 3: Commit**

```bash
git add saas/dashboard/src/views/HealthView.vue
git commit -m "feat(dashboard): add HealthView with library diagnostics (#223)"
```

---

### Task 2: Update router — rename dashboard→health, keep outline accessible

**Files:**
- Modify: `saas/dashboard/src/router/index.ts`

- [ ] **Step 1: Update routes**

In `router/index.ts`, make these changes:

1. Change the dashboard route to point to HealthView and rename:
```typescript
// Replace:
{
  path: '/:projectId/dashboard',
  name: 'dashboard',
  component: () => import('../views/DashboardView.vue'),
  meta: { requiresAuth: true },
},
// With:
{
  path: '/:projectId/health',
  name: 'health',
  component: () => import('../views/HealthView.vue'),
  meta: { requiresAuth: true },
},
// Add redirect for old URL:
{
  path: '/:projectId/dashboard',
  redirect: to => `/${to.params.projectId}/health`,
},
```

2. Keep all other routes as-is (outline, coverage stay accessible via direct URL, just not in nav).

- [ ] **Step 2: Verify router compiles**

Run: `cd saas/dashboard && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add saas/dashboard/src/router/index.ts
git commit -m "feat(router): rename dashboard→health route, add redirect (#223)"
```

---

### Task 3: Update AppLayout — consolidate navigation 5→3

**Files:**
- Modify: `saas/dashboard/src/components/AppLayout.vue:183-244`

- [ ] **Step 1: Replace 5 nav links with 3**

In `AppLayout.vue`, replace the `<nav>` section (lines ~183-244) containing 5 `<RouterLink>` elements (Обзор, Структура, Библиотека, Покрытие, Исследование) with exactly 3 links:

**Link 1 — Здоровье** (replaces Обзор):
- Path: `/${projectId}/health`
- Icon: heart/pulse icon (SVG below)
- Active when: path matches `/${projectId}/health`
- Label: "Здоровье"

**Link 2 — Библиотека** (stays):
- Path: `/${projectId}/library`
- Same icon as current
- Active when: path starts with `/${projectId}/library`
- Label: "Библиотека"

**Link 3 — Исследование** (stays):
- Path: `/${projectId}/research`
- Same icon as current
- Active when: path matches `/${projectId}/research`
- Label: "Исследование"

Also update `switchProject()` function (line ~19-26): change the modules array from `['dashboard', 'outline', 'library', 'coverage', 'research']` to `['health', 'outline', 'library', 'coverage', 'research']` (keep `outline` and `coverage` for URL-matching so users on those pages can switch projects without losing context) and change fallback from `'outline'` to `'health'`.

- [ ] **Step 2: Add outline access via a small gear/settings icon near project name**

In the project list section, add a small settings icon next to the active project that links to `/${projectId}/outline`. This keeps Outline accessible without it being a primary nav item.

```html
<!-- After the project button, for the active project only -->
<RouterLink
  v-if="route.params.projectId === project.project_id"
  :to="`/${project.project_id}/outline`"
  class="flex h-6 w-6 items-center justify-center rounded text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] hover:bg-[var(--color-accent-pale)] transition-colors flex-shrink-0"
  title="Структура проекта"
>
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-3.5 w-3.5">
    <path fill-rule="evenodd" d="M6.955 1.45A.5.5 0 0 1 7.452 1h1.096a.5.5 0 0 1 .497.45l.17 1.699c.484.12.94.312 1.356.562l1.321-.839a.5.5 0 0 1 .67.033l.774.775a.5.5 0 0 1 .034.67l-.839 1.32c.25.417.443.873.563 1.357l1.699.17a.5.5 0 0 1 .45.497v1.096a.5.5 0 0 1-.45.497l-1.7.17c-.12.484-.312.94-.562 1.356l.839 1.321a.5.5 0 0 1-.034.67l-.774.774a.5.5 0 0 1-.67.033l-1.32-.839c-.417.25-.873.443-1.357.563l-.17 1.699a.5.5 0 0 1-.497.45H7.452a.5.5 0 0 1-.497-.45l-.17-1.7c-.484-.12-.94-.312-1.356-.562l-1.321.839a.5.5 0 0 1-.67-.033l-.774-.775a.5.5 0 0 1-.034-.67l.839-1.32a5.518 5.518 0 0 1-.563-1.357l-1.699-.17A.5.5 0 0 1 1 8.548V7.452a.5.5 0 0 1 .45-.497l1.7-.17c.12-.484.312-.94.562-1.356l-.839-1.321a.5.5 0 0 1 .034-.67l.774-.774a.5.5 0 0 1 .67-.033l1.32.839c.417-.25.873-.443 1.357-.563l.17-1.699ZM11 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" clip-rule="evenodd" />
  </svg>
</RouterLink>
```

- [ ] **Step 3: Verify AppLayout renders correctly**

Run: `cd saas/dashboard && npm run dev` (manual check in browser or `npx vue-tsc --noEmit`)
Expected: 3 nav items visible, settings icon next to active project

- [ ] **Step 4: Commit**

```bash
git add saas/dashboard/src/components/AppLayout.vue
git commit -m "feat(nav): consolidate sidebar from 5 to 3 items (#223)"
```

---

### Task 4: Rewrite ResearchView — Roadmap with readiness statuses

**Files:**
- Modify: `saas/dashboard/src/views/ResearchView.vue`

- [ ] **Step 1: Rewrite ResearchView with roadmap layout**

Replace the entire file content. This preserves the existing generation logic (refs: `genJobId`, `genStatus`, `genError`, `pollTimer`, functions: `generate()`, `startPolling()`, `stopPolling()`) while adding the roadmap layout.

```vue
<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { research as researchApi, process as processApi, userProjects, projects as projectsApi, analyze } from '@/api/client'
import type { OutlineSection } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.projectId as string)

const loading = ref(true)
const MIN_SOURCES = 3

// --- Data from APIs ---
const outline = ref<OutlineSection[]>([])
const sectionCounts = ref<Record<string, number>>({}) // from coverage
const reportMap = ref<Record<string, string>>({})       // section → created_at

// --- Generation logic (preserved from original) ---
const genSectionId = ref('')
const generating = ref(false)
const genJobId = ref<string | null>(null)
const genStatus = ref('')
const genError = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// --- Roadmap computed ---
type Readiness = 'empty' | 'low' | 'ready' | 'done'

interface SectionRoadmap {
  id: string
  name: string
  sourceCount: number
  readiness: Readiness
  reportDate: string | null
}

const roadmap = computed<SectionRoadmap[]>(() => {
  // Only include second-level sections (1.1, 2.3, etc.) — chapters are not researchable
  const sections = outline.value.filter(s => /^\d+\.\d+$/.test(s.id))

  return sections
    .map(s => {
      const count = sectionCounts.value[s.id] ?? 0
      const reportDate = reportMap.value[s.id] ?? null
      let readiness: Readiness = 'empty'
      if (reportDate) {
        readiness = 'done'
      } else if (count >= MIN_SOURCES) {
        readiness = 'ready'
      } else if (count > 0) {
        readiness = 'low'
      }
      return { id: s.id, name: s.name, sourceCount: count, readiness, reportDate }
    })
    // Sort: empty → low → ready → done, then by section number
    .sort((a, b) => {
      const order: Record<Readiness, number> = { empty: 0, low: 1, ready: 2, done: 3 }
      const d = order[a.readiness] - order[b.readiness]
      if (d !== 0) return d
      const ap = a.id.split('.').map(Number)
      const bp = b.id.split('.').map(Number)
      for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
        const diff = (ap[i] || 0) - (bp[i] || 0)
        if (diff !== 0) return diff
      }
      return 0
    })
})

const counts = computed(() => ({
  empty: roadmap.value.filter(s => s.readiness === 'empty').length,
  low: roadmap.value.filter(s => s.readiness === 'low').length,
  ready: roadmap.value.filter(s => s.readiness === 'ready').length,
  done: roadmap.value.filter(s => s.readiness === 'done').length,
}))

function readinessLabel(r: Readiness) {
  return r === 'empty' ? 'Пусто' : r === 'low' ? 'Мало' : r === 'ready' ? 'Готов' : 'Обзор готов'
}

function readinessBadge(r: Readiness) {
  return r === 'empty'
    ? 'text-[var(--color-err)] bg-[var(--color-err-bg)]'
    : r === 'low'
      ? 'text-[var(--color-warn)] bg-[var(--color-warn-bg)]'
      : r === 'ready'
        ? 'text-[var(--color-accent)] bg-[var(--color-accent-pale)]'
        : 'text-[var(--color-ok)] bg-[var(--color-ok-bg)]'
}

function readinessDot(r: Readiness) {
  return r === 'empty' ? 'bg-[var(--color-err)]' : r === 'low' ? 'bg-[var(--color-warn)]' : r === 'ready' ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-ok)]'
}

function formatDate(iso: string) {
  try { return new Date(iso + 'Z').toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) }
  catch { return iso }
}

// --- Generation (preserved logic from original ResearchView) ---
async function generateFor(sectionId: string) {
  genSectionId.value = sectionId
  generating.value = true
  genError.value = ''
  genStatus.value = 'queued'

  try {
    const resp = await researchApi.generate(sectionId, projectId.value)
    genJobId.value = resp.job_id
    startPolling()
  } catch (e: any) {
    genError.value = e.message || 'Ошибка запуска'
    generating.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!genJobId.value) return
    try {
      const resp = await processApi.jobStatus(genJobId.value)
      genStatus.value = resp.status
      if (resp.status === 'finished') {
        stopPolling()
        generating.value = false
        const sec = genSectionId.value
        genJobId.value = null
        if (resp.result?.status === 'error') {
          genError.value = resp.result.detail || 'Генерация завершилась с ошибкой'
        } else {
          genSectionId.value = ''
          router.push(`/${projectId.value}/research/${sec}`)
        }
      } else if (resp.status === 'failed') {
        stopPolling()
        generating.value = false
        genJobId.value = null
        genError.value = resp.result?.detail || 'Генерация завершилась с ошибкой'
      }
    } catch { /* keep polling */ }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

// --- Data loading ---
async function loadData() {
  loading.value = true
  try {
    // Load outline
    if (projectStore.activeOutline && projectStore.activeOutline.length > 0) {
      outline.value = projectStore.activeOutline
    } else {
      const project = await userProjects.list()
      const p = project.projects.find(pr => pr.project_id === projectId.value)
      outline.value = p?.outline ?? []
    }

    // Load coverage + reports in parallel
    const [covResp, reportsResp] = await Promise.all([
      projectsApi.coverage().catch(() => null),
      researchApi.listReports(projectId.value).catch(() => ({ reports: [] })),
    ])

    // Section counts from coverage
    if (covResp && typeof covResp === 'object' && 'sections' in covResp) {
      sectionCounts.value = (covResp as any).sections ?? {}
    }

    // Report map: section → created_at
    const rMap: Record<string, string> = {}
    for (const r of (reportsResp as any).reports ?? []) {
      rMap[r.section] = r.created_at
    }
    reportMap.value = rMap
  } catch {
    // keep empty state
  } finally {
    loading.value = false
  }
}

// Re-resolve outline when store loads
watch(() => projectStore.activeOutline, (newOutline) => {
  if (newOutline && newOutline.length > 0) {
    outline.value = newOutline
  }
})

onMounted(loadData)
onUnmounted(stopPolling)
</script>

<template>
  <AppLayout>
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <div v-else class="space-y-8">
      <!-- Header -->
      <div class="animate-in">
        <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
          Исследование
        </h1>
        <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
          Готовность разделов к написанию обзора
        </p>
      </div>

      <!-- Summary counters -->
      <div v-if="roadmap.length > 0" class="animate-in animate-in-delay-1 grid grid-cols-4 gap-3">
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-4 py-3 text-center">
          <div class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-err)]">{{ counts.empty }}</div>
          <div class="text-xs text-[var(--color-ink-muted)]">Пусто</div>
        </div>
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-4 py-3 text-center">
          <div class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-warn)]">{{ counts.low }}</div>
          <div class="text-xs text-[var(--color-ink-muted)]">Мало источников</div>
        </div>
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-4 py-3 text-center">
          <div class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-accent)]">{{ counts.ready }}</div>
          <div class="text-xs text-[var(--color-ink-muted)]">Готов к генерации</div>
        </div>
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-4 py-3 text-center">
          <div class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-ok)]">{{ counts.done }}</div>
          <div class="text-xs text-[var(--color-ink-muted)]">Обзор готов</div>
        </div>
      </div>

      <!-- Generation status (shown while generating) -->
      <div v-if="generating" class="animate-in rounded-xl border border-[var(--color-accent)] bg-[var(--color-accent-pale)] p-5">
        <div class="flex items-center gap-3">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm font-medium text-[var(--color-accent-deep)]">
            Генерируем обзор для {{ genSectionId }}...
          </span>
          <span class="text-xs text-[var(--color-ink-muted)]">{{ genStatus }}</span>
        </div>
      </div>

      <div v-if="genError" class="rounded-xl border border-[var(--color-err)] bg-[var(--color-err-bg)] p-4">
        <p class="text-sm text-[var(--color-err)]">{{ genError }}</p>
      </div>

      <!-- Empty state: no outline -->
      <div v-if="roadmap.length === 0" class="animate-in animate-in-delay-1 rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center">
        <div class="mx-auto w-16 h-16 rounded-2xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-5">
          <svg class="w-8 h-8 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)]">
          Начните исследование
        </h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)] max-w-md mx-auto leading-relaxed">
          Определите структуру работы и загрузите источники, чтобы начать генерацию обзоров.
        </p>
        <RouterLink
          :to="`/${projectId}/outline`"
          class="mt-6 inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] transition-colors shadow-sm"
        >
          Настроить структуру
        </RouterLink>
      </div>

      <!-- Roadmap: all sections with readiness -->
      <div v-else class="animate-in animate-in-delay-1 space-y-2">
        <div
          v-for="sec in roadmap"
          :key="sec.id"
          class="rounded-xl border bg-[var(--color-paper-white)] px-5 py-4 transition-all"
          :class="sec.readiness === 'done'
            ? 'border-[var(--color-rule)]'
            : sec.readiness === 'ready'
              ? 'border-[var(--color-accent)] border-opacity-50'
              : 'border-[var(--color-rule)]'"
        >
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3 min-w-0">
              <div class="w-2 h-2 rounded-full flex-shrink-0" :class="readinessDot(sec.readiness)"></div>
              <span class="font-[var(--font-mono)] text-sm text-[var(--color-accent)] font-medium flex-shrink-0">{{ sec.id }}</span>
              <span class="text-sm font-medium text-[var(--color-ink)] truncate">{{ sec.name }}</span>
            </div>

            <div class="flex items-center gap-3 flex-shrink-0 ml-4">
              <span class="text-xs text-[var(--color-ink-muted)]">
                {{ sec.sourceCount }} ист.
              </span>
              <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium" :class="readinessBadge(sec.readiness)">
                {{ readinessLabel(sec.readiness) }}
              </span>

              <!-- Action based on readiness -->
              <RouterLink
                v-if="sec.readiness === 'done'"
                :to="`/${projectId}/research/${sec.id}`"
                class="text-xs text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] transition-colors whitespace-nowrap"
              >
                Открыть &rarr;
              </RouterLink>

              <button
                v-else-if="sec.readiness === 'ready'"
                @click="generateFor(sec.id)"
                :disabled="generating"
                class="rounded-md bg-[var(--color-accent)] px-3 py-1 text-xs font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors whitespace-nowrap"
              >
                Сгенерировать
              </button>

              <RouterLink
                v-else
                :to="`/${projectId}/library`"
                class="text-xs text-[var(--color-warn)] hover:text-[var(--color-ink)] transition-colors whitespace-nowrap"
              >
                {{ sec.readiness === 'empty' ? 'Добавить источники' : `Нужно ещё ${MIN_SOURCES - sec.sourceCount}` }}
              </RouterLink>
            </div>
          </div>

          <!-- Report date if done -->
          <p v-if="sec.reportDate" class="mt-1 ml-8 text-xs text-[var(--color-ink-muted)]">
            Обзор от {{ formatDate(sec.reportDate) }}
          </p>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
```

- [ ] **Step 2: Verify generation flow still works**

Run: `cd saas/dashboard && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No type errors

- [ ] **Step 3: Test manually**

Run: `cd saas/dashboard && npm run dev` — navigate to Research, verify:
1. Sections appear with colored readiness dots
2. "Сгенерировать" button on ready sections triggers generation + polling
3. "Открыть" link on done sections navigates to ReportView
4. "Добавить источники" link on empty sections goes to Library

- [ ] **Step 4: Commit**

```bash
git add saas/dashboard/src/views/ResearchView.vue
git commit -m "feat(research): roadmap with readiness statuses per section (#223)"
```

---

### Task 5: Final cleanup and verification

**Files:**
- Verify all files compile and work together

- [ ] **Step 1: Run full type check**

Run: `cd saas/dashboard && npx vue-tsc --noEmit`
Expected: No errors

- [ ] **Step 2: Run dev server and manually verify**

Run: `cd saas/dashboard && npm run dev`

Verify:
1. Sidebar shows 3 nav items (Здоровье, Библиотека, Исследование)
2. Settings icon appears next to active project → navigates to /outline
3. Health screen shows: health banner, chapter table, gaps section, stats
4. Clicking a chapter expands sections with bars
5. Research screen shows all sections with readiness colors
6. Old /dashboard URL redirects to /health
7. /outline and /coverage URLs still work via direct navigation
8. Empty project shows onboarding CTA

- [ ] **Step 3: Build for production**

Run: `cd saas/dashboard && npm run build`
Expected: Build completes without errors

- [ ] **Step 4: Update `saas/dashboard/CLAUDE.md`**

Update the Structure section:
- Replace `DashboardView` with `HealthView` — library health diagnostics, chapter assessment, gaps
- Note ResearchView now shows roadmap with readiness statuses
- Update nav description: 3 items (Здоровье, Библиотека, Исследование)
- Note OutlineView and CoverageView still accessible via direct URL, not in primary nav

- [ ] **Step 5: Final commit if any remaining changes**

```bash
git add -A saas/dashboard/
git commit -m "feat(dashboard): complete UX overhaul — health + research roadmap (#223)"
```
