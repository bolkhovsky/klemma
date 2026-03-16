<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { research as researchApi, process as processApi, projects, library } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.projectId as string)

// Section data
interface SectionData {
  id: string
  name: string
  sourceCount: number
  sources: { citekey: string; title: string; fragmentCount: number }[]
  report: { text: string; created_at: string; model: string } | null
  reportLoading: boolean
}

const sections = ref<SectionData[]>([])
const loading = ref(true)
const expandedSection = ref<string | null>(null)

// Stored report index: which sections have reports
const reportIndex = ref<Record<string, string>>({}) // section → created_at

// Research generation
const activeJob = ref<{ id: string; section: string } | null>(null)
const jobStatus = ref('')
const jobError = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

async function loadSections() {
  loading.value = true
  try {
    const [cov, reportList] = await Promise.all([
      projects.coverage(),
      researchApi.listReports(projectId.value).catch(() => ({ reports: [] })),
    ])
    const outline = projectStore.activeOutline ?? []

    // Build report index
    const idx: Record<string, string> = {}
    for (const r of reportList.reports) {
      idx[r.section] = r.created_at
    }
    reportIndex.value = idx

    // Build section data
    const sectionIds = Object.keys(cov.sections).sort((a, b) => {
      const ap = a.split('.').map(Number)
      const bp = b.split('.').map(Number)
      for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
        const diff = (ap[i] || 0) - (bp[i] || 0)
        if (diff !== 0) return diff
      }
      return 0
    })

    const sectionData: SectionData[] = []
    for (const sid of sectionIds) {
      const outlineEntry = outline.find(s => s.id === sid)
      sectionData.push({
        id: sid,
        name: outlineEntry?.name ?? sid,
        sourceCount: cov.sections[sid] ?? 0,
        sources: [],
        report: null,
        reportLoading: false,
      })
    }

    for (const s of outline) {
      if (!sectionData.find(sd => sd.id === s.id)) {
        sectionData.push({ id: s.id, name: s.name, sourceCount: 0, sources: [], report: null, reportLoading: false })
      }
    }

    sectionData.sort((a, b) => {
      const ap = a.id.split('.').map(Number)
      const bp = b.id.split('.').map(Number)
      for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
        const diff = (ap[i] || 0) - (bp[i] || 0)
        if (diff !== 0) return diff
      }
      return 0
    })

    sections.value = sectionData
  } catch {
    sections.value = []
  } finally {
    loading.value = false
  }
}

async function toggleSection(sectionId: string) {
  if (expandedSection.value === sectionId) {
    expandedSection.value = null
    return
  }
  expandedSection.value = sectionId
  const sec = sections.value.find(s => s.id === sectionId)
  if (!sec) return

  // Load report if available and not yet loaded
  if (reportIndex.value[sectionId] && !sec.report && !sec.reportLoading) {
    sec.reportLoading = true
    try {
      const report = await researchApi.getReport(projectId.value, sectionId)
      sec.report = { text: report.report_text, created_at: report.created_at, model: report.model }
    } catch {
      // no report
    } finally {
      sec.reportLoading = false
    }
  }

  // Load sources
  if (sec.sources.length === 0 && sec.sourceCount > 0) {
    try {
      const resp = await projects.sectionSources(sectionId)
      const sources = []
      for (const ck of resp.citekeys) {
        try {
          const src = await library.get(ck)
          sources.push({ citekey: ck, title: src.title || ck, fragmentCount: src.fragments?.length ?? 0 })
        } catch {
          sources.push({ citekey: ck, title: ck, fragmentCount: 0 })
        }
      }
      sec.sources = sources
    } catch { /* ignore */ }
  }
}

