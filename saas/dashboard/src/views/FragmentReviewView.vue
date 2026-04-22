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

// Map intent keys to CSS variable suffixes (tokens defined in main.css on one
// oklch family — same L/C, varying hue — so chips feel like one visual family).
const intentTokenKey: Record<string, string> = {
  background: 'bg',
  method: 'method',
  result_comparison: 'result',
  extends: 'ext',
  contrasts: 'contrast',
  uses_data: 'data',
}

function intentChipStyle(intent: string): Record<string, string> {
  const key = intentTokenKey[intent]
  if (!key) return {}
  return {
    background: `var(--color-intent-${key}-bg)`,
    color: `var(--color-intent-${key}-ink)`,
  }
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
// Pool = visible (not hidden) minus picked — the "awaiting decision" bucket.
const poolCount = computed(() =>
  allFragments.value.filter(f => !isPicked(f.fragment_id) && !isHidden(f.fragment_id)).length,
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
  return text.replace(/\[@([\w:.+\-]+)\]/g, (_m, key) => {
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

function composeNoteForSend(fragmentId: string, verdict: 'accepted' | 'rejected'): string | undefined {
  // notes[] always holds the user's clean note (never the [причина: …] marker).
  // The marker is only appended at send time when verdict === 'rejected' and
  // hideReasons has an entry — so an in-session undo doesn't leak the marker.
  const userNote = (notes.value[fragmentId] || '').replace(HIDE_REASON_RE, '').trim()
  const hr = hideReasons.value[fragmentId]
  if (verdict === 'rejected' && hr) {
    return userNote ? `${userNote}\n[причина: ${hr}]` : `[причина: ${hr}]`
  }
  return userNote || undefined
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
    note: composeNoteForSend(fragmentId, verdict),
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
  await setVerdict(fragmentId, 'rejected')
}

function onSuggestedInput(fragmentId: string, value: string) {
  suggestedTexts.value[fragmentId] = value
}

async function saveSuggested(fragmentId: string) {
  const sentence = (suggestedTexts.value[fragmentId] || '').trim()
  const model = sentenceModels.value[fragmentId]
  const hasRow = !!verdicts.value[fragmentId] || suggestedIds.value.has(fragmentId)
  if (hasRow) {
    // Persist even an empty string — lets the user clear an existing paraphrase.
    // Backend PATCH uses `is not None` so "" overwrites the stored text.
    await curation.update(projectId.value, fragmentId, {
      suggested_text: sentence,
      sentence_model: sentence && model ? model : undefined,
    })
    return
  }
  // No curation row yet. Empty input: nothing to persist.
  if (!sentence) return
  // Otherwise create a suggested row so the edit survives reload.
  const frag = allFragments.value.find(f => f.fragment_id === fragmentId)
  if (!frag) return
  await curation.curate(projectId.value, [{
    fragment_id: fragmentId,
    citekey: frag.citekey,
    verdict: 'suggested',
    suggested_text: sentence,
    sentence_model: sentence && model ? model : undefined,
  }])
  suggestedIds.value.add(fragmentId)
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
  <div class="fr-root">
    <div class="fr-content">

      <!-- Back link -->
      <router-link :to="`/${projectId}/library`" class="doc-back">&larr; Библиотека</router-link>

      <!-- Loading -->
      <div v-if="loading" class="fr-loading">
        <div class="fr-spinner"></div>
      </div>

      <template v-else>
        <!-- Header: title + metadata + overflow -->
        <div class="doc-head">
          <div class="doc-title-row">
            <div class="doc-title-block">
              <h1 class="doc-title">{{ sourceTitle || citekey }}</h1>
              <div class="doc-sub">
                <span>{{ sourceDisplay }}</span>
                <span class="dot">·</span>
                <span class="citekey-slug">{{ citekey }}</span>
                <template v-if="totalCount > 0">
                  <span class="dot">·</span>
                  <span>{{ totalCount }} фрагментов</span>
                </template>
              </div>
            </div>
            <div class="doc-overflow-wrap" id="source-overflow-menu">
              <button
                class="doc-overflow"
                title="Действия"
                @click.stop="menuOpen = !menuOpen"
              >⋯</button>
              <div v-if="menuOpen" class="doc-overflow-menu">
                <button
                  @click="deleteSource"
                  :disabled="deleting"
                  class="doc-overflow-item danger"
                >Удалить источник</button>
              </div>
            </div>
          </div>

          <!-- Tally: decision-state, not progress -->
          <div v-if="hasFragments" class="tally">
            <div class="tally-cell">
              <div class="tally-label">в работе</div>
              <div class="tally-value picked">{{ pickedCount
                }}<span class="tally-unit">&nbsp;{{ pluralizeCitations(pickedCount) }}</span></div>
            </div>
            <div class="tally-cell">
              <div class="tally-label">в пуле</div>
              <div class="tally-value">{{ poolCount
                }}<span class="tally-unit">&nbsp;на решении</span></div>
            </div>
            <div v-if="hiddenCount > 0" class="tally-cell">
              <div class="tally-label">скрыто</div>
              <div class="tally-value">{{ hiddenCount }}</div>
            </div>
            <div class="tally-cell tally-hint-cell">
              <div class="tally-hint">Решайте по сути, а не по числу — не все {{ totalCount }} должны попасть в работу.</div>
            </div>
          </div>
        </div>

        <!-- Source processing states -->
        <div v-if="isPending" class="fr-empty">
          <div class="fr-empty-icon">
            <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
            </svg>
          </div>
          <h3 class="fr-empty-title">Источник ещё не обработан</h3>
          <p class="fr-empty-sub">Нажмите «Обработать», чтобы извлечь фрагменты из PDF.</p>
          <button @click="startProcessing(false)" class="btn primary">Обработать</button>
        </div>

        <div v-if="isProcessing" class="fr-status">
          <div class="fr-spinner"></div>
          <span class="fr-status-text">Извлекаем фрагменты из PDF…</span>
          <span class="fr-status-sub">{{ jobStatus }}</span>
        </div>

        <div v-if="jobError" class="fr-error">
          <p>{{ jobError }}</p>
          <button @click="startProcessing(true)" class="fr-error-retry">Попробовать снова</button>
        </div>

        <div v-if="sourceStatus === 'failed' && !processing && !jobError" class="fr-error center">
          <p>Обработка завершилась с ошибкой</p>
          <button @click="startProcessing(true)" class="btn primary">Переобработать</button>
        </div>

        <div v-if="sourceStatus === 'completed' && totalCount === 0 && !loading" class="fr-empty">
          <p class="fr-empty-sub">Фрагменты не найдены.</p>
          <button @click="startProcessing(true)" class="fr-link">Переобработать</button>
        </div>

        <!-- Fragment pool -->
        <template v-if="hasFragments">

          <!-- Empty state: no sentences yet -->
          <template v-if="showEmptyState && firstFragment">
            <article class="frag">
              <div class="sentence-empty-head">
                <div class="sentence-empty-icon">✨</div>
                <div>
                  <h3 class="sentence-empty-title">Академические предложения ещё не сгенерированы</h3>
                  <p class="sentence-empty-sub">
                    Klemma создаст парафразы на русском для всех {{ totalCount }} фрагментов —
                    чтобы можно было сразу взять в работу самые ценные.
                  </p>
                </div>
              </div>
              <div class="sentence-empty-body">
                <div class="frag-orig">
                  <div class="frag-orig-header">
                    <span class="frag-orig-label">
                      Исходный фрагмент<template v-if="firstFragment.page"> · стр. {{ firstFragment.page }}</template>
                    </span>
                  </div>
                  <div class="frag-orig-text">«{{ firstFragment.text }}»</div>
                </div>
                <div class="frag-meta" style="margin-top: 14px;">
                  <div class="frag-meta-left">
                    <span class="chip intent-chip" :style="intentChipStyle(firstFragment.citation_intent)">
                      {{ intentLabel[firstFragment.citation_intent] || firstFragment.citation_intent }}
                    </span>
                    <span class="meta-aside">· предварительная классификация</span>
                  </div>
                </div>
              </div>
              <div class="sentence-empty-foot">
                <span>{{ totalCount }} фрагментов готовы к обработке</span>
                <button
                  :disabled="isGeneratingSentences"
                  @click="generateSentences('missing')"
                  class="btn primary sm"
                >✨ Сгенерировать предложения</button>
              </div>
            </article>
          </template>

          <!-- Active state: pool with picks -->
          <template v-else>
            <!-- Toolbar: intent filter chips + regenerate -->
            <div class="filters">
              <div class="filter-group">
                <button
                  class="chip filter-chip"
                  :class="{ active: activeFilter === 'all' }"
                  @click="activeFilter = 'all'"
                >Все<span class="count">{{ filterCount('all') }}</span></button>

                <button
                  v-for="filter in ['background', 'method', 'result_comparison', 'extends', 'contrasts', 'uses_data']"
                  :key="filter"
                  v-show="filterCount(filter) > 0"
                  class="chip intent-chip"
                  :class="{ active: activeFilter === filter }"
                  :style="intentChipStyle(filter)"
                  @click="activeFilter = filter === activeFilter ? 'all' : filter"
                >{{ intentLabel[filter] || filter }}<span class="count">{{ filterCount(filter) }}</span></button>

                <span v-if="hiddenCount > 0" class="filter-divider"></span>

                <button
                  v-if="hiddenCount > 0"
                  class="chip filter-chip"
                  :class="{ active: activeFilter === 'hidden' }"
                  @click="activeFilter = activeFilter === 'hidden' ? 'all' : 'hidden'"
                >Скрытые<span class="count">{{ hiddenCount }}</span></button>
              </div>

              <button
                v-if="allFragments.length > 0"
                :disabled="isGeneratingSentences"
                @click="generateSentences('force')"
                class="btn"
              >↻ Перегенерировать</button>
            </div>

            <div v-if="sentenceToast" class="fr-toast">{{ sentenceToast }}</div>

            <!-- Two-section grouping: ★ В работе / В пуле (or flat Hidden list) -->
            <template v-for="(group, gidx) in viewGroups" :key="group.key">
              <div v-if="gidx > 0" class="group-gap"></div>

              <div v-if="group.header === 'gold'" class="section-head">
                <span class="section-title picked">★ В работе</span>
                <span class="section-count">{{ group.count }} {{ pluralizeCitations(group.count) }}</span>
              </div>

              <div v-if="group.header === 'pool'" class="section-head">
                <span class="section-title">В пуле</span>
                <span class="section-count">{{ group.count }} {{ pluralizeCitations(group.count) }}</span>
                <span class="section-trailing">ещё не взяты</span>
              </div>

              <div class="stack">
                <article
                  v-for="f in group.fragments"
                  :key="f.fragment_id"
                  class="frag"
                  :class="{
                    picked: isPicked(f.fragment_id),
                    hidden: isHidden(f.fragment_id),
                  }"
                >
                  <!-- Overflow menu (not shown on hidden cards — they use "вернуть в пул" link) -->
                  <div
                    v-if="!isHidden(f.fragment_id)"
                    class="overflow-wrap"
                    data-card-menu-root
                  >
                    <button
                      @click="toggleCardMenu(f.fragment_id, $event)"
                      class="overflow-btn"
                      :class="{ open: openCardMenu === f.fragment_id }"
                      title="Ещё действия"
                    >⋯</button>

                    <div
                      v-if="openCardMenu === f.fragment_id"
                      class="overflow-menu"
                    >
                      <template v-if="isPicked(f.fragment_id)">
                        <button @click="undo(f.fragment_id)" class="overflow-item">
                          <span class="overflow-item-title">↶ Убрать из работы</span>
                          <span class="overflow-item-sub">вернуть в пул</span>
                        </button>
                      </template>

                      <div class="overflow-section-label">Скрыть из пула</div>
                      <button
                        @click="hideFragment(f.fragment_id, 'ошибка извлечения')"
                        class="overflow-item"
                      >
                        <span class="overflow-item-title">Ошибка извлечения</span>
                        <span class="overflow-item-sub">OCR-мусор, список литературы, формула</span>
                      </button>
                      <button
                        @click="hideFragment(f.fragment_id, 'нерелевантно')"
                        class="overflow-item"
                      >
                        <span class="overflow-item-title">Нерелевантно</span>
                        <span class="overflow-item-sub">не по теме диссертации</span>
                      </button>
                      <button
                        @click="hideFragment(f.fragment_id, 'другое')"
                        class="overflow-item"
                      >
                        <span class="overflow-item-title">Другое…</span>
                      </button>
                      <div class="overflow-divider"></div>
                      <div class="overflow-hint">Цитату можно вернуть через фильтр «Скрытые».</div>
                    </div>
                  </div>

                  <!-- Hidden card: compact, meta row + "вернуть в пул" on the right -->
                  <template v-if="isHidden(f.fragment_id)">
                    <div
                      v-if="(suggestedTexts[f.fragment_id] || '').trim()"
                      class="hidden-para"
                    >{{ suggestedTexts[f.fragment_id] }}</div>
                    <div v-else class="hidden-para serif-italic">«{{ f.text }}»</div>
                    <div class="frag-meta">
                      <div class="frag-meta-left">
                        <span class="chip intent-chip" :style="intentChipStyle(f.citation_intent)">
                          {{ intentLabel[f.citation_intent] || f.citation_intent }}
                        </span>
                        <span v-if="hideReasons[f.fragment_id]" class="meta-aside">скрыто · {{ hideReasons[f.fragment_id] }}</span>
                        <span v-else class="meta-aside">скрыто</span>
                      </div>
                      <div class="frag-meta-right">
                        <button class="note-link" @click="undo(f.fragment_id)">↶ вернуть в пул</button>
                      </div>
                    </div>
                  </template>

                  <!-- Active card: pool or picked -->
                  <template v-else>
                    <!-- Paraphrase (editable on click) -->
                    <div class="paraphrase">
                      <div
                        v-if="inProgressIds.has(f.fragment_id)"
                        class="paraphrase-loading"
                      >✨ Генерируем академическое предложение…</div>

                      <div
                        v-else-if="(suggestedTexts[f.fragment_id] || '').trim() && !editingSuggested[f.fragment_id]"
                        class="frag-para"
                        @click="startEditSuggested(f.fragment_id)"
                      >{{ formatParaphrase(suggestedTexts[f.fragment_id] || '') }}</div>

                      <div
                        v-else-if="!editingSuggested[f.fragment_id]"
                        class="paraphrase-placeholder"
                        @click="startEditSuggested(f.fragment_id)"
                      >{{ failedIds.has(f.fragment_id) ? 'Не удалось сгенерировать. Нажмите 🔄, чтобы повторить.' : 'Академическое предложение появится здесь после генерации.' }}</div>

                      <textarea
                        v-else
                        :data-edit-id="f.fragment_id"
                        class="paraphrase-edit"
                        :value="suggestedTexts[f.fragment_id] || ''"
                        @input="onSuggestedInput(f.fragment_id, ($event.target as HTMLTextAreaElement).value)"
                        @blur="finishEditSuggested(f.fragment_id)"
                      />

                      <button
                        v-if="failedIds.has(f.fragment_id) && !inProgressIds.has(f.fragment_id)"
                        class="retry-link"
                        @click="retryFragmentSentence(f.fragment_id)"
                      >🔄 повторить</button>
                      <div
                        v-if="sentenceModels[f.fragment_id] && !failedIds.has(f.fragment_id)"
                        class="model-chip"
                      ><span class="model-chip-icon">✨</span>{{ humanizeModel(sentenceModels[f.fragment_id] || '') }}</div>
                    </div>

                    <!-- Original + trust badge -->
                    <div class="frag-orig">
                      <div class="frag-orig-header">
                        <span class="frag-orig-label">
                          оригинал<template v-if="f.page"> · стр. {{ f.page }}</template>
                        </span>
                        <span
                          v-if="sentenceModels[f.fragment_id]"
                          class="trust-badge"
                          :title="`Парафраз сверен с оригиналом · ${humanizeModel(sentenceModels[f.fragment_id] || '')}`"
                        >✓ совпадает с источником · {{ humanizeModel(sentenceModels[f.fragment_id] || '') }}</span>
                      </div>
                      <div class="frag-orig-text">«{{ f.text }}»</div>
                    </div>

                    <!-- Meta row: intent + verbatim chip + (note/action) -->
                    <div class="frag-meta">
                      <div class="frag-meta-left">
                        <span class="chip intent-chip" :style="intentChipStyle(f.citation_intent)">
                          {{ intentLabel[f.citation_intent] || f.citation_intent }}
                        </span>
                        <span
                          v-if="f.verbatim"
                          class="chip type-chip"
                          title="Дословная цитата"
                        >Цитата</span>
                        <span
                          v-else
                          class="chip type-chip"
                          title="Парафраз — проверьте перед цитированием"
                        >Парафраз</span>
                      </div>
                      <div class="frag-meta-right">
                        <button
                          v-if="!openNotes[f.fragment_id]"
                          class="note-link"
                          @click="toggleNote(f.fragment_id)"
                        >+ заметка</button>
                        <button
                          v-if="!isPicked(f.fragment_id)"
                          class="btn primary sm"
                          @click="pickFragment(f.fragment_id)"
                        >+ В работу</button>
                      </div>
                    </div>

                    <!-- Note area -->
                    <div v-if="openNotes[f.fragment_id]" class="note-area">
                      <template v-if="notes[f.fragment_id] && !editingNote[f.fragment_id]">
                        <div class="note-display">
                          {{ notes[f.fragment_id] }}
                          <button class="note-edit-link" @click="startEditNote(f.fragment_id)">(изм.)</button>
                        </div>
                      </template>
                      <template v-else>
                        <textarea
                          class="note-input"
                          placeholder="Как использовать эту цитату…"
                          :value="notes[f.fragment_id] || ''"
                          @blur="saveNote(f.fragment_id, ($event.target as HTMLTextAreaElement).value)"
                        />
                      </template>
                    </div>
                  </template>
                </article>
              </div>
            </template>
          </template>

        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* ── Layout ──────────────────────────────────────────────────────────── */
.fr-root {
  flex: 1;
  width: 100%;
  overflow-y: auto;
  background: var(--color-paper);
  font-family: var(--font-ui);
  color: var(--color-ink);
  font-feature-settings: 'ss01', 'cv11';
  -webkit-font-smoothing: antialiased;
}
.fr-content {
  max-width: 960px;
  width: 100%;
  margin: 0 auto;
  padding: 40px 48px 120px;
}
@media (max-width: 1100px) {
  .fr-content { padding: 32px 32px 80px; }
}

/* ── Back link, loading, status ───────────────────────────────────── */
.doc-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--color-accent);
  margin-bottom: 16px;
  text-decoration: none;
}
.doc-back:hover { border-bottom: 1px solid var(--color-accent); padding-bottom: 1px; }

.fr-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 96px 0;
}
.fr-spinner {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid var(--color-accent);
  border-top-color: transparent;
  animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Document head ───────────────────────────────────────────────────── */
.doc-head { margin-bottom: 28px; }
.doc-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}
.doc-title-block { min-width: 0; flex: 1; }
.doc-title {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 34px;
  line-height: 1.15;
  letter-spacing: -0.02em;
  margin: 0 0 10px 0;
  max-width: 760px;
  color: var(--color-ink);
}
.doc-sub {
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  color: var(--color-ink-muted);
  font-family: var(--font-mono);
  flex-wrap: wrap;
}
.doc-sub .dot { color: var(--color-ink-faint); }
.citekey-slug { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }

.doc-overflow-wrap { position: relative; flex-shrink: 0; margin-top: 2px; }
.doc-overflow {
  width: 32px; height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-ink-muted);
  border-radius: 4px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}
