<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { library, process, curation, analyze, ApiError } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

interface Source {
  citekey: string
  title: string
  authors: string
  year: number | null
  status: string
  doi: string
  sections: string[]
}

const sources = ref<Source[]>([])
const loading = ref(true)
const deleteConfirm = ref<string | null>(null)

// Upload state
const uploading = ref(false)
const uploadError = ref('')
const uploadSuccess = ref('')
const dragOver = ref(false)

// Processing state per source
const processingJobs = ref<Record<string, string>>({})

// Reference gaps
interface Gap {
  title: string
  authors: string | null
  year: number | null
  doi: string | null
  cited_by_count: number
  score?: number
  avg_quality?: number
  intent_weight?: number
  semantic_factor?: number
  intents?: string[]
  top_intent?: string | null
  sections_served?: Array<{ section: string; count: number }>
}
const gaps = ref<Gap[]>([])
const gapsDetail = ref('')

// LLM-curated recommendations (#332)
interface Recommendation {
  title: string
  authors: string
  year: number | null
  doi: string | null
  rationale: string
  score: number
}
const recommendations = ref<Recommendation[]>([])
const recommendationsDetail = ref('')
const recommendationsWarning = ref('')
const recommendationsModel = ref('')
const recommendationsGeneratedAt = ref('')
const recommendationsLoading = ref(false)

// Briefing
const briefing = ref<Awaited<ReturnType<typeof analyze.briefing>> | null>(null)
const briefingDismissed = ref(false)

// Curation stats per citekey: { accepted, total }
const curationStats = ref<Record<string, { accepted: number; total: number }>>({})


function shortAuthors(a: string | null): string {
  if (!a) return '—'
  return a.includes(',') ? a.split(',')[0]!.trim() + ' et al.' : a
}

/**
 * Russian plural form selector: 1 → `one`, 2-4 → `few`, 0/5+ → `many`.
 * Handles the teen exception (11-14 all take `many`).
 */
function plural(n: number, one: string, few: string, many: string): string {
  const n10 = n % 10
  const n100 = n % 100
  if (n10 === 1 && n100 !== 11) return one
  if (n10 >= 2 && n10 <= 4 && !(n100 >= 12 && n100 <= 14)) return few
  return many
}

/** Map citation intent identifiers to human-readable Russian labels. */
const INTENT_LABELS: Record<string, string> = {
  background: 'Фон',
  method: 'Метод',
  result_comparison: 'Сравнение',
  extends: 'Развивает',
  contrasts: 'Оппозиция',
  uses_data: 'Данные',
}

/** Map citation intent identifiers to Tailwind-compatible colour classes. */
const INTENT_COLORS: Record<string, string> = {
  background: 'intent-background',
  method: 'intent-method',
  result_comparison: 'intent-result',
  extends: 'intent-extends',
  contrasts: 'intent-contrasts',
  uses_data: 'intent-data',
}

function intentLabel(intent: string): string {
  return INTENT_LABELS[intent] ?? intent
}

function intentColor(intent: string): string {
  return INTENT_COLORS[intent] ?? 'intent-default'
}

/**
 * Return deduplicated intent array (API returns string[] already).
 * Also handles legacy comma-separated string for backward compatibility.
 */
function parseIntents(raw: string[] | string | null | undefined): string[] {
  if (!raw) return []
  if (Array.isArray(raw)) return [...new Set(raw)]
  // Fallback: legacy comma-separated string
  const seen = new Set<string>()
  return raw.split(',').map(s => s.trim()).filter(s => s && !seen.has(s) && seen.add(s))
}

/**
 * Format a numeric score with decomposition for tooltip.
 */
function formatScore(gap: Gap): string {
  if (gap.score == null) return String(gap.cited_by_count)
  return gap.score.toFixed(1)
}

function formatScoreTooltip(gap: Gap): string {
  if (gap.score == null) return `Ссылок: ${gap.cited_by_count}`
  const count = gap.cited_by_count
  const quality = (gap.avg_quality ?? 3).toFixed(1)
  const intent = (gap.intent_weight ?? 1).toFixed(2)
  const semantic = (gap.semantic_factor ?? 1).toFixed(2)
  return `${gap.score.toFixed(1)} = ${count} × ${quality}q × ${intent}i × ${semantic}s`
}

