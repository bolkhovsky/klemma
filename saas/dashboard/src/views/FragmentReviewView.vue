<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { curation, library, process, userProjects, type OutlineSection } from '../api/client'
import { humanizeModel } from '../utils/model'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.projectId as string)
const citekey = computed(() => route.params.citekey as string)

// Source info
const sourceTitle = ref('')
const sourceAuthors = ref('')
const sourceYear = ref<number | null>(null)
const sourceStatus = ref<string>('pending')

// Processing state
const processing = ref(false)
const jobId = ref<string | null>(null)
const jobStatus = ref('')
const jobError = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// Fragments
interface Fragment {
  fragment_id: string
  text: string
  citation_intent: string
  page: number | null
  citekey: string
  verbatim: boolean
  suggested_text?: string | null
  sentence_model?: string | null
}

const allFragments = ref<Fragment[]>([])
const totalCount = ref(0)
const loading = ref(true)
const deleting = ref(false)

// Verdicts & notes
// Backend keeps `accepted` / `rejected`; UI reads them as `picked` / `hidden`.
const verdicts = ref<Record<string, 'accepted' | 'rejected'>>({})
const suggestedIds = ref<Set<string>>(new Set())
const assignedSections = ref<Record<string, string>>({})
const notes = ref<Record<string, string>>({})
const editingNote = ref<Record<string, boolean>>({})
const openNotes = ref<Record<string, boolean>>({})

// Hide reasons — persisted by appending a `[причина: X]` suffix to the existing
// `note` field. No backend change needed; on load the suffix is stripped back
// into hideReasons so the user-written note stays clean in the UI.
const hideReasons = ref<Record<string, string>>({})
const HIDE_REASON_RE = /\s*\[причина:\s*([^\]]+)\]\s*$/

// Suggested sentences (editable per-fragment)
const suggestedTexts = ref<Record<string, string>>({})
const sentenceModels = ref<Record<string, string>>({})
const sentenceJobId = ref<string | null>(null)
const sentenceJobStatus = ref<string>('')
const inProgressIds = ref<Set<string>>(new Set())
const failedIds = ref<Set<string>>(new Set())
const sentenceToast = ref<string>('')
let sentencePollTimer: ReturnType<typeof setInterval> | null = null

// Inline paraphrase editing
const editingSuggested = ref<Record<string, boolean>>({})

// Header overflow menu
const menuOpen = ref(false)

// Per-card overflow menu (fragment_id of open menu, or null)
const openCardMenu = ref<string | null>(null)

// Outline sections — still loaded for future use (may drive writing-time hints), not shown in UI
const outline = ref<OutlineSection[]>([])

// Filter: 'all' | 'picked' | 'hidden' | intent-key
const activeFilter = ref('all')

const intentLabel: Record<string, string> = {
  background: 'Фон',
  method: 'Метод',
  result_comparison: 'Результат',
  extends: 'Расширение',
  contrasts: 'Контраст',
  uses_data: 'Данные',
}

const intentColor: Record<string, string> = {
  background: 'bg-[#dbeafe] text-[#1d4ed8]',
  method: 'bg-[#ede9fe] text-[#6d28d9]',
  result_comparison: 'bg-[#dcfce7] text-[#15803d]',
  extends: 'bg-[#ccfbf1] text-[#0f766e]',
  contrasts: 'bg-[#ffedd5] text-[#c2410c]',
  uses_data: 'bg-[#fef9c3] text-[#a16207]',
}

// Saturated (filled) variant for active filter chips — same hue as the card chip,
// but fully saturated so the active state is obvious at a glance.
const intentColorActive: Record<string, string> = {
  background: 'bg-[#1d4ed8] text-white border-[#1d4ed8]',
  method: 'bg-[#6d28d9] text-white border-[#6d28d9]',
  result_comparison: 'bg-[#15803d] text-white border-[#15803d]',
  extends: 'bg-[#0f766e] text-white border-[#0f766e]',
  contrasts: 'bg-[#c2410c] text-white border-[#c2410c]',
  uses_data: 'bg-[#a16207] text-white border-[#a16207]',
}

function filterChipClasses(filter: string): string {
  const active = activeFilter.value === filter
  if (active) return intentColorActive[filter] + ' border'
  // Inactive: match the card chip's bg+text, transparent border for layout parity
  const c = intentColor[filter]
  return c ? `${c} border border-transparent hover:opacity-75` : ''
}

function isPicked(id: string): boolean {
  return verdicts.value[id] === 'accepted'
}

function isHidden(id: string): boolean {
  return verdicts.value[id] === 'rejected'
}

const pickedCount = computed(() =>
  Object.values(verdicts.value).filter(v => v === 'accepted').length,
)
const hiddenCount = computed(() =>
  Object.values(verdicts.value).filter(v => v === 'rejected').length,
)

const visibleFragments = computed(() =>
  allFragments.value.filter(f => !isHidden(f.fragment_id)),
)

const filteredFragments = computed(() => {
  const af = activeFilter.value
  if (af === 'hidden') {
    return allFragments.value.filter(f => isHidden(f.fragment_id))
  }
  const pool = visibleFragments.value
  if (af === 'all') return pool
  return pool.filter(f => f.citation_intent === af)
})