.doc-overflow:hover { background: var(--color-paper-2); color: var(--color-ink); }
.doc-overflow-menu {
  position: absolute; right: 0; top: 36px;
  background: white;
  border: 1px solid var(--color-rule);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(30,27,24,.08);
  padding: 4px 0;
  min-width: 180px;
  z-index: 20;
}
.doc-overflow-item {
  width: 100%;
  text-align: left;
  padding: 8px 12px;
  font-size: 13px;
  background: transparent;
  border: none;
  cursor: pointer;
}
.doc-overflow-item.danger { color: var(--color-err); }
.doc-overflow-item.danger:hover { background: var(--color-err-bg); }
.doc-overflow-item:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── Tally: decision state (not progress) ────────────────────────────── */
.tally {
  margin-top: 22px;
  display: flex;
  align-items: stretch;
  gap: 0;
  border-top: 1px solid var(--color-rule);
  border-bottom: 1px solid var(--color-rule);
  padding: 14px 0;
}
.tally-cell {
  padding: 0 24px;
  border-right: 1px solid var(--color-rule);
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.tally-cell:first-child { padding-left: 0; }
.tally-cell:last-child { border-right: none; padding-right: 0; }
.tally-hint-cell {
  margin-left: auto;
  text-align: right;
  justify-content: center;
  border-right: none;
}
.tally-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--color-ink-faint);
}
.tally-value {
  font-family: var(--font-body-serif);
  font-size: 24px;
  font-weight: 500;
  color: var(--color-ink);
  letter-spacing: -0.01em;
  line-height: 1.1;
}
.tally-value.picked { color: var(--color-picked); }
.tally-unit {
  font-size: 12px;
  color: var(--color-ink-faint);
  font-weight: 400;
  font-family: var(--font-ui);
  margin-left: 2px;
  font-style: italic;
}
.tally-hint {
  font-size: 12px;
  color: var(--color-ink-muted);
  font-style: italic;
  max-width: 240px;
  line-height: 1.45;
}