// Briefing coverage counters — derived from by_section readiness flags.
const readySectionCount = computed(() =>
  briefing.value?.by_section.filter(s => s.readiness === 'ready').length ?? 0
)
const totalSectionCount = computed(() => briefing.value?.by_section.length ?? 0)

async function loadGaps() {
  gaps.value = []
  gapsDetail.value = ''
  try {
    const data = await library.gaps()
    gaps.value = data.gaps
    gapsDetail.value = data.detail ?? ''
  } catch {
    gaps.value = []
  }
}

async function loadRecommendations() {
  const pid = projectStore.activeProjectId
  recommendations.value = []
  recommendationsDetail.value = ''
  recommendationsWarning.value = ''
  recommendationsModel.value = ''
  recommendationsGeneratedAt.value = ''
  if (!pid) return
  recommendationsLoading.value = true
  try {
    const data = await library.recommendations(pid)
    recommendations.value = data.recommendations
    recommendationsDetail.value = data.detail ?? ''
    recommendationsWarning.value = data.warning ?? ''
    recommendationsModel.value = data.model ?? ''
    recommendationsGeneratedAt.value = data.generated_at ?? ''
  } catch {
    // silently fall back to the raw gaps list below
  } finally {
    recommendationsLoading.value = false
  }
}

function formatGeneratedAt(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return iso
  }
}

async function loadSources() {
  loading.value = true
  try {
    const data = await library.list(projectStore.activeProjectId ?? undefined)
    sources.value = data.sources
  } catch {
    sources.value = []
  } finally {
    loading.value = false
  }
}

async function loadCurationStats() {
  const pid = projectStore.activeProjectId
  if (!pid) return
  try {
    // Get all curated (accepted + rejected) to compute per-source stats
    const data = await curation.curated(pid)
    const stats: Record<string, { accepted: number; total: number }> = {}

    for (const f of data.fragments) {
      if (!stats[f.citekey]) stats[f.citekey] = { accepted: 0, total: 0 }
      stats[f.citekey]!.total++
      if (f.verdict === 'accepted') {
        stats[f.citekey]!.accepted++
      }
    }
    curationStats.value = stats
  } catch {
    curationStats.value = {}
  }
}

// Total fragments per source (from processing, not curation)
const fragmentCounts = ref<Record<string, number>>({})

async function loadFragmentCounts() {
  // We'll fetch pending for each completed source to get total fragment count
  // This is expensive — skip if no project
  const pid = projectStore.activeProjectId
  if (!pid) return
  const completed = sources.value.filter(s => s.status === 'completed')
  for (const src of completed) {
    if (fragmentCounts.value[src.citekey] !== undefined) continue
    try {
      const data = await curation.pending(pid, src.citekey)
      fragmentCounts.value[src.citekey] = data.total
    } catch {
      // skip
    }
  }
}

function curationBadge(citekey: string): { text: string; cls: string } {
  const stats = curationStats.value[citekey]
  const total = fragmentCounts.value[citekey]
  if (total === undefined || total === 0) return { text: '—', cls: 'curation-none' }
  const accepted = stats?.accepted ?? 0
  const label = `${accepted}/${total}`
  if (accepted === 0) return { text: label, cls: 'curation-none' }
  if (accepted >= total) return { text: `${label} ✓`, cls: 'curation-done' }
  return { text: label, cls: 'curation-partial' }
}

async function deleteSource(citekey: string) {
  try {
    await library.remove(citekey)
    deleteConfirm.value = null
    await loadSources()
    // Library mutation invalidates the recommendations cache server-side;
    // refresh the curated list so the user sees the new state immediately.
    loadRecommendations()
  } catch {
    // silently fail
  }
}

async function processSource(citekey: string, force = false) {
  try {
    const resp = await process.submit(citekey, {
      projectId: projectStore.activeProjectId ?? undefined,
      force,
    })
    processingJobs.value[citekey] = resp.job_id
    pollJob(citekey, resp.job_id)
  } catch {
    // ignore
  }
}

async function pollJob(citekey: string, jobId: string) {
  const interval = setInterval(async () => {
    try {
      const resp = await process.jobStatus(jobId)
      if (resp.status === 'finished' || resp.status === 'failed') {
        clearInterval(interval)
        delete processingJobs.value[citekey]
        await loadSources()
        briefingDismissed.value = false
        loadBriefing()
        // Processing populates citation_graph + embeddings — refresh
        // recommendations so the user sees the wow list without a
        // manual page reload.
        loadRecommendations()
      }
    } catch {
      clearInterval(interval)
      delete processingJobs.value[citekey]
    }
  }, 3000)
}