// Split the filtered view into picked and pool buckets for two-section layout.
// Applies only when activeFilter !== 'hidden' (hidden has its own flat view).
const pickedInView = computed(() =>
  filteredFragments.value.filter(f => isPicked(f.fragment_id)),
)
const poolInView = computed(() =>
  filteredFragments.value.filter(f => !isPicked(f.fragment_id)),
)

type ViewGroup = {
  key: string
  header: 'gold' | 'pool' | null
  count: number
  fragments: Fragment[]
}

const viewGroups = computed<ViewGroup[]>(() => {
  if (activeFilter.value === 'hidden') {
    return [{
      key: 'hidden',
      header: null,
      count: filteredFragments.value.length,
      fragments: filteredFragments.value,
    }]
  }
  const groups: ViewGroup[] = []
  const picked = pickedInView.value
  const pool = poolInView.value
  if (picked.length > 0) {
    groups.push({ key: 'picked', header: 'gold', count: picked.length, fragments: picked })
  }
  if (pool.length > 0) {
    groups.push({
      key: 'pool',
      // No header when pool is the only group (nothing to group against)
      header: picked.length > 0 ? 'pool' : null,
      count: pool.length,
      fragments: pool,
    })
  }
  return groups
})

const intentCounts = computed(() => {
  const counts: Record<string, number> = {}
  for (const f of visibleFragments.value) {
    counts[f.citation_intent] = (counts[f.citation_intent] || 0) + 1
  }
  return counts
})

function filterCount(filter: string): number {
  if (filter === 'all') return visibleFragments.value.length
  if (filter === 'hidden') return hiddenCount.value
  return intentCounts.value[filter] || 0
}

function pluralizeCitations(n: number): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 'цитата'
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'цитаты'
  return 'цитат'
}

// Replace [@citekey] in paraphrase with readable "(Author, Year)" or "(key)" fallback.
// When citekey matches the current source, use author+year; otherwise keep the key itself.
function formatParaphrase(text: string): string {
  if (!text) return ''
  return text.replace(/\[@([\w\d_-]+)\]/g, (_m, key) => {
    if (key === citekey.value && sourceAuthors.value) {
      const a = sourceAuthors.value
      const short = a.includes(',') ? a.split(',')[0]!.trim() : a
      return sourceYear.value ? `(${short}, ${sourceYear.value})` : `(${short})`
    }
    return `(${key})`
  })
}

const sourceDisplay = computed(() => {
  const a = sourceAuthors.value
  const y = sourceYear.value
  if (!a) return citekey.value
  const short = a.includes(',') ? a.split(',')[0]!.trim() + ' et al.' : a
  return y ? `${short} (${y})` : short
})

const hasFragments = computed(() => sourceStatus.value === 'completed' && totalCount.value > 0)
const missingSentencesCount = computed(() =>
  allFragments.value.filter(f => !(suggestedTexts.value[f.fragment_id] || '').trim()).length,
)
const isGeneratingSentences = computed(() => sentenceJobId.value !== null)
const isPending = computed(() => sourceStatus.value === 'pending' && !processing.value)
const isProcessing = computed(() => processing.value || sourceStatus.value === 'processing')
const firstFragment = computed(() => allFragments.value[0] ?? null)

const showEmptyState = computed(() =>
  hasFragments.value &&
  missingSentencesCount.value === allFragments.value.length &&
  !isGeneratingSentences.value &&
  inProgressIds.value.size === 0,
)

function startEditSuggested(fragmentId: string) {
  editingSuggested.value[fragmentId] = true
  nextTick(() => {
    const el = document.querySelector(`[data-edit-id="${fragmentId}"]`) as HTMLTextAreaElement
    el?.focus()
  })
}

async function finishEditSuggested(fragmentId: string) {
  editingSuggested.value[fragmentId] = false
  await saveSuggested(fragmentId)
}

function toggleCardMenu(fragmentId: string, event: MouseEvent) {
  event.stopPropagation()
  openCardMenu.value = openCardMenu.value === fragmentId ? null : fragmentId
}

function handleClickOutside(e: MouseEvent) {
  const target = e.target as HTMLElement
  const sourceMenu = document.getElementById('source-overflow-menu')
  if (sourceMenu && !sourceMenu.contains(target)) {
    menuOpen.value = false
  }
  if (!target.closest('[data-card-menu-root]')) {
    openCardMenu.value = null
  }
}