/* ── Processing / empty states ───────────────────────────────────────── */
.fr-empty {
  border: 2px dashed var(--color-rule);
  border-radius: 12px;
  padding: 48px;
  text-align: center;
}
.fr-empty-icon {
  margin: 0 auto 16px;
  width: 48px; height: 48px;
  border-radius: 12px;
  background: var(--color-accent-tint);
  color: var(--color-accent);
  display: flex;
  align-items: center;
  justify-content: center;
}
.fr-empty-title { font-size: 18px; font-weight: 600; color: var(--color-ink); margin: 0; }
.fr-empty-sub { margin-top: 8px; font-size: 13px; color: var(--color-ink-muted); }

.fr-status {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--color-accent);
  background: var(--color-accent-tint);
  border-radius: 12px;
  padding: 18px 20px;
}
.fr-status-text { font-size: 13px; font-weight: 500; color: var(--color-accent-deep); }
.fr-status-sub { font-size: 13px; color: var(--color-ink-muted); }

.fr-error {
  margin-top: 12px;
  border: 1px solid var(--color-err);
  background: var(--color-err-bg);
  border-radius: 12px;
  padding: 16px;
  color: var(--color-err);
  font-size: 13px;
}
.fr-error.center { text-align: center; padding: 20px; }
.fr-error-retry {
  margin-top: 6px;
  font-size: 13px;
  color: var(--color-accent);
  border: none;
  background: transparent;
  cursor: pointer;
}
.fr-error-retry:hover { text-decoration: underline; }
.fr-link {
  margin-top: 10px;
  font-size: 13px;
  color: var(--color-accent);
  border: none;
  background: transparent;
  cursor: pointer;
}
.fr-link:hover { text-decoration: underline; }

