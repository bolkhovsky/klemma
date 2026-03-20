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