async function loadData() {
  loading.value = true
  try {
    const src = await library.get(citekey.value)
    sourceTitle.value = src.title || ''
    sourceAuthors.value = src.authors || ''
    sourceYear.value = src.year
    sourceStatus.value = src.status || 'pending'

    const projList = await userProjects.list()
    const proj = projList.projects.find(p => p.project_id === projectId.value)
    if (proj?.outline) outline.value = proj.outline

    if (sourceStatus.value === 'completed') {
      const data = await curation.pending(projectId.value, citekey.value)
      allFragments.value = data.fragments
      totalCount.value = data.total

      for (const f of data.fragments) {
        if ((f as any).suggested_section && !assignedSections.value[f.fragment_id]) {
          assignedSections.value[f.fragment_id] = (f as any).suggested_section
        }
        if (f.suggested_text) {
          suggestedTexts.value[f.fragment_id] = f.suggested_text
        }
        if (f.sentence_model) {
          sentenceModels.value[f.fragment_id] = f.sentence_model
        }
      }

      const curated = await curation.curated(projectId.value, { citekey: citekey.value })
      for (const c of curated.fragments) {
        if (c.verdict === 'suggested') {
          if (c.assigned_section && !assignedSections.value[c.fragment_id]) {
            assignedSections.value[c.fragment_id] = c.assigned_section
          }
          suggestedIds.value.add(c.fragment_id)
        } else {
          verdicts.value[c.fragment_id] = c.verdict as 'accepted' | 'rejected'
        }
        if (c.assigned_section) assignedSections.value[c.fragment_id] = c.assigned_section
        if (c.note) {
          const m = c.note.match(HIDE_REASON_RE)
          if (m && c.verdict === 'rejected') {
            hideReasons.value[c.fragment_id] = m[1]!.trim()
            const clean = c.note.replace(HIDE_REASON_RE, '').trim()
            if (clean) {
              notes.value[c.fragment_id] = clean
              openNotes.value[c.fragment_id] = true
            }
          } else {
            notes.value[c.fragment_id] = c.note
            openNotes.value[c.fragment_id] = true
          }
        }
        if (c.suggested_text) {
          suggestedTexts.value[c.fragment_id] = c.suggested_text
        }
        if (c.sentence_model) {
          sentenceModels.value[c.fragment_id] = c.sentence_model
        }
        if (!allFragments.value.find(f => f.fragment_id === c.fragment_id)) {
          allFragments.value.push({
            fragment_id: c.fragment_id,
            text: c.text,
            citation_intent: c.citation_intent,
            page: null,
            citekey: c.citekey,
            verbatim: false,
            suggested_text: c.suggested_text,
            sentence_model: c.sentence_model,
          })
        }
      }
    }
  } catch (e) {
    console.error('Failed to load fragment review data', e)
  } finally {
    loading.value = false
  }
}