/* ── Buttons ─────────────────────────────────────────────────────────── */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 4px;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  border: 1px solid var(--color-rule);
  background: white;
  color: var(--color-ink-light);
  cursor: pointer;
  line-height: 1.4;
}
.btn:hover { background: var(--color-paper-2); }
.btn.primary {
  background: var(--color-accent);
  color: white;
  border-color: var(--color-accent);
}
.btn.primary:hover { background: var(--color-accent-deep); }
.btn.primary:disabled { opacity: 0.6; cursor: not-allowed; }
.btn.sm { padding: 4px 10px; font-size: 12px; }

/* ── Filters ─────────────────────────────────────────────────────────── */
.filters {
  margin-top: 32px;
  margin-bottom: 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.filter-group {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.filter-divider {
  width: 1px;
  height: 20px;
  background: var(--color-rule);
  margin: 0 4px;
}

/* ── Chips ───────────────────────────────────────────────────────────── */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;
  white-space: nowrap;
  border: 1px solid transparent;
  background: transparent;
  cursor: default;
}
.chip .count {
  opacity: 0.55;
  font-weight: 400;
  margin-left: 2px;
}
.chip.filter-chip {
  background: var(--color-paper-2);
  color: var(--color-ink-light);
  cursor: pointer;
}
.chip.filter-chip:hover { background: var(--color-paper-3); }
.chip.filter-chip.active {
  background: white;
  color: var(--color-ink);
  border-color: var(--color-rule);
  box-shadow: 0 1px 2px rgba(30,27,24,.05);
}
.chip.intent-chip { cursor: pointer; }
.chip.intent-chip.active {
  box-shadow: 0 0 0 1.5px currentColor inset, 0 1px 2px rgba(30,27,24,.05);
}
button.chip {
  font-family: inherit;
  border: 1px solid transparent;
}
.chip.type-chip {
  background: transparent;
  color: var(--color-ink-muted);
  border: 1px solid var(--color-rule);
  border-radius: 4px;
  padding: 2px 8px;
  font-weight: 400;
}

/* ── Toast ───────────────────────────────────────────────────────────── */
.fr-toast {
  margin-bottom: 12px;
  border: 1px solid #fbbf24;
  background: #fef9c3;
  color: #78350f;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}

/* ── Section headers ─────────────────────────────────────────────────── */
.section-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin: 32px 0 14px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-rule);
}
.section-title {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--color-ink-muted);
  font-weight: 500;
}
.section-title.picked { color: var(--color-picked); }
.section-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-ink-faint);
}
.section-trailing {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-ink-muted);
}
.group-gap { height: 28px; }

