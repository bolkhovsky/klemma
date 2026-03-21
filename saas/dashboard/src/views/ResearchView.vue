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