async function startProcessing(force = false) {
  processing.value = true
  jobError.value = ''
  jobStatus.value = 'queued'
  try {
    const resp = await process.submit(citekey.value, { projectId: projectId.value, force })
    jobId.value = resp.job_id
    startPolling()
  } catch (e: any) {
    jobError.value = e?.message || 'Ошибка запуска обработки'
    processing.value = false
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    if (!jobId.value) return
    try {
      const resp = await process.jobStatus(jobId.value)
      jobStatus.value = resp.status
      if (resp.status === 'finished') {
        stopPolling()
        processing.value = false
        if (resp.result?.status === 'error') {
          jobError.value = resp.result.detail || 'Обработка завершилась с ошибкой'
          sourceStatus.value = 'failed'
        } else {
          sourceStatus.value = 'completed'
          await loadData()
        }
      } else if (resp.status === 'failed') {
        stopPolling()
        processing.value = false
        jobError.value = resp.result?.detail || 'Обработка завершилась с ошибкой'
      }
    } catch {
      // keep polling on transient errors
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function setVerdict(fragmentId: string, verdict: 'accepted' | 'rejected') {
  verdicts.value[fragmentId] = verdict
  const frag = allFragments.value.find(f => f.fragment_id === fragmentId)
  if (!frag) return
  const sentence = (suggestedTexts.value[fragmentId] || '').trim()
  const model = sentenceModels.value[fragmentId]
  await curation.curate(projectId.value, [{
    fragment_id: fragmentId,
    citekey: frag.citekey,
    verdict,
    assigned_section: assignedSections.value[fragmentId] || undefined,
    note: notes.value[fragmentId] || undefined,
    suggested_text: sentence || undefined,
    sentence_model: sentence && model ? model : undefined,
  }])
}

async function pickFragment(fragmentId: string) {
  openCardMenu.value = null
  await setVerdict(fragmentId, 'accepted')
}

async function hideFragment(fragmentId: string, reason: string) {
  hideReasons.value[fragmentId] = reason
  openCardMenu.value = null
  // Pipe the reason into the `note` field so it survives reload via existing
  // curation plumbing. Preserve any user-written note that was already there.
  const existing = (notes.value[fragmentId] || '').replace(HIDE_REASON_RE, '').trim()
  notes.value[fragmentId] = existing
    ? `${existing}\n[причина: ${reason}]`
    : `[причина: ${reason}]`
  await setVerdict(fragmentId, 'rejected')
}

function onSuggestedInput(fragmentId: string, value: string) {
  suggestedTexts.value[fragmentId] = value
}

async function saveSuggested(fragmentId: string) {
  if (!verdicts.value[fragmentId] && !suggestedIds.value.has(fragmentId)) return
  const sentence = (suggestedTexts.value[fragmentId] || '').trim()
  const model = sentenceModels.value[fragmentId]
  await curation.update(projectId.value, fragmentId, {
    suggested_text: sentence,
    sentence_model: sentence && model ? model : undefined,
  })
}

async function generateSentences(mode: 'missing' | 'force') {
  if (sentenceJobId.value) return
  sentenceToast.value = ''
  failedIds.value = new Set()
  const targets = mode === 'force'
    ? allFragments.value.map(f => f.fragment_id)
    : allFragments.value.filter(f => !(suggestedTexts.value[f.fragment_id] || '').trim()).map(f => f.fragment_id)
  if (targets.length === 0) return
  inProgressIds.value = new Set(targets)
  try {
    const resp = await curation.generateSentences(projectId.value, citekey.value, mode)
    sentenceJobId.value = resp.job_id
    sentenceJobStatus.value = resp.status
    startSentencePolling()
  } catch (e: any) {
    inProgressIds.value = new Set()
    sentenceJobId.value = null
    sentenceToast.value = e?.message || 'Не удалось запустить генерацию'
  }
}

function startSentencePolling() {
  if (sentencePollTimer) clearInterval(sentencePollTimer)
  sentencePollTimer = setInterval(async () => {
    if (!sentenceJobId.value) return
    try {
      const resp = await process.jobStatus(sentenceJobId.value)
      sentenceJobStatus.value = resp.status
      if (resp.status === 'finished') {
        stopSentencePolling()
        const result = resp.result || {}
        if (result.status === 'error') {
          sentenceToast.value = result.detail || 'Ошибка генерации предложений'
        } else {
          const sentences: Record<string, string> = result.sentences || {}
          const model: string = result.model || ''
          for (const [fid, text] of Object.entries(sentences)) {
            suggestedTexts.value[fid] = text
            if (model) sentenceModels.value[fid] = model
            if (verdicts.value[fid] || suggestedIds.value.has(fid)) {
              curation.update(projectId.value, fid, {
                suggested_text: text,
                sentence_model: model || undefined,
              }).catch(() => {})
            }
          }
          const failed: string[] = result.failed_ids || []
          failedIds.value = new Set(failed)
          const generated = result.generated || 0
          const failedCount = result.failed || 0
          if (failedCount > 0) {
            sentenceToast.value = `Сгенерировано ${generated} из ${generated + failedCount}. Нажмите 🔄 на карточке, чтобы повторить.`
          }
        }
        inProgressIds.value = new Set()
        sentenceJobId.value = null
      } else if (resp.status === 'failed') {
        stopSentencePolling()
        sentenceToast.value = resp.result?.detail || 'Задача завершилась с ошибкой'
        inProgressIds.value = new Set()
        sentenceJobId.value = null
      }
    } catch {
      // keep polling on transient errors
    }
  }, 2000)
}

function stopSentencePolling() {
  if (sentencePollTimer) {
    clearInterval(sentencePollTimer)
    sentencePollTimer = null
  }
}

async function retryFragmentSentence(fragmentId: string) {
  suggestedTexts.value[fragmentId] = ''
  failedIds.value.delete(fragmentId)
  await generateSentences('missing')
}

async function undo(fragmentId: string) {
  delete verdicts.value[fragmentId]
  delete hideReasons.value[fragmentId]
  // Note: leave notes/openNotes intact so the user doesn't lose annotations when unpicking
}

function toggleNote(fragmentId: string) {
  openNotes.value[fragmentId] = !openNotes.value[fragmentId]
}

function startEditNote(fragmentId: string) {
  editingNote.value[fragmentId] = true
}

async function saveNote(fragmentId: string, text: string) {
  if (!text.trim()) return
  notes.value[fragmentId] = text.trim()
  editingNote.value[fragmentId] = false
  if (verdicts.value[fragmentId] || suggestedIds.value.has(fragmentId)) {
    await curation.update(projectId.value, fragmentId, { note: text.trim() })
  }
}

async function deleteSource() {
  menuOpen.value = false
  if (!confirm('Удалить источник из библиотеки? Это действие нельзя отменить.')) return
  deleting.value = true
  try {
    await library.remove(citekey.value)
    router.push(`/${projectId.value}/library`)
  } catch (e: any) {
    alert(e?.message || 'Ошибка удаления')
    deleting.value = false
  }
}

onMounted(() => {
  loadData()
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  stopPolling()
  stopSentencePolling()
  document.removeEventListener('click', handleClickOutside)
})
</script>

<template>
  <div class="w-full overflow-y-auto" style="background: var(--color-paper-bg, #faf9f7)">
    <div class="max-w-[780px] mx-auto py-6 px-5">

      <!-- Back link -->
      <div class="mb-4">
        <router-link :to="`/${projectId}/library`" class="text-sm text-[#0d7377] no-underline hover:underline">&larr; Библиотека</router-link>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex items-center justify-center py-24">
        <div class="h-5 w-5 animate-spin rounded-full border-2 border-[#0d7377] border-t-transparent"></div>
      </div>

      <template v-else>
        <!-- Header -->
        <div class="flex items-start justify-between gap-3 mb-5">
          <div class="flex-1 min-w-0">
            <h1 class="text-lg font-semibold tracking-tight mb-1 leading-snug">{{ sourceTitle || citekey }}</h1>
            <div class="text-sm text-[#6b6b8a]">
              {{ sourceDisplay }}<template v-if="totalCount > 0"> &middot; {{ totalCount }} фрагментов</template>
            </div>
          </div>
          <div class="flex items-center gap-1 shrink-0 mt-0.5">
            <div class="relative" id="source-overflow-menu">
              <button
                @click.stop="menuOpen = !menuOpen"
                class="w-7 h-7 rounded-md border border-transparent bg-transparent text-[#9ca3af] hover:bg-[#f0ede8] hover:text-[#3d3d5c] flex items-center justify-center cursor-pointer text-[16px] leading-none"
                title="Ещё"
              >⋯</button>
              <div
                v-if="menuOpen"
                class="absolute right-0 top-8 bg-white border border-[#e8e5df] rounded-lg shadow-lg py-1 min-w-[160px] z-20"
              >
                <button
                  @click="deleteSource"
                  :disabled="deleting"
                  class="w-full text-left px-3 py-2 text-[13px] text-[#c62828] hover:bg-[#fff0f0] cursor-pointer bg-transparent border-none disabled:opacity-50"
                >Удалить источник</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Source processing states (unchanged) -->
        <div v-if="isPending" class="rounded-xl border-2 border-dashed border-[#e8e5df] p-12 text-center">
          <div class="mx-auto w-12 h-12 rounded-xl bg-[#e6f3f3] flex items-center justify-center mb-4">
            <svg class="w-6 h-6 text-[#0d7377]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
            </svg>
          </div>
          <h3 class="text-lg font-semibold text-[#1a1a2e]">Источник ещё не обработан</h3>
          <p class="mt-2 text-sm text-[#6b6b8a]">Нажмите «Обработать», чтобы извлечь фрагменты из PDF.</p>
          <button
            @click="startProcessing(false)"
            class="mt-5 inline-flex items-center gap-2 rounded-lg bg-[#0d7377] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[#065a5e] transition-colors cursor-pointer border-none"
          >Обработать</button>
        </div>

        <div v-if="isProcessing" class="rounded-xl border border-[#0d7377] bg-[#e6f3f3] p-5">
          <div class="flex items-center gap-3">
            <div class="h-4 w-4 animate-spin rounded-full border-2 border-[#0d7377] border-t-transparent"></div>
            <span class="text-sm font-medium text-[#065a5e]">Извлекаем фрагменты из PDF...</span>
            <span class="text-[13px] text-[#6b6b8a]">{{ jobStatus }}</span>
          </div>
        </div>

        <div v-if="jobError" class="mt-3 rounded-xl border border-[#c62828] bg-[#fff0f0] p-4">
          <p class="text-sm text-[#c62828]">{{ jobError }}</p>
          <button
            @click="startProcessing(true)"
            class="mt-2 text-sm text-[#0d7377] cursor-pointer border-none bg-transparent hover:underline"
          >Попробовать снова</button>
        </div>

        <div v-if="sourceStatus === 'failed' && !processing && !jobError" class="rounded-xl border border-[#c62828] bg-[#fff0f0] p-5 text-center">
          <p class="text-sm text-[#c62828] mb-3">Обработка завершилась с ошибкой</p>
          <button
            @click="startProcessing(true)"
            class="inline-flex items-center gap-2 rounded-lg bg-[#0d7377] px-4 py-2 text-sm font-semibold text-white hover:bg-[#065a5e] transition-colors cursor-pointer border-none"
          >Переобработать</button>
        </div>

        <div v-if="sourceStatus === 'completed' && totalCount === 0 && !loading" class="rounded-xl border-2 border-dashed border-[#e8e5df] p-12 text-center">
          <p class="text-sm text-[#6b6b8a]">Фрагменты не найдены.</p>
          <button
            @click="startProcessing(true)"
            class="mt-3 text-sm text-[#0d7377] cursor-pointer border-none bg-transparent hover:underline"
          >Переобработать</button>
        </div>

        <!-- Fragment pool -->
        <template v-if="hasFragments">

          <!-- Empty state: no sentences yet -->
          <template v-if="showEmptyState && firstFragment">
            <article class="bg-white border border-[#e8e5df] rounded-[10px] overflow-hidden">
              <div class="px-[18px] pt-5 pb-4 border-b border-[#f0ede8] flex gap-3 items-start">
                <div class="w-7 h-7 rounded-md bg-[#e6f3f3] text-[#0d7377] flex items-center justify-center shrink-0 text-sm">✨</div>
                <div>
                  <h3 class="text-sm font-semibold text-[#1a1a2e] mb-1">Академические предложения ещё не сгенерированы</h3>
                  <p class="text-sm text-[#6b6b8a] leading-snug">
                    Klemma создаст парафразы на русском для всех {{ totalCount }} фрагментов —
                    чтобы можно было сразу взять в работу самые ценные.
                  </p>
                </div>
              </div>
              <div class="px-[18px] py-4">
                <div class="border-l-2 border-[#e8e5df] pl-3 py-1 mb-4">
                  <div class="text-[12px] uppercase tracking-[0.8px] text-[#9ca3af] font-semibold mb-1">
                    Исходный фрагмент<template v-if="firstFragment.page"> · стр. {{ firstFragment.page }}</template>
                  </div>
                  <div class="text-sm text-[#6b6b8a] leading-relaxed" style="font-family:'Georgia',serif">"{{ firstFragment.text }}"</div>
                </div>
                <div class="flex items-center gap-2">
                  <span
                    class="text-[12px] font-medium px-2 py-0.5 rounded"
                    :class="intentColor[firstFragment.citation_intent] || 'bg-gray-100 text-gray-600'"
                  >{{ intentLabel[firstFragment.citation_intent] || firstFragment.citation_intent }}</span>
                  <span class="text-[13px] text-[#9ca3af]">· предварительная классификация</span>
                </div>
              </div>
              <div class="px-[18px] py-[10px] border-t border-[#f0ede8] bg-[#fafaf8] rounded-b-[10px] flex items-center justify-between">
                <span class="text-[13px] text-[#9ca3af]">{{ totalCount }} фрагментов готовы к обработке</span>
                <button
                  :disabled="isGeneratingSentences"
                  @click="generateSentences('missing')"
                  class="inline-flex items-center gap-1.5 px-3 py-[5px] rounded-md text-[13px] font-medium cursor-pointer border-none bg-[#0d7377] text-white hover:bg-[#065a5e] disabled:opacity-60 disabled:cursor-not-allowed"
                >✨ Сгенерировать предложения</button>
              </div>
            </article>
          </template>

          <!-- Active state: pool with picks -->
          <template v-else>
            <!-- Toolbar: filters + regenerate -->
            <div class="flex items-center justify-between gap-3 mb-4 flex-wrap">
              <div class="flex gap-1.5 flex-wrap items-center">
                <button
                  class="px-3 py-1 rounded-md text-[13px] font-medium border cursor-pointer transition-all"
                  :class="activeFilter === 'all'
                    ? 'bg-[#1a1a2e] text-white border-[#1a1a2e]'
                    : 'bg-white text-[#6b6b8a] border-[#e8e5df] hover:border-[#d4d0ca] hover:text-[#1a1a2e]'"
                  @click="activeFilter = 'all'"
                >Все<span class="opacity-50 font-normal ml-1">{{ filterCount('all') }}</span></button>

                <button
                  v-for="filter in ['background', 'method', 'result_comparison', 'extends', 'contrasts', 'uses_data']"
                  :key="filter"
                  v-show="filterCount(filter) > 0"
                  class="px-3 py-1 rounded-md text-[13px] font-medium cursor-pointer transition-all"
                  :class="filterChipClasses(filter)"
                  @click="activeFilter = filter"
                >{{ intentLabel[filter] || filter }}<span class="opacity-60 font-normal ml-1">{{ filterCount(filter) }}</span></button>

                <button
                  v-if="hiddenCount > 0"
                  class="px-3 py-1 rounded-md text-[13px] font-medium border cursor-pointer transition-all ml-2"
                  :class="activeFilter === 'hidden'
                    ? 'bg-[#6b6b8a] text-white border-[#6b6b8a]'
                    : 'bg-white text-[#9ca3af] border-[#e8e5df] hover:border-[#d4d0ca] hover:text-[#6b6b8a]'"
                  @click="activeFilter = 'hidden'"
                >Скрытые<span class="opacity-60 font-normal ml-1">{{ hiddenCount }}</span></button>
              </div>

              <button
                v-if="allFragments.length > 0"
                :disabled="isGeneratingSentences"
                @click="generateSentences('force')"
                class="inline-flex items-center gap-1.5 px-3 py-[5px] rounded-md text-[13px] cursor-pointer border border-[#e8e5df] bg-transparent text-[#6b6b8a] hover:text-[#1a1a2e] hover:border-[#d4d0ca] disabled:opacity-50 disabled:cursor-not-allowed"
              >↻ Перегенерировать</button>
            </div>

            <div
              v-if="sentenceToast"
              class="mb-3 rounded-md border border-[#fbbf24] bg-[#fef9c3] px-3 py-2 text-[13px] text-[#78350f]"
            >{{ sentenceToast }}</div>

            <!-- Two-section grouping: ⭐ В работе  /  В пуле  (or flat Hidden list) -->
            <template v-for="(group, gidx) in viewGroups" :key="group.key">
              <!-- Gap between groups (not before the first) -->
              <div v-if="gidx > 0" class="h-7"></div>

              <!-- Group header: picked (thistle purple) -->
              <div
                v-if="group.header === 'gold'"
                class="flex items-baseline justify-between font-semibold text-[13px] mb-3 px-0.5 text-[#3b1f47]"
              >
                <span class="inline-flex items-center gap-2">
                  <span class="text-[#934eb1] text-[14px]">★</span>В работе
                </span>
                <span class="text-[13px] text-[#934eb1]/75 font-normal">{{ group.count }} {{ pluralizeCitations(group.count) }}</span>
              </div>

              <!-- Group header: pool -->
              <div
                v-if="group.header === 'pool'"
                class="flex items-baseline justify-between font-semibold text-[13px] mb-3 px-0.5 text-[#3d3d5c]"
              >
                <span>В пуле</span>
                <span class="text-[13px] text-[#9ca3af] font-normal">{{ group.count }} {{ pluralizeCitations(group.count) }} · ещё не взяты</span>
              </div>

              <!-- Cards -->
              <div
                v-for="f in group.fragments"
                :key="f.fragment_id"
              class="relative group bg-white border rounded-[10px] mb-3 overflow-visible transition-all"
              :class="{
                'border-[#be95d0] bg-[#f4edf7]': isPicked(f.fragment_id),
                'border-[#e8e5df] opacity-70': isHidden(f.fragment_id),
                'border-[#e8e5df] hover:border-[#d4d0ca]': !isPicked(f.fragment_id) && !isHidden(f.fragment_id),
              }"
            >
              <!-- Overflow menu trigger (hidden until hover unless picked/menu-open) -->
              <div
                v-if="!isHidden(f.fragment_id)"
                class="absolute top-2 right-2 z-10"
                data-card-menu-root
              >
                <button
                  @click="toggleCardMenu(f.fragment_id, $event)"
                  class="w-7 h-7 rounded-md border-none bg-transparent text-[#9ca3af] hover:bg-black/5 hover:text-[#1a1a2e] flex items-center justify-center cursor-pointer text-[16px] leading-none transition-all opacity-60 group-hover:opacity-100"
                  :class="{ '!opacity-100 bg-black/5 text-[#1a1a2e]': openCardMenu === f.fragment_id }"
                  title="Ещё действия"
                >⋯</button>

                <div
                  v-if="openCardMenu === f.fragment_id"
                  class="absolute top-[34px] right-0 bg-white border border-[#e8e5df] rounded-lg shadow-lg min-w-[260px] z-30 py-1"
                >
                  <div class="text-[12px] uppercase tracking-[0.8px] text-[#9ca3af] font-semibold px-[14px] pt-2.5 pb-1.5">Скрыть из пула</div>
                  <button
                    @click="hideFragment(f.fragment_id, 'ошибка извлечения')"
                    class="block w-full text-left px-[14px] py-2 bg-transparent border-none cursor-pointer hover:bg-[#f0ede8]"
                  >
                    <span class="block text-[13px] font-medium text-[#1a1a2e]">Ошибка извлечения</span>
                    <span class="block text-[12px] text-[#6b6b8a] mt-0.5">OCR-мусор, список литературы, формула</span>
                  </button>
                  <button
                    @click="hideFragment(f.fragment_id, 'нерелевантно')"
                    class="block w-full text-left px-[14px] py-2 bg-transparent border-none cursor-pointer hover:bg-[#f0ede8]"
                  >
                    <span class="block text-[13px] font-medium text-[#1a1a2e]">Нерелевантно</span>
                    <span class="block text-[12px] text-[#6b6b8a] mt-0.5">не по теме диссертации</span>
                  </button>
                  <button
                    @click="hideFragment(f.fragment_id, 'другое')"
                    class="block w-full text-left px-[14px] py-2 bg-transparent border-none cursor-pointer hover:bg-[#f0ede8]"
                  >
                    <span class="block text-[13px] font-medium text-[#1a1a2e]">Другое…</span>
                  </button>
                  <div class="h-px bg-[#e8e5df] my-1"></div>
                  <div class="text-[12px] text-[#6b6b8a] px-[14px] py-1.5 pb-2 leading-snug">Цитату можно вернуть через фильтр «Скрытые».</div>
                </div>
              </div>

              <!-- Hidden card: compact -->
              <template v-if="isHidden(f.fragment_id)">
                <div class="px-[18px] py-3">
                  <div
                    v-if="(suggestedTexts[f.fragment_id] || '').trim()"
                    class="text-[13px] leading-[1.6] text-[#6b6b8a] mb-2"
                  >{{ suggestedTexts[f.fragment_id] }}</div>
                  <div
                    v-else
                    class="text-[13px] leading-[1.6] text-[#6b6b8a] mb-2"
                    style="font-family:'Georgia',serif"
                  >"{{ f.text }}"</div>
                  <div class="flex items-center gap-1.5 flex-wrap">
                    <span
                      class="text-[12px] font-medium px-2 py-0.5 rounded"
                      :class="intentColor[f.citation_intent] || 'bg-gray-100 text-gray-600'"
                    >{{ intentLabel[f.citation_intent] || f.citation_intent }}</span>
                    <span v-if="f.page" class="text-[13px] text-[#9ca3af]">&middot; стр. {{ f.page }}</span>
                    <span v-if="hideReasons[f.fragment_id]" class="text-[13px] text-[#9ca3af]">&middot; {{ hideReasons[f.fragment_id] }}</span>
                  </div>
                </div>
                <div class="px-[18px] py-[10px] border-t border-[#f0ede8] bg-[#fafaf8] rounded-b-[10px] flex items-center justify-between">
                  <span class="text-[13px] text-[#9ca3af]">Скрыто</span>
                  <button
                    class="text-[13px] text-[#6b6b8a] cursor-pointer border-none bg-transparent px-2 py-1 rounded-md hover:bg-black/5 hover:text-[#1a1a2e]"
                    @click="undo(f.fragment_id)"
                  >↶ вернуть в пул</button>
                </div>
              </template>

              <!-- Active card: pool or picked -->
              <template v-else>
                <div class="pl-[18px] pr-11 py-4">
                  <!-- Paraphrase -->
                  <div class="mb-3">
                    <div
                      v-if="inProgressIds.has(f.fragment_id)"
                      class="animate-pulse rounded-lg bg-[#e6f3f3] border border-[#b8dcdc] px-3 py-2 text-[13px] text-[#065a5e] italic"
                    >✨ Генерируем академическое предложение…</div>

                    <div
                      v-else-if="(suggestedTexts[f.fragment_id] || '').trim() && !editingSuggested[f.fragment_id]"
                      class="text-[15px] leading-[1.65] text-[#1a1a2e] cursor-text select-text"
                      @click="startEditSuggested(f.fragment_id)"
                    >{{ formatParaphrase(suggestedTexts[f.fragment_id] || '') }}</div>

                    <div
                      v-else-if="!editingSuggested[f.fragment_id]"
                      class="text-[14px] leading-[1.65] text-[#9ca3af] italic cursor-text"
                      @click="startEditSuggested(f.fragment_id)"
                    >{{ failedIds.has(f.fragment_id) ? 'Не удалось сгенерировать. Нажмите 🔄, чтобы повторить.' : 'Академическое предложение появится здесь после генерации.' }}</div>

                    <textarea
                      v-else
                      :data-edit-id="f.fragment_id"
                      class="w-full rounded-lg border border-[#e8e5df] bg-white px-3 py-2 text-[15px] leading-[1.65] text-[#1a1a2e] resize-y min-h-[56px] focus:outline-none focus:border-[#0d7377]"
                      :value="suggestedTexts[f.fragment_id] || ''"
                      @input="onSuggestedInput(f.fragment_id, ($event.target as HTMLTextAreaElement).value)"
                      @blur="finishEditSuggested(f.fragment_id)"
                    />

                    <button
                      v-if="failedIds.has(f.fragment_id) && !inProgressIds.has(f.fragment_id)"
                      class="mt-1 text-[13px] text-[#c62828] cursor-pointer border-none bg-transparent hover:underline"
                      @click="retryFragmentSentence(f.fragment_id)"
                    >🔄 повторить</button>
                    <div
                      v-if="sentenceModels[f.fragment_id] && !failedIds.has(f.fragment_id)"
                      class="mt-1 text-[12px] text-[#9ca3af] inline-flex items-center gap-1 font-mono"
                    ><span class="text-[10px] opacity-70">✨</span>{{ humanizeModel(sentenceModels[f.fragment_id] || '') }}</div>
                  </div>

                  <!-- Original -->
                  <div
                    class="border-l-2 pl-3 py-1 mb-4"
                    :class="isPicked(f.fragment_id) ? 'border-[#be95d0]' : 'border-[#e8e5df]'"
                  >
                    <div class="text-[12px] uppercase tracking-[0.8px] text-[#9ca3af] font-semibold mb-1">
                      Оригинал<template v-if="f.page"> · стр. {{ f.page }}</template>
                    </div>
                    <div class="text-sm text-[#6b6b8a] leading-relaxed" style="font-family:'Georgia',serif">"{{ f.text }}"</div>
                  </div>

                  <!-- Meta row (intent + verbatim/paraphrase chip, note link) -->
                  <div class="flex items-center justify-between flex-wrap gap-2">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span
                        class="text-[12px] font-medium px-2 py-0.5 rounded"
                        :class="intentColor[f.citation_intent] || 'bg-gray-100 text-gray-600'"
                      >{{ intentLabel[f.citation_intent] || f.citation_intent }}</span>
                      <span
                        v-if="f.verbatim"
                        class="text-[12px] font-medium px-2 py-0.5 rounded bg-[#f0ede8] text-[#3d3d5c]"
                        title="Дословная цитата"
                      >Цитата</span>
                      <span
                        v-else
                        class="text-[12px] font-medium px-2 py-0.5 rounded bg-[#f0ede8] text-[#3d3d5c]"
                        title="Парафраз — проверьте перед цитированием"
                      >Парафраз</span>
                    </div>
                    <button
                      v-if="!openNotes[f.fragment_id]"
                      class="text-[13px] text-[#6b6b8a] cursor-pointer border-none bg-transparent p-0 hover:text-[#0d7377]"
                      @click="toggleNote(f.fragment_id)"
                    >✎ заметка</button>
                  </div>

                  <!-- Note area -->
                  <div v-if="openNotes[f.fragment_id]" class="mt-3">
                    <template v-if="notes[f.fragment_id] && !editingNote[f.fragment_id]">
                      <div class="text-[13px] text-[#6b6b8a] italic leading-6">
                        {{ notes[f.fragment_id] }}
                        <button class="text-[#0d7377] not-italic cursor-pointer ml-1 border-none bg-transparent" @click="startEditNote(f.fragment_id)">(изм.)</button>
                      </div>
                    </template>
                    <template v-else>
                      <textarea
                        class="w-full border border-[#e8e5df] rounded-md p-2 text-[13px] font-sans resize-y min-h-12 text-[#3d3d5c] focus:outline-none focus:border-[#0d7377]"
                        placeholder="Как использовать эту цитату..."
                        :value="notes[f.fragment_id] || ''"
                        @blur="saveNote(f.fragment_id, ($event.target as HTMLTextAreaElement).value)"
                      />
                    </template>
                  </div>
                </div>

                <!-- Card actions footer -->
                <div
                  class="px-[18px] py-[10px] border-t flex items-center justify-between rounded-b-[10px]"
                  :class="isPicked(f.fragment_id)
                    ? 'bg-[#e9dcef] border-[#a971c1]'
                    : 'bg-[#fafaf8] border-[#f0ede8]'"
                >
                  <template v-if="isPicked(f.fragment_id)">
                    <span class="text-[13px] font-bold text-[#3b1f47] flex items-center gap-1.5">
                      <span class="text-[#934eb1] text-[17px] leading-none">★</span>В работе
                    </span>
                    <button
                      class="text-[13px] text-[#6b6b8a] cursor-pointer border-none bg-transparent px-2 py-1 rounded-md hover:bg-black/5 hover:text-[#1a1a2e]"
                      @click="undo(f.fragment_id)"
                    >убрать</button>
                  </template>
                  <template v-else>
                    <span class="text-[13px] text-[#9ca3af]">В пуле</span>
                    <button
                      class="inline-flex items-center px-[10px] py-[5px] rounded-md text-[13px] font-medium cursor-pointer bg-transparent text-[#065a5e] border border-transparent hover:bg-[#e6f3f3] hover:border-[#0d7377]"
                      @click="pickFragment(f.fragment_id)"
                    >＋ В работу</button>
                  </template>
                </div>
              </template>
            </div>
          </template>

        </template>
      </template>
      </template>
    </div>
  </div>
</template>