/* ── Fragment cards ──────────────────────────────────────────────────── */
.stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.frag {
  background: white;
  border: 1px solid var(--color-rule);
  border-radius: 6px;
  padding: 20px 22px;
  position: relative;
  transition: box-shadow .15s ease, border-color .15s ease;
}
.frag:hover {
  border-color: var(--color-paper-3);
  box-shadow: 0 1px 2px rgba(30,27,24,.04);
}
/* Picked: 3px inset left bar + ochre tint, no frame — the bar IS the state */
.frag.picked {
  background: var(--color-picked-tint);
  border-color: transparent;
  box-shadow: inset 3px 0 0 var(--color-picked);
  padding-left: 24px;
}
.frag.picked:hover {
  box-shadow: inset 3px 0 0 var(--color-picked), 0 1px 2px rgba(30,27,24,.05);
}
.frag.hidden {
  opacity: 0.55;
  padding: 14px 22px;
  background: transparent;
  border-style: dashed;
}

/* Paraphrase (the primary text) */
.paraphrase { margin-bottom: 14px; margin-right: 36px; }
.frag-para {
  font-size: 15px;
  line-height: 1.62;
  color: var(--color-ink);
  text-wrap: pretty;
  cursor: text;
}
.paraphrase-loading {
  animation: pulse-soft 1.8s ease-in-out infinite;
  border-radius: 8px;
  background: var(--color-accent-tint);
  border: 1px solid var(--color-accent-rule);
  padding: 8px 12px;
  font-size: 13px;
  color: var(--color-accent-deep);
  font-style: italic;
}
.paraphrase-placeholder {
  font-size: 14px;
  line-height: 1.65;
  color: var(--color-ink-faint);
  font-style: italic;
  cursor: text;
}
.paraphrase-edit {
  width: 100%;
  border-radius: 6px;
  border: 1px solid var(--color-rule);
  background: white;
  padding: 8px 12px;
  font-size: 15px;
  font-family: inherit;
  line-height: 1.65;
  color: var(--color-ink);
  resize: vertical;
  min-height: 56px;
}
.paraphrase-edit:focus {
  outline: none;
  border-color: var(--color-accent);
}
.retry-link {
  margin-top: 4px;
  font-size: 13px;
  color: var(--color-err);
  border: none;
  background: transparent;
  cursor: pointer;
}
.retry-link:hover { text-decoration: underline; }
.model-chip {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-ink-faint);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
}
.model-chip-icon { font-size: 10px; opacity: 0.7; }