async function handleUpload(files: FileList | null) {
  if (!files || files.length === 0) return
  uploadError.value = ''
  uploadSuccess.value = ''
  uploading.value = true

  let uploaded = 0
  let errors = 0
  const rejected: string[] = []
  const fileArray = Array.from(files)
  let lastUploadedCitekey: string | null = null
  let lastJobId: string | null = null
  for (const file of fileArray) {
    if (!file.name.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf') {
      errors++
      rejected.push(`${file.name} (${file.type || 'unknown type'})`)
      continue
    }
    try {
      const result = await library.upload(file, projectStore.activeProjectId ?? undefined)
      uploaded++
      lastUploadedCitekey = result.citekey
      if (result.already_owned) {
        uploadSuccess.value = `${file.name} — уже в библиотеке`
      } else if (result.deduplicated) {
        uploadSuccess.value = `${file.name} — загружен (обработка не требуется)`
      } else if (result.job_id) {
        lastJobId = result.job_id
        processingJobs.value[result.citekey] = result.job_id
        pollJob(result.citekey, result.job_id)
        uploadSuccess.value = `${file.name} — загружен, обработка запущена`
      }
    } catch (e) {
      errors++
      uploadError.value = e instanceof ApiError ? e.message : `Ошибка загрузки ${file.name}`
    }
  }

  if (uploaded > 0) {
    uploadSuccess.value = uploadSuccess.value || `Загружено: ${uploaded} файл(ов)`
    await loadSources()
    // Upload invalidates the recommendations cache; refresh immediately
    // so the 3+ sources threshold triggers the curated list.
    loadRecommendations()
    if (fileArray.length === 1 && lastUploadedCitekey && route.params.projectId) {
      // Pass job_id so SourceView can resume polling without a manual click
      const query = lastJobId ? { job_id: lastJobId } : {}
      router.push({ path: `/${route.params.projectId}/library/${lastUploadedCitekey}/review`, query })
    }
  }
  if (errors > 0 && !uploadError.value) {
    uploadError.value = rejected.length ? `Пропущено: ${rejected.join(', ')}` : `Ошибка загрузки: ${errors}`
  }
  uploading.value = false
  dragOver.value = false
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  handleUpload(e.dataTransfer?.files ?? null)
}

function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  handleUpload(input.files)
  input.value = ''
}


async function loadBriefing() {
  const pid = projectStore.activeProjectId
  if (!pid) return
  try {
    briefing.value = await analyze.briefing(pid)
  } catch {
    briefing.value = null
  }
}

async function loadAll() {
  await loadSources()
  loadGaps()
  loadRecommendations()
  loadCurationStats()
  loadBriefing()
}

onMounted(loadAll)
watch(() => projectStore.activeProjectId, () => {
  briefingDismissed.value = false
  loadAll()
})
watch(sources, () => { loadFragmentCounts() })
</script>