async function generateReport(sectionId: string) {
  jobError.value = ''
  jobStatus.value = 'queued'

  try {
    const resp = await researchApi.generate(sectionId, projectId.value)
    activeJob.value = { id: resp.job_id, section: sectionId }
    startPolling()
  } catch (e: any) {
    jobError.value = e.message || 'Ошибка запуска'
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!activeJob.value) return
    try {
      const resp = await processApi.jobStatus(activeJob.value.id)
      jobStatus.value = resp.status
      if (resp.status === 'finished') {
        stopPolling()
        const job = activeJob.value
        activeJob.value = null
        // Reload the report for this section
        if (job) {
          const sec = sections.value.find(s => s.id === job.section)
          if (sec) {
            try {
              const report = await researchApi.getReport(projectId.value, job.section)
              sec.report = { text: report.report_text, created_at: report.created_at, model: report.model }
              reportIndex.value[job.section] = report.created_at
            } catch { /* ignore */ }
          }
        }
      } else if (resp.status === 'failed') {
        stopPolling()
        jobError.value = resp.result?.detail || 'Генерация завершилась с ошибкой'
        activeJob.value = null
      }
    } catch { /* keep polling */ }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text)
}

function formatDate(iso: string) {
  try { return new Date(iso + 'Z').toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) }
  catch { return iso }
}

const totalSources = computed(() => sections.value.reduce((sum, s) => sum + s.sourceCount, 0))
const coveredSections = computed(() => sections.value.filter(s => s.sourceCount > 0).length)
const reportCount = computed(() => Object.keys(reportIndex.value).length)