/* Original block: paper bg + left rule + mono label + trust badge + serif italic text */
.frag-orig {
  margin-top: 14px;
  padding: 12px 14px 13px;
  background: var(--color-paper);
  border-radius: 4px;
  border-left: 2px solid var(--color-rule);
}
.frag.picked .frag-orig {
  background: rgba(255,255,255,.5);
  border-left-color: var(--color-picked-rule);
}
.frag-orig-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  gap: 12px;
  flex-wrap: wrap;
}
.frag-orig-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-ink-faint);
}
.trust-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-accent);
  background: var(--color-accent-tint);
  padding: 2px 7px;
  border-radius: 3px;
  white-space: nowrap;
  cursor: help;
}
.frag-orig-text {
  font-family: var(--font-body-serif);
  font-size: 13px;
  font-style: italic;
  line-height: 1.58;
  color: var(--color-ink-light);
}

/* Meta row: intent + type + (note/action) */
.frag-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  gap: 12px;
  flex-wrap: wrap;
}
.frag-meta-left {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}
.frag-meta-right {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 13px;
  color: var(--color-ink-muted);
}
.meta-aside {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-ink-faint);
  margin-left: 4px;
}

/* Note link (inline action) */
.note-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--color-accent);
  font-size: 13px;
  padding: 2px 0;
  border: none;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
}
.note-link:hover {
  border-bottom: 1px solid var(--color-accent);
  padding-bottom: 1px;
  margin-bottom: -1px;
}