<template>
  <AppLayout>
    <!-- Upload zone -->
    <div class="animate-in">
      <div
        class="upload-zone"
        :class="{ 'upload-zone-hover': dragOver }"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop="onDrop"
      >
        <div v-if="uploading" class="flex items-center justify-center gap-2">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm text-[var(--color-ink-muted)]">Загрузка...</span>
        </div>
        <div v-else>
          <div class="upload-icon">&#128196;</div>
          <div class="upload-label">Загрузить PDF</div>
          <div class="upload-hint">
            Перетащите файл или
            <label class="cursor-pointer text-[var(--color-accent)] hover:text-[var(--color-accent-deep)]">
              нажмите для выбора
              <input type="file" accept=".pdf" multiple class="hidden" @change="onFileInput" />
            </label>
          </div>
        </div>
        <div v-if="uploadError" class="mt-3 text-sm text-[var(--color-err)]">{{ uploadError }}</div>
        <div v-if="uploadSuccess" class="mt-3 text-sm text-[var(--color-ok)]">{{ uploadSuccess }}</div>
      </div>
    </div>

    <!-- Briefing bar: compact one-line summary. Full section breakdown and
         coach findings live on /map — this is just a teaser. -->
    <div
      v-if="briefing && briefing.total_sources > 0 && !briefingDismissed"
      class="mt-5 animate-in animate-in-delay-1"
    >
      <div
        class="flex items-center justify-between gap-4 rounded-xl border border-[#b2dfdb] bg-[#e0f2f1] px-5 py-3"
      >
        <div class="text-sm text-[#004d40]">
          <span class="font-semibold">{{ briefing.total_sources }}&nbsp;{{ plural(briefing.total_sources, 'источник', 'источника', 'источников') }}</span>
          <span class="mx-2 text-[#80cbc4]">·</span>
          <span>{{ briefing.total_fragments }}&nbsp;{{ plural(briefing.total_fragments, 'фрагмент', 'фрагмента', 'фрагментов') }}</span>
          <span class="mx-2 text-[#80cbc4]">·</span>
          <span>покрытие {{ readySectionCount }}/{{ totalSectionCount }}</span>
        </div>
        <div class="flex items-center gap-4 flex-shrink-0">
          <RouterLink
            v-if="projectStore.activeProjectId"
            :to="`/${projectStore.activeProjectId}/map`"
            class="text-sm font-medium text-[#00695c] hover:text-[#004d40] no-underline"
          >
            подробнее &rarr; Карта
          </RouterLink>
          <button
            @click="briefingDismissed = true"
            class="text-[#80cbc4] hover:text-[#00695c] text-lg leading-none"
            aria-label="Скрыть"
          >&times;</button>
        </div>
      </div>
    </div>

    <!-- Sources table -->
    <div class="mt-5 animate-in animate-in-delay-2">
      <div v-if="loading" class="flex items-center justify-center py-16">
        <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
      </div>

      <!-- Empty state -->
      <div v-else-if="sources.length === 0" class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-8">
        <h3 class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)] mb-1">С чего начать?</h3>
        <p class="text-sm text-[var(--color-ink-muted)] mb-5">Три шага до первого результата:</p>
        <div class="space-y-4">
          <div class="flex items-start gap-3">
            <div class="w-7 h-7 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center text-[13px] font-bold flex-shrink-0">1</div>
            <div>
              <div class="text-[15px] font-medium text-[var(--color-ink)]">Загрузите PDF-статьи</div>
              <div class="text-sm text-[var(--color-ink-muted)] mt-0.5">Перетащите файлы в зону выше. Минимум 3 статьи для анализа покрытия.</div>
            </div>
          </div>
          <div class="flex items-start gap-3">
            <div class="w-7 h-7 rounded-full bg-[var(--color-rule)] text-[var(--color-ink-muted)] flex items-center justify-center text-[13px] font-bold flex-shrink-0">2</div>
            <div>
              <div class="text-[15px] font-medium text-[var(--color-ink-muted)]">Дождитесь обработки</div>
              <div class="text-sm text-[var(--color-ink-muted)] mt-0.5">Klemma извлечёт цитаты, аргументы и ключевые фрагменты из каждой статьи.</div>
            </div>
          </div>
          <div class="flex items-start gap-3">
            <div class="w-7 h-7 rounded-full bg-[var(--color-rule)] text-[var(--color-ink-muted)] flex items-center justify-center text-[13px] font-bold flex-shrink-0">3</div>
            <div>
              <div class="text-[15px] font-medium text-[var(--color-ink-muted)]">Отберите цитаты</div>
              <div class="text-sm text-[var(--color-ink-muted)] mt-0.5">Нажмите на источник и отберите полезные цитаты для вашей работы.</div>
            </div>
          </div>
        </div>
      </div>

      <template v-else>
        <!-- Outline hint -->
        <div
          v-if="!projectStore.activeOutline || projectStore.activeOutline.length === 0"
          class="mb-4 flex items-start gap-3 rounded-lg border border-amber-200 bg-[var(--color-warn-bg)] px-4 py-3"
        >
          <svg class="w-5 h-5 text-[var(--color-warn)] flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"/></svg>
          <div>
            <div class="text-sm font-medium text-[var(--color-ink)]">Нет структуры работы</div>
            <div class="text-sm text-[var(--color-ink-muted)] mt-0.5">Создайте новый проект с шаблоном структуры — Klemma распределит источники по разделам автоматически.</div>
          </div>
        </div>

        <div class="text-base font-semibold text-[var(--color-ink)] mb-2.5 flex items-center gap-2">
          Мои источники <span class="text-sm font-semibold text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] px-2 py-0.5 rounded-full">{{ sources.length }}</span>
        </div>
        <table class="source-table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Авторы</th>
              <th>Год</th>
              <th>Статус</th>
              <th>Цитаты</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="src in sources" :key="src.citekey">
              <td>
                <RouterLink
                  :to="`/${route.params.projectId}/library/${src.citekey}`"
                  class="text-[14px] font-medium text-[var(--color-ink)] no-underline hover:text-[var(--color-accent)]"
                >{{ src.title || src.citekey }}</RouterLink>
              </td>
              <td class="text-[13px] text-[var(--color-ink-muted)]" style="max-width: 140px">{{ shortAuthors(src.authors) }}</td>
              <td class="font-mono text-[14px]">{{ src.year || '—' }}</td>
              <td>
                <div class="flex items-center gap-2">
                  <span
                    class="status-badge"
                    :class="{
                      'status-completed': src.status === 'completed',
                      'status-pending': src.status === 'pending' || src.status === 'processing',
                      'status-failed': src.status === 'failed',
                    }"
                  >
                    {{ src.status === 'completed' ? 'готово' : src.status === 'pending' ? 'ожидает' : src.status === 'processing' ? 'обработка' : 'ошибка' }}
                  </span>
                  <button
                    v-if="(src.status === 'pending' || src.status === 'failed') && !processingJobs[src.citekey]"
                    @click.stop="processSource(src.citekey, false)"
                    class="text-sm text-[var(--color-accent)] hover:text-[var(--color-accent-deep)]"
                  >обработать</button>
                  <div
                    v-if="processingJobs[src.citekey]"
                    class="h-3 w-3 animate-spin rounded-full border border-[var(--color-accent)] border-t-transparent"
                  ></div>
                </div>
              </td>
              <td>
                <span
                  class="curation-badge"
                  :class="curationBadge(src.citekey).cls"
                >{{ curationBadge(src.citekey).text }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>

    <!-- LLM-curated recommendations (#332) -->
    <div
      v-if="recommendations.length > 0 && sources.length > 0"
      class="mt-8 animate-in animate-in-delay-3"
    >
      <div class="flex items-baseline justify-between mb-2.5">
        <div class="text-base font-semibold text-[var(--color-ink)] flex items-center gap-2">
          Рекомендуемая литература
          <span class="text-sm font-semibold text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] px-2 py-0.5 rounded-full">{{ recommendations.length }}</span>
        </div>
        <div class="text-[12px] text-[var(--color-ink-muted)]" v-if="recommendationsModel">
          Куратор: <code>{{ recommendationsModel }}</code>
          <span v-if="recommendationsGeneratedAt"> · {{ formatGeneratedAt(recommendationsGeneratedAt) }}</span>
        </div>
      </div>

      <div
        v-if="recommendationsDetail"
        class="mb-3 text-[13px] text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] border-l-2 border-[var(--color-accent)] px-3 py-2 rounded-r"
      >
        {{ recommendationsDetail }}
      </div>
      <div
        v-if="recommendationsWarning"
        class="mb-3 text-[13px] text-[var(--color-ink-muted)]"
      >
        {{ recommendationsWarning }}
      </div>

      <ol class="rec-list">
        <li v-for="(rec, i) in recommendations" :key="i" class="rec-item">
          <div class="rec-head">
            <span class="rec-rank">{{ i + 1 }}</span>
            <div class="rec-meta">
              <div class="rec-title">{{ rec.title }}</div>
              <div class="rec-authors">
                {{ shortAuthors(rec.authors || null) }}<span v-if="rec.year"> · {{ rec.year }}</span>
                <a
                  v-if="rec.doi"
                  :href="`https://doi.org/${rec.doi}`"
                  target="_blank"
                  rel="noopener"
                  class="rec-doi"
                >DOI</a>
              </div>
            </div>
            <span class="rec-score" :title="`Оценка релевантности: ${rec.score}/10`">
              {{ rec.score.toFixed(1) }}
            </span>
          </div>
          <p v-if="rec.rationale" class="rec-rationale">{{ rec.rationale }}</p>
        </li>
      </ol>
    </div>

    <!-- AI-недоступен / нет outline — показываем как info-блок вне списка -->
    <div
      v-else-if="(recommendationsDetail || recommendationsWarning) && sources.length >= 3"
      class="mt-8 text-[13px] text-[var(--color-ink-muted)]"
    >
      {{ recommendationsDetail || recommendationsWarning }}
    </div>

    <!-- Raw gap table: expert view / fallback -->
    <div v-if="gaps.length > 0 && sources.length > 0" class="mt-8 animate-in animate-in-delay-3">
      <div class="text-base font-semibold text-[var(--color-ink)] mb-2.5 flex items-center gap-2">
        Подробный анализ ссылок <span class="text-sm font-semibold text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] px-2 py-0.5 rounded-full">{{ gaps.length }}</span>
      </div>
      <table class="gaps-table">
        <thead>
          <tr>
            <th>Название</th>
            <th>Авторы</th>
            <th>Год</th>
            <th>DOI</th>
            <th>Роль</th>
            <th>Для разделов</th>
            <th style="text-align: right">Важность</th>
            <th style="text-align: center">Ссылок</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(gap, i) in gaps"
            :key="i"
            :class="{ 'gap-row-muted': gap.semantic_factor != null && gap.semantic_factor < 0.7 }"
          >
            <td><span class="gap-title">{{ gap.title }}</span></td>
            <td class="text-[13px] text-[var(--color-ink-muted)]">{{ shortAuthors(gap.authors) }}</td>
            <td class="font-mono text-[14px]">{{ gap.year || '—' }}</td>
            <td>
              <a
                v-if="gap.doi"
                :href="`https://doi.org/${gap.doi}`"
                target="_blank"
                rel="noopener"
                class="gap-doi"
                :title="gap.doi"
              >{{ gap.doi.length > 20 ? gap.doi.slice(0, 18) + '...' : gap.doi }}</a>
              <span v-else class="text-[var(--color-ink-muted)]">—</span>
            </td>
            <td>
              <div class="flex flex-wrap gap-1">
                <span
                  v-if="gap.top_intent"
                  class="intent-chip"
                  :class="intentColor(gap.top_intent)"
                  :title="gap.intents?.length ? 'Намерения: ' + gap.intents.join(', ') : gap.top_intent"
                >{{ intentLabel(gap.top_intent) }}</span>
                <span v-else class="text-[var(--color-ink-muted)]">—</span>
              </div>
            </td>
            <td>
              <div v-if="gap.sections_served && gap.sections_served.length" class="flex flex-wrap gap-1">
                <span
                  v-for="s in gap.sections_served.slice(0, 3)"
                  :key="s.section"
                  class="section-chip"
                  :title="gap.sections_served.map(x => x.section + ' (' + x.count + ')').join(', ')"
                >{{ s.section }} · {{ s.count }}</span>
                <span
                  v-if="gap.sections_served.length > 3"
                  class="text-[var(--color-ink-muted)] text-[12px]"
                  :title="gap.sections_served.slice(3).map(x => x.section + ' (' + x.count + ')').join(', ')"
                >+{{ gap.sections_served.length - 3 }}</span>
              </div>
              <span v-else class="text-[var(--color-ink-muted)]">—</span>
            </td>
            <td class="gap-score" :title="formatScoreTooltip(gap)">
              {{ formatScore(gap) }}
            </td>
            <td class="gap-refs">{{ gap.cited_by_count }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else-if="gapsDetail" class="mt-8">
      <p class="text-sm text-[var(--color-ink-muted)]">{{ gapsDetail }}</p>
    </div>
  </AppLayout>
</template>

<style scoped>
/* Upload zone */
.upload-zone {
  border: 2px dashed var(--color-rule);
  border-radius: 10px;
  padding: 24px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
}
.upload-zone:hover,
.upload-zone-hover {
  border-color: var(--color-accent);
  background: var(--color-accent-pale);
}
.upload-icon { font-size: 28px; margin-bottom: 8px; }
.upload-label { font-size: 16px; font-weight: 500; color: var(--color-ink-2); }
.upload-hint { font-size: 14px; color: var(--color-ink-muted); margin-top: 4px; }


/* Sources table */
.source-table { width: 100%; border-collapse: collapse; }
.source-table th {
  text-align: left;
  padding: 10px 12px;
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: var(--color-ink-muted);
  border-bottom: 1px solid var(--color-rule);
}
.source-table td {
  padding: 12px 12px;
  font-size: 14px;
  border-bottom: 1px solid var(--color-rule-light);
}
.source-table tr:hover td { background: var(--color-rule-light); }

.status-badge { font-size: 14px; padding: 3px 10px; border-radius: 4px; font-weight: 500; }
.status-completed { background: var(--color-ok-bg); color: var(--color-ok); }
.status-pending { background: var(--color-warn-bg); color: var(--color-warn); }
.status-failed { background: var(--color-err-bg); color: var(--color-err); }

.curation-badge { font-size: 14px; font-family: monospace; padding: 3px 10px; border-radius: 4px; }
.curation-done { background: var(--color-ok-bg); color: var(--color-ok); border: 1px solid #a7f3d0; }
.curation-partial { background: var(--color-warn-bg); color: var(--color-warn); border: 1px solid #fcd34d; }
.curation-none { background: var(--color-rule-light); color: var(--color-ink-muted); border: 1px solid var(--color-rule); }

/* LLM-curated recommendations */
.rec-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.rec-item {
  border: 1px solid var(--color-rule);
  border-radius: 10px;
  padding: 14px 16px;
  background: var(--color-surface, #fff);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.rec-item:hover {
  border-color: var(--color-accent);
  box-shadow: 0 1px 6px -3px var(--color-accent);
}
.rec-head { display: flex; align-items: baseline; gap: 12px; }
.rec-rank {
  flex: 0 0 auto;
  font-family: monospace;
  font-size: 13px;
  color: var(--color-ink-muted);
  min-width: 24px;
}
.rec-meta { flex: 1 1 auto; min-width: 0; }
.rec-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-ink);
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.rec-authors {
  font-size: 13px;
  color: var(--color-ink-muted);
  margin-top: 2px;
}
.rec-doi {
  margin-left: 8px;
  font-size: 12px;
  color: var(--color-accent);
  text-decoration: none;
}
.rec-doi:hover { text-decoration: underline; }
.rec-score {
  flex: 0 0 auto;
  font-family: monospace;
  font-size: 13px;
  color: var(--color-ink-muted);
  padding: 2px 8px;
  background: var(--color-rule-light);
  border-radius: 12px;
}
.rec-rationale {
  margin: 8px 0 0 36px;
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-ink-2);
}

/* Gaps table */
.gaps-table { width: 100%; border-collapse: collapse; }
.gaps-table th {
  text-align: left;
  padding: 10px 12px;
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: var(--color-ink-muted);
  border-bottom: 1px solid var(--color-rule);
}
.gaps-table td {
  padding: 12px 12px;
  font-size: 14px;
  border-bottom: 1px solid var(--color-rule-light);
}
.gaps-table tr:hover td { background: var(--color-rule-light); }
.gap-title { color: var(--color-ink); font-weight: 500; }
.gap-doi { font-size: 14px; color: var(--color-accent); text-decoration: none; }
.gap-doi:hover { text-decoration: underline; }
.gap-refs { font-family: monospace; font-size: 14px; color: var(--color-warn); text-align: center; }
.gap-score { font-family: monospace; font-size: 14px; color: var(--color-ink-muted); text-align: right; cursor: default; }

/* Row muting when semantic relevance is low */
.gap-row-muted td { opacity: 0.45; }

/* Intent chips — colour-coded citation role badges */
.intent-chip {
  display: inline-block;
  font-size: 12px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 4px;
  white-space: nowrap;
}
.intent-background { background: #f3f4f6; color: #6b7280; border: 1px solid #e5e7eb; }
.intent-method     { background: #ede9fe; color: #6d28d9; border: 1px solid #c4b5fd; }
.intent-result     { background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7; }
.intent-extends    { background: #e0f2fe; color: #0369a1; border: 1px solid #7dd3fc; }
.intent-contrasts  { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
.intent-data       { background: #fce7f3; color: #9d174d; border: 1px solid #f9a8d4; }
.intent-default    { background: var(--color-rule-light); color: var(--color-ink-muted); border: 1px solid var(--color-rule); }

/* Section chips — compact numeric section labels */
.section-chip {
  display: inline-block;
  font-size: 12px;
  font-family: monospace;
  padding: 2px 6px;
  border-radius: 3px;
  background: var(--color-rule-light);
  color: var(--color-ink-muted);
  border: 1px solid var(--color-rule);
  white-space: nowrap;
}
</style>