onMounted(loadSections)
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
          Обзоры литературы и структура цитирования по разделам
        </p>
      </div>

      <!-- Summary stats -->
      <div v-if="sections.length > 0" class="animate-in animate-in-delay-1 grid grid-cols-4 gap-4">
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-5 py-4">
          <span class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">Разделов</span>
          <div class="mt-1 font-[var(--font-mono)] text-2xl font-medium text-[var(--color-ink)]">{{ sections.length }}</div>
        </div>
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-5 py-4">
          <span class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">С источниками</span>
          <div class="mt-1 font-[var(--font-mono)] text-2xl font-medium text-[var(--color-ok)]">{{ coveredSections }}</div>
        </div>
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-5 py-4">
          <span class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">Привязок</span>
          <div class="mt-1 font-[var(--font-mono)] text-2xl font-medium text-[var(--color-accent)]">{{ totalSources }}</div>
        </div>
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-5 py-4">
          <span class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">Обзоров</span>
          <div class="mt-1 font-[var(--font-mono)] text-2xl font-medium text-[var(--color-accent)]">{{ reportCount }}</div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-if="sections.length === 0" class="animate-in animate-in-delay-1 rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center">
        <div class="mx-auto w-14 h-14 rounded-2xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-4">
          <svg class="w-7 h-7 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)]">Нет данных для исследования</h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)] max-w-md mx-auto">
          Определите структуру работы и загрузите источники — система автоматически привяжет их к разделам.
        </p>
      </div>

      <!-- Active job indicator -->
      <div v-if="activeJob" class="animate-in rounded-xl border border-[var(--color-accent)] bg-[var(--color-accent-pale)] p-5">
        <div class="flex items-center gap-3">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm font-medium text-[var(--color-accent-deep)]">
            Генерируем обзор для раздела {{ activeJob.section }}...
          </span>
          <span class="text-xs text-[var(--color-ink-muted)]">{{ jobStatus }}</span>
        </div>
      </div>

      <!-- Job error -->
      <div v-if="jobError" class="rounded-xl border border-[var(--color-err)] bg-[var(--color-err-bg)] p-4">
        <p class="text-sm text-[var(--color-err)]">{{ jobError }}</p>
      </div>

      <!-- Section-by-section structure with reports -->
      <div v-if="sections.length > 0" class="animate-in animate-in-delay-2 space-y-3">
        <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
          Разделы
        </h2>

        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden divide-y divide-[var(--color-rule-light)]">
          <div v-for="sec in sections" :key="sec.id">
            <!-- Section row -->
            <button
              @click="toggleSection(sec.id)"
              class="w-full flex items-center gap-4 px-5 py-3.5 text-left hover:bg-[var(--color-paper-warm)] transition-colors"
            >
              <span class="font-[var(--font-mono)] text-sm text-[var(--color-accent)] w-12 shrink-0">{{ sec.id }}</span>
              <span class="text-sm font-medium text-[var(--color-ink)] flex-1 truncate">{{ sec.name }}</span>
              <!-- Report badge -->
              <span
                v-if="reportIndex[sec.id]"
                class="inline-flex items-center rounded-full bg-blue-50 text-blue-600 px-2 py-0.5 text-[10px] font-medium shrink-0"
              >
                обзор
              </span>
              <span
                class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium shrink-0"
                :class="sec.sourceCount > 0
                  ? 'bg-[var(--color-ok-bg)] text-[var(--color-ok)]'
                  : 'bg-[var(--color-rule-light)] text-[var(--color-ink-muted)]'"
              >
                {{ sec.sourceCount }} ист.
              </span>
              <svg
                class="w-4 h-4 text-[var(--color-ink-muted)] transition-transform shrink-0"
                :class="{ 'rotate-90': expandedSection === sec.id }"
                fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
              >
                <path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
              </svg>
            </button>

            <!-- Expanded section -->
            <div v-if="expandedSection === sec.id" class="border-t border-[var(--color-rule-light)]">
              <!-- Report block -->
              <div v-if="sec.reportLoading" class="px-5 py-6 flex items-center gap-2">
                <div class="h-3 w-3 animate-spin rounded-full border border-[var(--color-accent)] border-t-transparent"></div>
                <span class="text-sm text-[var(--color-ink-muted)]">Загрузка обзора...</span>
              </div>

              <div v-else-if="sec.report" class="px-5 py-5">
                <div class="flex items-center justify-between mb-3">
                  <div class="flex items-center gap-2">
                    <span class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">Обзор литературы</span>
                    <span class="text-[10px] text-[var(--color-ink-muted)]">{{ formatDate(sec.report.created_at) }}</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <button
                      @click.stop="copyToClipboard(sec.report!.text)"
                      class="text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] transition-colors"
                      title="Скопировать"
                    >
                      <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184" />
                      </svg>
                    </button>
                    <button
                      @click.stop="generateReport(sec.id)"
                      :disabled="!!activeJob"
                      class="text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] disabled:opacity-50 transition-colors"
                    >
                      Обновить
                    </button>
                  </div>
                </div>
                <div class="rounded-lg border border-[var(--color-rule-light)] bg-[var(--color-paper)] p-5 max-h-[600px] overflow-y-auto">
                  <div class="prose prose-sm max-w-none text-[var(--color-ink-light)] leading-relaxed whitespace-pre-wrap font-[var(--font-body)]">{{ sec.report.text }}</div>
                </div>
              </div>

              <!-- Generate button when no report -->
              <div v-else-if="sec.sourceCount > 0" class="px-5 py-4 flex items-center justify-between bg-[var(--color-paper-warm)]">
                <span class="text-sm text-[var(--color-ink-muted)]">Обзор литературы ещё не сгенерирован</span>
                <button
                  @click.stop="generateReport(sec.id)"
                  :disabled="!!activeJob"
                  class="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors"
                >
                  Сгенерировать обзор
                </button>
              </div>

              <!-- Sources list -->
              <div class="bg-[var(--color-paper-warm)] px-5 py-4">
                <div v-if="sec.sourceCount === 0" class="text-sm text-[var(--color-ink-muted)] italic">
                  Нет привязанных источников.
                </div>
                <div v-else-if="sec.sources.length === 0" class="flex items-center gap-2">
                  <div class="h-3 w-3 animate-spin rounded-full border border-[var(--color-accent)] border-t-transparent"></div>
                  <span class="text-sm text-[var(--color-ink-muted)]">Загрузка источников...</span>
                </div>
                <div v-else>
                  <h4 class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider mb-2">Источники</h4>
                  <div class="space-y-1.5">
                    <RouterLink
                      v-for="src in sec.sources"
                      :key="src.citekey"
                      :to="`/${route.params.projectId}/library/${src.citekey}`"
                      class="flex items-center gap-3 rounded-lg border border-[var(--color-rule-light)] bg-[var(--color-paper-white)] px-4 py-2 hover:border-[var(--color-accent)] transition-colors"
                    >
                      <span class="font-[var(--font-mono)] text-xs text-[var(--color-accent)] shrink-0">{{ src.citekey }}</span>
                      <span class="text-sm text-[var(--color-ink)] flex-1 truncate">{{ src.title }}</span>
                      <span v-if="src.fragmentCount > 0" class="text-xs text-[var(--color-ink-muted)] shrink-0">{{ src.fragmentCount }} фрагм.</span>
                    </RouterLink>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