/* Note area */
.note-area { margin-top: 12px; }
.note-display {
  font-size: 13px;
  color: var(--color-ink-muted);
  font-style: italic;
  line-height: 1.6;
}
.note-edit-link {
  color: var(--color-accent);
  font-style: normal;
  cursor: pointer;
  border: none;
  background: transparent;
  margin-left: 4px;
  font-family: inherit;
  font-size: inherit;
}
.note-input {
  width: 100%;
  border: 1px solid var(--color-rule);
  border-radius: 4px;
  padding: 8px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
  min-height: 48px;
  color: var(--color-ink-light);
  background: white;
}
.note-input:focus {
  outline: none;
  border-color: var(--color-accent);
}

/* Hidden-card compact text */
.hidden-para {
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-ink-muted);
  margin-bottom: 8px;
  margin-right: 36px;
}
.hidden-para.serif-italic {
  font-family: var(--font-body-serif);
  font-style: italic;
}

/* Overflow menu on the card */
.overflow-wrap {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 10;
}
.overflow-btn {
  width: 28px; height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-ink-faint);
  background: transparent;
  border: none;
  border-radius: 4px;
  font-size: 15px;
  line-height: 1;
  letter-spacing: 2px;
  cursor: pointer;
}
.overflow-btn:hover,
.overflow-btn.open {
  background: var(--color-paper-2);
  color: var(--color-ink-light);
}
.overflow-menu {
  position: absolute;
  top: 34px;
  right: 0;
  background: white;
  border: 1px solid var(--color-rule);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(30,27,24,.08);
  min-width: 260px;
  z-index: 30;
  padding: 4px 0;
}
.overflow-section-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-ink-faint);
  padding: 10px 14px 6px;
}
.overflow-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 8px 14px;
  background: transparent;
  border: none;
  cursor: pointer;
}
.overflow-item:hover { background: var(--color-paper-2); }
.overflow-item-title {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-ink);
}
.overflow-item-sub {
  display: block;
  font-size: 12px;
  color: var(--color-ink-muted);
  margin-top: 2px;
}
.overflow-divider {
  height: 1px;
  background: var(--color-rule);
  margin: 4px 0;
}
.overflow-hint {
  font-size: 12px;
  color: var(--color-ink-muted);
  padding: 6px 14px 8px;
  line-height: 1.4;
}

/* ── Sentence-empty onboarding card ──────────────────────────────────── */
.sentence-empty-head {
  padding: 20px 22px 16px;
  border-bottom: 1px solid var(--color-rule-light);
  display: flex;
  gap: 12px;
  align-items: flex-start;
  margin: -20px -22px 0;
}
.sentence-empty-icon {
  width: 28px; height: 28px;
  border-radius: 6px;
  background: var(--color-accent-tint);
  color: var(--color-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 14px;
}
.sentence-empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-ink);
  margin: 0 0 4px 0;
}
.sentence-empty-sub {
  font-size: 13px;
  color: var(--color-ink-muted);
  line-height: 1.5;
  margin: 0;
}
.sentence-empty-body { padding: 16px 0 0; }
.sentence-empty-foot {
  margin: 16px -22px -20px;
  padding: 10px 22px;
  border-top: 1px solid var(--color-rule-light);
  background: var(--color-paper);
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  color: var(--color-ink-faint);
  border-radius: 0 0 6px 6px;
}

@keyframes pulse-soft {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
</style>
