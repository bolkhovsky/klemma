<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  projects as apiProjects, drafts, write as writeApi,
  process as processApi, library as apiLibrary,
  type DraftFile, type DraftHeading,
} from '@/api/client'
import SourceDrawer from '@/components/SourceDrawer.vue'
import SourcePanel from '@/components/SourcePanel.vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.projectId as string)

// ── Load state ─────────────────────────────────────────────────────────────
const loading = ref(true)
const loadError = ref('')
const draftFiles = ref<DraftFile[]>([])
const sectionCounts = ref<Record<string, number>>({})  // section_id → source count
const totalSources = ref(0)

// ── Active chapter (sidebar nav) ────────────────────────────────────────────
const activeFile = ref<string>('')          // e.g. "chapter_1.md"
const activeSectionId = ref<string | null>(null)   // focused section card

// ── Per-section card state machine ─────────────────────────────────────────
type CardState = 'idle' | 'prompt' | 'generating' | 'diff' | 'accepted'
const cardStates = ref<Record<string, CardState>>({})
const cardPromptText = ref<Record<string, string>>({})
const cardSelectedPreset = ref<Record<string, string>>({})
const cardGenJobId = ref<Record<string, string>>({})
const cardGenResult = ref<Record<string, string>>({})   // generated text
const cardGenBefore = ref<Record<string, string>>({})   // original text before diff
let pollTimers: Record<string, ReturnType<typeof setInterval>> = {}

// ── Toast ───────────────────────────────────────────────────────────────────
const toastMsg = ref('')
const toastVisible = ref(false)
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(msg: string) {
  toastMsg.value = msg
  toastVisible.value = true
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastVisible.value = false }, 3000)
}

// ── Citation search (per section) ───────────────────────────────────────────
interface FragmentResult {
  fragment_id: string
  citekey: string
  title: string
  year: number | null
  text: string
  fragment_type: string
}
const citeSearchOpen = ref<Record<string, boolean>>({})
const citeSearchQuery = ref<Record<string, string>>({})
const citeSearchResults = ref<Record<string, FragmentResult[]>>({})
const citeSearchLoading = ref<Record<string, boolean>>({})
let citeSearchTimers: Record<string, ReturnType<typeof setTimeout>> = {}

const CITE_PRESETS = [
  'Запрос на автоматизацию',
  'Из A следует B',
  'Субъективность экспертов',
  'Точность метода',
]

function toggleCiteSearch(sectionId: string) {
  citeSearchOpen.value[sectionId] = !citeSearchOpen.value[sectionId]
}

function setCiteQuery(sectionId: string, q: string) {
  citeSearchQuery.value[sectionId] = q
  if (citeSearchTimers[sectionId]) clearTimeout(citeSearchTimers[sectionId])
  if (!q.trim() || q.trim().length < 2) { citeSearchResults.value[sectionId] = []; return }
  citeSearchTimers[sectionId] = setTimeout(() => runCiteSearch(sectionId, q), 400)
}

async function runCiteSearch(sectionId: string, q: string) {
  citeSearchLoading.value[sectionId] = true
  try {
    const data = await apiLibrary.fragmentSearch(q, 8)
    citeSearchResults.value[sectionId] = data.results
  } catch { citeSearchResults.value[sectionId] = [] }
  finally { citeSearchLoading.value[sectionId] = false }
}

async function attachFragmentSource(sectionId: string, citekey: string) {
  try {
    await apiProjects.assignSections(citekey, [sectionId])
    // Update local source count
    sectionCounts.value[sectionId] = (sectionCounts.value[sectionId] ?? 0) + 1
    showToast(`[@${citekey}] прикреплён к разделу`)
  } catch { showToast('Ошибка при прикреплении') }
}

// ── Computed chapter list ────────────────────────────────────────────────────
function fileDisplayName(name: string): string {
  const map: Record<string, string> = { 'intro.md': 'Введение', 'conclusion.md': 'Заключение' }
  if (map[name]) return map[name]
  const m = name.match(/chapter_(\d+)\.md/)
  if (m?.[1]) return `Глава ${m[1]}`
  return name.replace('.md', '')
}

function fileOrder(name: string): number {
  const id = name.replace('.md', '')
  if (id === 'abstract') return -1
  if (id === 'intro') return 0
  const m = id.match(/^chapter_(\d+)$/)
  if (m) return parseInt(m[1]!)
  if (id === 'appendix') return 95
  if (id === 'conclusion') return 99
  return 50
}

const sortedFiles = computed(() =>
  [...draftFiles.value]
    .filter(f => f.name !== 'dissertation.md')
    .sort((a, b) => fileOrder(a.name) - fileOrder(b.name))
)

const activeFileData = computed(() =>
  draftFiles.value.find(f => f.name === activeFile.value)
)

const activeSections = computed((): DraftHeading[] => {
  const headings = activeFileData.value?.headings ?? []
  const level3 = headings.filter(h => h.level === 3)
  // intro.md / conclusion.md use ## headings (level 2) for their ГОСТ sections
  if (level3.length > 0) return level3
  return headings.filter(h => h.level === 2)
})

const coveragePercent = computed(() => {
  const total = Object.keys(sectionCounts.value).length
  if (!total) return 0
  const covered = Object.values(sectionCounts.value).filter(c => c >= 5).length
  return Math.round((covered / total) * 100)
})

// ── Word count helpers ───────────────────────────────────────────────────────
function sectionWordCount(id: string): number {
  const text = currentSectionText(id)
  if (!text) return 0
  return text.trim().split(/\s+/).filter(Boolean).length
}

const totalWordCount = computed(() =>
  activeSections.value.reduce((sum, h) => sum + sectionWordCount(h.section_id), 0)
)

// ── Pin state (local, visual only) ──────────────────────────────────────────
const pinnedCitekeys = ref(new Set<string>())

function togglePin(citekey: string) {
  if (pinnedCitekeys.value.has(citekey)) {
    const next = new Set(pinnedCitekeys.value); next.delete(citekey); pinnedCitekeys.value = next
  } else {
    pinnedCitekeys.value = new Set([...pinnedCitekeys.value, citekey])
  }
}

// ── Add-source slide-in drawer ───────────────────────────────────────────────
const addSourceOpen = ref(false)

function onPanelAttach(citekey: string) {
  if (!panelSources.value.includes(citekey)) panelSources.value.push(citekey)
}

function onPanelDetach(citekey: string) {
  panelSources.value = panelSources.value.filter(k => k !== citekey)
}

async function detachFromPanel(citekey: string) {
  panelSources.value = panelSources.value.filter(k => k !== citekey)
  pinnedCitekeys.value = new Set([...pinnedCitekeys.value].filter(k => k !== citekey))
  const id = activeSectionId.value
  if (!id) return
  try { await apiProjects.detachSection(citekey, id) } catch { /* ignore */ }
}

function isUsedInText(citekey: string): boolean {
  const id = activeSectionId.value
  if (!id) return false
  return currentSectionText(id).includes(`[@${citekey}]`)
}

// ── Section card draft state ─────────────────────────────────────────────────
const MIN_SOURCES_WARN = 5

function sectionDraftState(id: string): 0 | 1 | 2 {
  const c = sectionCounts.value[id] ?? 0
  if (c === 0) return 0
  if (c < MIN_SOURCES_WARN) return 1
  return 2
}

function getDraftButton(id: string) {
  const st = sectionDraftState(id)
  if (st === 0) return { disabled: true, cls: 'opacity-40 cursor-not-allowed bg-[var(--color-ink-muted)]', label: 'Написать черновик' }
  if (st === 1) return { disabled: false, cls: 'bg-[var(--color-warn)] hover:bg-amber-700', label: 'Написать черновик (мало источников)' }
  return { disabled: false, cls: 'bg-[var(--color-accent)] hover:bg-[var(--color-accent-deep)]', label: 'Написать черновик' }
}

function getBadges(id: string): { label: string; cls: string }[] {
  const c = sectionCounts.value[id] ?? 0
  const wc = sectionWordCount(id)
  const state = cardStates.value[id] ?? 'idle'
  const badges: { label: string; cls: string }[] = []
  // Word count (only if has draft text)
  if (wc > 0) {
    badges.push({ label: `${wc} сл`, cls: 'text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200' })
  }
  // Source count
  if (c === 0) {
    if (wc === 0) badges.push({ label: 'нет черновика', cls: 'text-[var(--color-err)] bg-[var(--color-err-bg)] border border-red-200 font-semibold' })
    badges.push({ label: '0 фрагментов', cls: 'text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] border border-[var(--color-rule)]' })
  } else if (c < MIN_SOURCES_WARN) {
    badges.push({ label: `${c} источника`, cls: 'text-[var(--color-warn)] bg-[var(--color-warn-bg)] border border-amber-200 font-semibold' })
  } else {
    badges.push({ label: `${c} источников`, cls: 'text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200' })
  }
  if (state === 'generating') {
    badges.push({ label: 'генерация…', cls: 'text-[var(--color-warn)] bg-[var(--color-warn-bg)] border border-amber-200' })
  } else if (state === 'diff') {
    badges.push({ label: 'готово — просмотри', cls: 'text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200' })
  }
  return badges
}

// ── Draft generation ─────────────────────────────────────────────────────────
const PROMPT_PRESETS = [
  { key: 'academic', label: 'академический стиль' },
  { key: 'concise', label: 'краткий' },
  { key: 'detailed', label: 'детальный' },
  { key: 'critical', label: 'критический' },
]

function openPrompt(id: string) {
  if (sectionDraftState(id) === 0) return
  activeSectionId.value = id
  cardStates.value[id] = 'prompt'
  if (!cardSelectedPreset.value[id]) cardSelectedPreset.value[id] = 'academic'
  nextTick(() => {
    document.getElementById(`prompt-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })
}

function selectPreset(id: string, preset: string) {
  cardSelectedPreset.value[id] = preset
  const labels: Record<string, string> = {
    academic: 'Напиши академическим стилем, с пассивным залогом и ссылками на источники.',
    concise: 'Напиши кратко, только ключевые тезисы.',
    detailed: 'Напиши детально, раскрой каждый тезис.',
    critical: 'Напиши критически, указывая ограничения и противоречия.',
  }
  cardPromptText.value[id] = labels[preset] ?? ''
}

// ── File content (for diff "before") ─────────────────────────────────────────
const fileContent = ref<Record<string, string>>({})

watch(activeFile, async (name) => {
  if (name && !fileContent.value[name]) {
    try {
      const data = await drafts.get(projectId.value, name)
      fileContent.value[name] = data.content
    } catch { /* before will show "(пустой раздел)" */ }
  }
}, { immediate: true })

function currentSectionText(sectionId: string): string {
  const content = fileContent.value[activeFile.value]
  if (!content) return ''
  const lines = content.split('\n')
  const h = activeSections.value.find(x => x.section_id === sectionId)
  if (!h) return ''
  const nextH = activeSections.value.find(x => x.line > h.line)
  return lines.slice(h.line + 1, nextH?.line ?? lines.length).join('\n').trim()
}

async function runGenerate(sectionId: string) {
  const state = sectionDraftState(sectionId)
  if (state === 0) return

  cardStates.value[sectionId] = 'generating'
  cardGenBefore.value[sectionId] = currentSectionText(sectionId)

  try {
    const resp = await writeApi.draft(sectionId, projectId.value)
    cardGenJobId.value[sectionId] = resp.job_id
    startPoll(sectionId)
  } catch (e: any) {
    cardStates.value[sectionId] = 'idle'
    showToast(`Ошибка запуска: ${e.message}`)
  }
}

function startPoll(sectionId: string) {
  stopPoll(sectionId)
  pollTimers[sectionId] = setInterval(async () => {
    const jobId = cardGenJobId.value[sectionId]
    if (!jobId) return
    try {
      const resp = await processApi.jobStatus(jobId)
      if (resp.status === 'finished') {
        stopPoll(sectionId)
        const text: string = resp.result?.draft_text ?? resp.result?.text ?? ''
        if (resp.result?.status === 'error') {
          cardStates.value[sectionId] = 'idle'
          showToast(resp.result.detail || 'Ошибка генерации')
        } else {
          cardGenResult.value[sectionId] = text
          cardStates.value[sectionId] = 'diff'
        }
      } else if (resp.status === 'failed') {
        stopPoll(sectionId)
        cardStates.value[sectionId] = 'idle'
        showToast(resp.result?.detail || 'Генерация завершилась с ошибкой')
      }
    } catch { /* keep polling */ }
  }, 3000)
}

function stopPoll(sectionId: string) {
  if (pollTimers[sectionId]) { clearInterval(pollTimers[sectionId]); delete pollTimers[sectionId] }
}

async function acceptDraft(sectionId: string) {
  const text = cardGenResult.value[sectionId] ?? ''
  if (!text || !activeFile.value) return
  // Find the heading title
  const h = activeSections.value.find(h => h.section_id === sectionId)
  try {
    await drafts.upsertSection(projectId.value, activeFile.value, sectionId, text, h?.full_title)
    cardStates.value[sectionId] = 'accepted'
    showToast('✓ Черновик принят и сохранён')
  } catch (e: any) {
    showToast(`Ошибка сохранения: ${e.message}`)
  }
}

function rejectDraft(sectionId: string) {
  cardStates.value[sectionId] = 'idle'
  cardGenResult.value[sectionId] = ''
}

function editPrompt(sectionId: string) {
  cardStates.value[sectionId] = 'prompt'
}

// ── Citekey click → source drawer ────────────────────────────────────────────
const drawerCitekey = ref<string | null>(null)

function renderWithCitekeys(text: string): string {
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return escaped
    .replace(/\[@([\w\d_:.-]+)\]/g,
      '<span class="citekey-link" data-citekey="$1">[@$1]</span>')
    .replace(/\n/g, '<br>')
}

function handleCitekeyClick(e: MouseEvent) {
  const key = (e.target as HTMLElement).dataset?.citekey
  if (key) drawerCitekey.value = key
}

// ── Right panel: sources for active section ──────────────────────────────────
const panelSources = ref<string[]>([])
const panelLoading = ref(false)

watch(activeSectionId, async (id) => {
  panelSources.value = []
  if (!id) return
  panelLoading.value = true
  try {
    const r = await apiProjects.sectionSources(id)
    panelSources.value = r.citekeys
  } catch {
    panelSources.value = []
  } finally {
    panelLoading.value = false
  }
})

// ── Data loading ─────────────────────────────────────────────────────────────
async function loadAll() {
  loading.value = true
  loadError.value = ''
  try {
    const [filesData, coverageData] = await Promise.allSettled([
      drafts.list(projectId.value),
      apiProjects.coverage(),
    ])

    if (filesData.status === 'fulfilled') {
      draftFiles.value = filesData.value.files
    }
    if (coverageData.status === 'fulfilled') {
      sectionCounts.value = coverageData.value.sections
      totalSources.value = coverageData.value.total_sources
    }
  } catch (e: any) {
    loadError.value = e.message ?? 'Ошибка загрузки'
  } finally {
    loading.value = false
    if (!activeFile.value && draftFiles.value.length > 0) {
      const sortedF = [...draftFiles.value].sort((a, b) => fileOrder(a.name) - fileOrder(b.name))
      activeFile.value = sortedF[0]?.name ?? ''
    }
    await handleQuerySection()
  }
}

async function handleQuerySection() {
  const sec = route.query.section as string | undefined
  const file = route.query.file as string | undefined
  if (sec) {
    if (file && draftFiles.value.some(f => f.name === file)) {
      activeFile.value = file
    } else {
      // Infer file from section id
      const chapter = sec.split('.')[0]
      if (!chapter || chapter === 'intro' || chapter === '0') activeFile.value = 'intro.md'
      else if (chapter === 'conclusion') activeFile.value = 'conclusion.md'
      else activeFile.value = `chapter_${chapter}.md`
    }
    activeSectionId.value = sec
    await nextTick()
    const el = document.getElementById(`section-${sec}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

onMounted(loadAll)

// Re-scroll when navigating to a different ?section= without unmounting
watch(() => route.query.section, async (sec) => {
  if (sec) await handleQuerySection()
})

onUnmounted(() => {
  Object.keys(pollTimers).forEach(stopPoll)
  if (toastTimer) clearTimeout(toastTimer)
})

</script>

<template>
  <div class="flex flex-1 overflow-hidden">

      <!-- ── Main content ───────────────────────────────────────────────── -->
      <main class="flex-1 overflow-y-auto px-6 py-6">

        <!-- Loading -->
        <div v-if="loading" class="flex items-center justify-center h-40 text-[var(--color-ink-muted)]">
          <svg class="animate-spin w-5 h-5 mr-2" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-dasharray="31.4" stroke-dashoffset="10"/>
          </svg>
          Загрузка…
        </div>

        <!-- Error -->
        <div v-else-if="loadError" class="text-[var(--color-err)] text-sm p-4 bg-[var(--color-err-bg)] rounded-lg">
          {{ loadError }}
        </div>

        <!-- No files -->
        <div v-else-if="draftFiles.length === 0" class="text-center py-12 text-[var(--color-ink-muted)]">
          <div class="text-3xl mb-3 opacity-30">📄</div>
          <p class="text-sm mb-1">Нет черновиков</p>
          <p class="text-xs">Используйте <code class="font-mono bg-[var(--color-rule-light)] px-1 rounded">klemma push</code> для загрузки черновиков</p>
        </div>

        <!-- Chapter heading -->
        <template v-else-if="activeFileData">
          <div class="mb-5">
            <h1 class="font-display text-[18px] font-semibold tracking-tight text-[var(--color-ink)]">
              {{ fileDisplayName(activeFile) }}
            </h1>
            <p class="text-[13px] font-mono text-[var(--color-ink-muted)] mt-1">
              {{ activeFile }} · {{ activeFileData.word_count }} слов · {{ activeSections.length }} разделов
            </p>
          </div>

          <!-- No sections in file -->
          <div v-if="activeSections.length === 0" class="text-center py-8 text-[var(--color-ink-muted)] text-sm">
            Нет подразделов в этом файле. Добавьте заголовки <code class="font-mono bg-[var(--color-rule-light)] px-1 rounded">##</code> или <code class="font-mono bg-[var(--color-rule-light)] px-1 rounded">###</code> в черновик.
          </div>

          <!-- Section cards — always expanded, prototype style -->
          <div
            v-for="heading in activeSections"
            :key="heading.section_id"
            :id="`section-${heading.section_id}`"
            :class="[
              'bg-white border rounded-[10px] mb-3.5 overflow-hidden transition-all',
              activeSectionId === heading.section_id
                ? 'border-[var(--color-accent)] shadow-[0_0_0_3px_rgba(13,115,119,0.08)]'
                : cardStates[heading.section_id] === 'diff'
                  ? 'border-[var(--color-ok)] shadow-[0_0_0_3px_rgba(45,106,79,0.08)]'
                  : cardStates[heading.section_id] === 'generating'
                    ? 'border-amber-400 shadow-[0_0_0_3px_rgba(180,83,9,0.07)]'
                    : 'border-[var(--color-rule)] hover:border-[#d4d0ca]'
            ]"
          >
            <!-- Card header — click to focus section in right panel -->
            <div
              @click="activeSectionId = activeSectionId === heading.section_id ? null : heading.section_id"
              class="flex items-center gap-2.5 px-4 py-3 cursor-pointer border-b border-[var(--color-rule-light)] hover:bg-[var(--color-rule-light)] transition-colors"
            >
              <span class="text-[11px] font-mono font-semibold text-[var(--color-accent)] bg-[var(--color-accent-pale)] rounded px-1.5 py-0.5 flex-shrink-0">
                {{ heading.section_id }}
              </span>
              <span class="text-[14px] font-medium text-[var(--color-ink)] flex-1">
                {{ heading.full_title.replace(/^[\d.]+\s*/, '') }}
              </span>
              <div class="flex gap-1.5 flex-shrink-0">
                <span
                  v-for="badge in getBadges(heading.section_id)"
                  :key="badge.label"
                  :class="['text-[11px] font-mono rounded px-1.5 py-0.5', badge.cls]"
                >{{ badge.label }}</span>
              </div>
            </div>

            <!-- Card body — always visible -->

            <!-- STATE: generating -->
            <div v-if="cardStates[heading.section_id] === 'generating'" class="flex flex-col items-center py-7 gap-3">
              <div class="w-7 h-7 rounded-full border-[2.5px] border-[var(--color-rule)] border-t-amber-500 animate-spin" />
              <p class="text-[13px] text-[var(--color-ink-muted)]">Генерация черновика…</p>
              <p class="text-[11px] font-mono text-[var(--color-ink-muted)] opacity-60">{{ heading.section_id }}</p>
            </div>

            <!-- STATE: diff -->
            <div v-else-if="cardStates[heading.section_id] === 'diff'">
              <div class="flex items-center gap-2 px-4 py-2 bg-[var(--color-ok-bg)] border-b border-green-200 text-[12px] text-[var(--color-ok)]">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                Klemma предлагает правку
                <span class="flex-1 text-[11px] text-[var(--color-ink-muted)] italic ml-1">
                  «{{ cardPromptText[heading.section_id] || cardSelectedPreset[heading.section_id] || '' }}»
                </span>
              </div>
              <div class="grid grid-cols-2 divide-x divide-[var(--color-rule-light)]">
                <div class="px-4 py-3 bg-[#fff8f8]">
                  <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-err)] mb-2">Было</div>
                  <p class="text-[13px] text-[var(--color-ink-muted)] leading-relaxed italic">
                    {{ cardGenBefore[heading.section_id] || '(пустой раздел)' }}
                  </p>
                </div>
                <div class="px-4 py-3 bg-[#f8fff9]">
                  <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ok)] mb-2">Станет</div>
                  <div
                    v-html="renderWithCitekeys(cardGenResult[heading.section_id] ?? '')"
                    @click="handleCitekeyClick($event)"
                    class="text-[13px] text-[var(--color-ink)] leading-relaxed [&_.citekey-link]:text-[var(--color-accent)] [&_.citekey-link]:underline [&_.citekey-link]:decoration-dotted [&_.citekey-link]:cursor-pointer [&_.citekey-link:hover]:no-underline"
                  />
                </div>
              </div>
              <div class="flex items-center gap-2 px-4 py-2.5 border-t border-[var(--color-rule-light)] bg-white">
                <button @click="acceptDraft(heading.section_id)"
                  class="inline-flex items-center gap-1.5 bg-[var(--color-ok)] text-white border-none rounded-md px-3.5 py-1.5 text-[13px] font-medium cursor-pointer hover:bg-green-700 transition-colors">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  Принять
                </button>
                <button @click="rejectDraft(heading.section_id)"
                  class="inline-flex items-center gap-1.5 bg-transparent text-[var(--color-err)] border border-red-200 rounded-md px-3.5 py-1.5 text-[13px] cursor-pointer hover:bg-[var(--color-err-bg)] transition-colors">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  Отклонить
                </button>
                <button @click="editPrompt(heading.section_id)"
                  class="inline-flex items-center gap-1.5 bg-transparent text-[var(--color-ink-muted)] border border-[var(--color-rule)] rounded-md px-3 py-1.5 text-[13px] cursor-pointer hover:bg-[var(--color-rule-light)] transition-colors">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                  Изменить запрос
                </button>
              </div>
            </div>

            <!-- STATE: idle / prompt / accepted -->
            <div v-else class="sec-card-body">

              <!-- Accepted note -->
              <p v-if="cardStates[heading.section_id] === 'accepted'" class="text-[11px] text-[var(--color-ok)] bg-[var(--color-ok-bg)] border-b border-green-200 px-4 py-2 flex items-center gap-1.5">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                Черновик сохранён
              </p>

              <!-- Prose content (when draft text exists) -->
              <div
                v-if="currentSectionText(heading.section_id)"
                class="px-4 pt-4 pb-3"
              >
                <div
                  v-html="renderWithCitekeys(currentSectionText(heading.section_id))"
                  @click="handleCitekeyClick($event)"
                  class="text-[14px] leading-[1.75] text-[var(--color-ink-2,#3d3d5c)] [&_.citekey-link]:font-mono [&_.citekey-link]:text-[13px] [&_.citekey-link]:text-[var(--color-accent)] [&_.citekey-link]:bg-[var(--color-accent-pale)] [&_.citekey-link]:rounded [&_.citekey-link]:px-1 [&_.citekey-link]:cursor-pointer [&_.citekey-link:hover]:underline"
                />
              </div>

              <!-- Empty state (no draft text) -->
              <div v-else class="px-4 py-5 text-center">
                <div v-if="sectionDraftState(heading.section_id) === 0">
                  <p class="text-[13px] text-[var(--color-ink-muted)] mb-1">Нет источников для этого раздела</p>
                  <p class="text-[11px] text-[var(--color-ink-muted)] opacity-70 mb-4">Используйте поиск цитат ниже, чтобы найти и прикрепить источники</p>
                  <button disabled class="inline-flex items-center gap-1.5 opacity-40 cursor-not-allowed bg-[var(--color-ink-muted)] text-white rounded-lg px-4 py-2 text-[13px] font-medium mb-3">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                    Написать черновик
                  </button>
                  <p class="text-[11px] text-[var(--color-err)] bg-[var(--color-err-bg)] border border-red-200 rounded-lg px-3 py-1.5 inline-flex items-center gap-1.5">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>
                    Нет источников — черновик недоступен
                  </p>
                </div>
                <div v-else>
                  <p v-if="sectionDraftState(heading.section_id) === 1" class="text-[11px] text-amber-700 bg-[var(--color-warn-bg)] border border-amber-200 rounded-lg px-3 py-1.5 mb-3 inline-flex items-center gap-1.5">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/></svg>
                    Мало источников ({{ sectionCounts[heading.section_id] ?? 0 }}/{{ MIN_SOURCES_WARN }}) — черновик может быть поверхностным
                  </p>
                  <p v-else class="text-[11px] text-[var(--color-ink-muted)] mb-3">Черновик ещё не написан</p>
                  <button
                    v-if="cardStates[heading.section_id] !== 'prompt'"
                    @click="openPrompt(heading.section_id)"
                    :class="[
                      'inline-flex items-center gap-1.5 text-white border-none rounded-lg px-4 py-2 text-[13px] font-medium cursor-pointer transition-colors',
                      getDraftButton(heading.section_id).cls
                    ]"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                    {{ getDraftButton(heading.section_id).label }}
                  </button>
                </div>
              </div>

              <!-- Prompt box (appears when state=prompt) -->
              <div v-if="cardStates[heading.section_id] === 'prompt'" :id="`prompt-${heading.section_id}`" class="mx-4 mb-3 bg-[#fafaf9] border border-[var(--color-rule)] rounded-lg px-3.5 py-3">
                <label class="text-[11px] font-semibold text-[var(--color-ink-muted)] uppercase tracking-[0.5px] block mb-2">Запрос к черновику</label>
                <div class="flex flex-wrap gap-1.5 mb-2.5">
                  <button
                    v-for="preset in PROMPT_PRESETS"
                    :key="preset.key"
                    @click="selectPreset(heading.section_id, preset.key)"
                    :class="[
                      'text-[11px] px-2.5 py-1 rounded border transition-all',
                      cardSelectedPreset[heading.section_id] === preset.key
                        ? 'border-[var(--color-accent)] text-[var(--color-accent)] bg-[var(--color-accent-pale)]'
                        : 'border-[var(--color-rule)] text-[var(--color-ink-muted)] bg-white hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]'
                    ]"
                  >{{ preset.label }}</button>
                </div>
                <div class="flex gap-2 items-end">
                  <textarea
                    v-model="cardPromptText[heading.section_id]"
                    class="flex-1 font-sans text-[13px] border border-[var(--color-rule)] rounded-md px-2.5 py-1.5 resize-none bg-white text-[var(--color-ink)] focus:outline-none focus:border-[var(--color-accent)] min-h-[36px] max-h-24"
                    placeholder="Уточнения к черновику (опционально)…"
                    rows="2"
                  />
                  <button
                    @click="runGenerate(heading.section_id)"
                    class="flex-shrink-0 bg-[var(--color-accent)] text-white border-none rounded-md px-3.5 py-1.5 text-[13px] font-medium cursor-pointer hover:bg-[var(--color-accent-deep)] whitespace-nowrap"
                  >Написать →</button>
                </div>
                <button @click="cardStates[heading.section_id] = 'idle'" class="text-[11px] text-[var(--color-ink-muted)] mt-1.5 hover:text-[var(--color-ink)]">Отмена</button>
              </div>

              <!-- Ops row (prototype style) -->
              <div class="flex flex-wrap gap-1.5 px-4 py-2.5 border-t border-[var(--color-rule-light)]">
                <button @click="openPrompt(heading.section_id)"
                  :disabled="sectionDraftState(heading.section_id) === 0"
                  :class="['inline-flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-md border transition-colors', sectionDraftState(heading.section_id) === 0 ? 'border-[var(--color-rule)] text-[var(--color-ink-muted)] opacity-40 cursor-not-allowed' : 'border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] hover:text-[var(--color-ink)]']">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487z"/></svg>
                  Переписать
                </button>
                <button @click="openPrompt(heading.section_id)"
                  :disabled="sectionDraftState(heading.section_id) === 0"
                  :class="['inline-flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-md border transition-colors', sectionDraftState(heading.section_id) === 0 ? 'border-[var(--color-rule)] text-[var(--color-ink-muted)] opacity-40 cursor-not-allowed' : 'border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] hover:text-[var(--color-ink)]']">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25"/></svg>
                  Сократить
                </button>
                <button @click="openPrompt(heading.section_id)"
                  :disabled="sectionDraftState(heading.section_id) === 0"
                  :class="['inline-flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-md border transition-colors', sectionDraftState(heading.section_id) === 0 ? 'border-[var(--color-rule)] text-[var(--color-ink-muted)] opacity-40 cursor-not-allowed' : 'border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] hover:text-[var(--color-ink)]']">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15"/></svg>
                  Развернуть
                </button>
                <RouterLink
                  :to="`/${projectId}/library`"
                  class="inline-flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-md border-[var(--color-accent)] text-white bg-[var(--color-accent)] hover:bg-[var(--color-accent-deep)] transition-colors"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244"/></svg>
                  Закрыть пробелы
                </RouterLink>
              </div>

              <!-- Citation search zone (always visible, prototype style) -->
              <div class="border-t border-[var(--color-rule-light)] px-4 py-3 bg-[#fafaf9]">
                <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-2 flex items-center gap-1.5">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                  Найти цитату
                </div>
                <div class="flex flex-wrap gap-1.5 mb-2">
                  <button
                    v-for="preset in CITE_PRESETS"
                    :key="preset"
                    @click="setCiteQuery(heading.section_id, preset)"
                    :class="[
                      'text-[11px] px-2 py-0.5 rounded border transition-all',
                      citeSearchQuery[heading.section_id] === preset
                        ? 'border-[var(--color-accent)] text-[var(--color-accent)] bg-[var(--color-accent-pale)]'
                        : 'border-[var(--color-rule)] text-[var(--color-ink-muted)] bg-white hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]'
                    ]"
                  >{{ preset }}</button>
                </div>
                <div class="flex gap-2">
                  <input
                    :value="citeSearchQuery[heading.section_id] ?? ''"
                    @input="setCiteQuery(heading.section_id, ($event.target as HTMLInputElement).value)"
                    @keydown.enter="runCiteSearch(heading.section_id, citeSearchQuery[heading.section_id] ?? '')"
                    type="text"
                    class="flex-1 text-[13px] border border-[var(--color-rule)] rounded-md px-2.5 py-1.5 bg-white focus:outline-none focus:border-[var(--color-accent)] placeholder-[var(--color-ink-muted)]"
                    placeholder="Клемма, найди цитату для утверждения…"
                    autocomplete="off"
                  />
                  <button
                    @click="runCiteSearch(heading.section_id, citeSearchQuery[heading.section_id] ?? '')"
                    class="flex-shrink-0 bg-[var(--color-accent)] text-white border-none rounded-md px-3 py-1.5 text-[13px] font-medium cursor-pointer hover:bg-[var(--color-accent-deep)] whitespace-nowrap"
                  >→ Найти</button>
                </div>
                <!-- Results -->
                <div v-if="citeSearchLoading[heading.section_id]" class="mt-2 text-center text-[12px] text-[var(--color-ink-muted)] py-2">Ищу…</div>
                <div v-else-if="(citeSearchResults[heading.section_id] ?? []).length > 0" class="mt-2 border border-[var(--color-rule)] rounded-md overflow-hidden bg-white divide-y divide-[var(--color-rule-light)] max-h-56 overflow-y-auto">
                  <div
                    v-for="r in citeSearchResults[heading.section_id]"
                    :key="r.fragment_id"
                    class="px-3 py-2 hover:bg-[var(--color-accent-pale)] flex gap-3 items-start transition-colors"
                  >
                    <div class="flex-1 min-w-0">
                      <div class="flex items-center gap-2 mb-0.5">
                        <span class="font-mono text-[11px] font-semibold text-[var(--color-accent)]">@{{ r.citekey }}</span>
                        <span class="text-[11px] text-[var(--color-ink-muted)]">{{ r.year }}</span>
                        <span class="text-[11px] text-[var(--color-ink-muted)] truncate">{{ r.title }}</span>
                      </div>
                      <p class="text-[12px] text-[var(--color-ink)] leading-relaxed line-clamp-2">{{ r.text }}</p>
                    </div>
                    <button
                      @click="attachFragmentSource(heading.section_id, r.citekey)"
                      class="flex-shrink-0 text-[11px] px-2 py-0.5 rounded border border-[var(--color-rule)] bg-white text-[var(--color-ink-muted)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] hover:bg-[var(--color-accent-pale)] transition-colors"
                    >+ прикрепить</button>
                  </div>
                </div>
              </div>

            </div><!-- end idle/prompt/accepted -->

          </div><!-- end section card -->
        </template>
      </main>

      <!-- ── Right panel ──────────────────────────────────────────────── -->
      <aside class="w-64 flex-shrink-0 bg-[var(--color-paper-white)] border-l border-[var(--color-rule)] flex flex-col overflow-y-auto">

        <!-- Active section info -->
        <div class="border-b border-[var(--color-rule)] px-4 py-3">
          <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-1">Активная секция</div>
          <template v-if="activeSectionId">
            <div class="text-[12px] font-semibold text-[var(--color-accent-deep)] leading-snug mb-0.5">
              {{ activeSections.find(h => h.section_id === activeSectionId)?.full_title || activeSectionId }}
            </div>
            <div class="text-[11px] text-[var(--color-ink-muted)]">
              {{ sectionWordCount(activeSectionId) }} сл · {{ panelSources.length }} источника
            </div>
          </template>
          <div v-else class="text-[12px] text-[var(--color-ink-muted)] italic">Выберите раздел</div>
        </div>

        <!-- Pinned zone (shown when any pins exist) -->
        <div v-if="pinnedCitekeys.size > 0" class="border-b border-[var(--color-rule)] px-4 py-3">
          <div class="bg-[#fffbeb] border border-[#fde68a] rounded-md px-3 py-2">
            <div class="text-[11px] font-semibold text-[#b45309] uppercase tracking-[0.5px] mb-1.5 flex items-center gap-1">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M6.32 2.577a49.255 49.255 0 0111.36 0c1.497.174 2.57 1.46 2.57 2.93V21a.75.75 0 01-1.085.67L12 18.089l-7.165 3.583A.75.75 0 013.75 21V5.507c0-1.47 1.073-2.756 2.57-2.93z" clip-rule="evenodd"/></svg>
              Закреплено для генерации
            </div>
            <div class="flex flex-wrap gap-1">
              <span
                v-for="key in [...pinnedCitekeys]"
                :key="key"
                @click="togglePin(key)"
                class="text-[11px] text-[#92400e] bg-[#fef3c7] rounded font-mono px-1.5 py-0.5 cursor-pointer hover:bg-[#fde68a]"
              >{{ key }}</span>
            </div>
            <div class="text-[11px] text-[#b45309] mt-1.5 italic">Эти фрагменты всегда попадают в промпт</div>
          </div>
        </div>

        <!-- "Добавить источник" primary button -->
        <div class="border-b border-[var(--color-rule)] px-4 py-3">
          <button
            @click="addSourceOpen = true"
            class="w-full flex items-center justify-center gap-2 py-2 rounded-md bg-[var(--color-accent)] text-white text-[13px] font-semibold hover:bg-[var(--color-accent-deep)] transition-colors"
          >
            <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
              <circle cx="7.5" cy="7.5" r="6.5" stroke="white" stroke-width="1.4"/>
              <path d="M7.5 4.5v6M4.5 7.5h6" stroke="white" stroke-width="1.6" stroke-linecap="round"/>
            </svg>
            Добавить источник
          </button>
        </div>

        <!-- Fragment list -->
        <div class="border-b border-[var(--color-rule)] px-4 py-3 flex-1">
          <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-2">Фрагменты секции</div>
          <div v-if="panelLoading" class="text-[12px] text-[var(--color-ink-muted)] py-1">Загрузка…</div>
          <div v-else-if="panelSources.length === 0" class="text-[12px] text-[var(--color-ink-muted)] italic">Нет источников</div>
          <template v-else>
            <div
              v-for="key in panelSources"
              :key="key"
              :class="[
                'border rounded-md px-2.5 py-2 mb-1.5 transition-all duration-100 relative group cursor-default',
                isUsedInText(key)
                  ? 'border-green-200 bg-[var(--color-ok-bg)]'
                  : pinnedCitekeys.has(key)
                    ? 'border-[#fbbf24] bg-[#fffbeb]'
                    : 'border-[var(--color-rule)] hover:border-[#c8c4be]'
              ]"
            >
              <div class="flex items-start gap-1">
                <span :class="['font-mono text-[11px] flex-1', isUsedInText(key) ? 'text-[var(--color-ok)]' : pinnedCitekeys.has(key) ? 'text-[#b45309]' : 'text-[var(--color-ink-muted)]']">
                  [@{{ key }}]
                  <template v-if="isUsedInText(key)">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="inline-block align-[-1px]"><path d="M4.5 12.75l6 6 9-13.5"/></svg>
                    в тексте
                  </template>
                </span>
              </div>
              <!-- Actions: visible on hover -->
              <div class="flex gap-1 mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  @click.stop="togglePin(key)"
                  :class="[
                    'text-[11px] px-1.5 py-0.5 rounded border transition-all',
                    pinnedCitekeys.has(key)
                      ? 'border-[#fbbf24] text-[#b45309] bg-[#fef3c7]'
                      : 'border-[var(--color-rule)] bg-white text-[var(--color-ink-muted)] hover:border-[#fbbf24] hover:text-[#b45309] hover:bg-[#fffbeb]'
                  ]"
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="inline-block align-[-1px]"><path d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0111.186 0z"/></svg>
                  Закрепить
                </button>
                <button
                  @click.stop="detachFromPanel(key)"
                  class="text-[11px] px-1.5 py-0.5 rounded border border-[var(--color-rule)] bg-white text-[var(--color-ink-muted)] hover:border-[#fecaca] hover:text-[var(--color-err)] hover:bg-[var(--color-err-bg)] transition-all"
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="inline-block align-[-1px]"><path d="M6 18L18 6M6 6l12 12"/></svg>
                  Убрать
                </button>
              </div>
            </div>
          </template>
        </div>

        <!-- Chapter stats -->
        <div class="px-4 py-3">
          <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-2">Статистика главы</div>
          <div class="flex justify-between items-center text-[13px] mb-1.5">
            <span class="text-[var(--color-ink-muted)]">Слов</span>
            <span class="font-mono font-semibold text-[var(--color-ink)]">{{ totalWordCount }}</span>
          </div>
          <div class="flex justify-between items-center text-[13px] mb-1.5">
            <span class="text-[var(--color-ink-muted)]">Источников</span>
            <span class="font-mono font-semibold text-[var(--color-ink)]">{{ totalSources }}</span>
          </div>
          <div class="flex justify-between items-center text-[13px] mb-1.5">
            <span class="text-[var(--color-ink-muted)]">Разделов</span>
            <span class="font-mono font-semibold text-[var(--color-ink)]">{{ activeSections.length }}</span>
          </div>
          <div class="mt-2 bg-[var(--color-rule-light)] rounded h-1.5 overflow-hidden">
            <div :style="`width: ${coveragePercent}%`" class="h-full bg-[var(--color-accent)] rounded transition-all duration-500"></div>
          </div>
          <div class="text-[10px] text-[var(--color-ink-muted)] mt-1 text-right">{{ coveragePercent }}% покрыто</div>
        </div>

      </aside>

  </div><!-- end workspace -->

  <!-- ── Toast ────────────────────────────────────────────────────────── -->
  <div :class="['fixed bottom-5 right-5 bg-[var(--color-ink)] text-white px-4 py-2 rounded-lg text-[13px] shadow-lg transition-all duration-250 pointer-events-none z-50', toastVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10']">
    {{ toastMsg }}
  </div>

  <!-- ── "Добавить источник" slide-in ──────────────────────────────────── -->
  <Teleport to="body">
    <template v-if="addSourceOpen">
      <div @click="addSourceOpen = false" class="fixed inset-0 bg-black/15 z-[99]"></div>
      <aside class="fixed right-0 top-0 h-full w-80 bg-white shadow-2xl border-l border-[var(--color-rule)] z-[100] flex flex-col">
        <div class="flex items-center gap-3 px-4 py-3 border-b border-[var(--color-rule)] bg-[var(--color-rule-light)] flex-shrink-0">
          <div class="text-[13px] font-semibold text-[var(--color-ink)] flex-1">Добавить источник</div>
          <button
            @click="addSourceOpen = false"
            class="w-7 h-7 flex items-center justify-center rounded-md border border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:bg-[var(--color-err-bg)] hover:text-[var(--color-err)] hover:border-[#fecaca] transition-all"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <SourcePanel
          :section-id="activeSectionId ?? ''"
          :project-id="projectId"
          :is-demo-mode="false"
          :section-name="activeSections.find(h => h.section_id === activeSectionId)?.full_title ?? activeSectionId ?? ''"
          @attach="onPanelAttach"
          @detach="onPanelDetach"
          class="flex-1 overflow-y-auto"
        />
      </aside>
    </template>
  </Teleport>

  <!-- ── Source drawer ─────────────────────────────────────────────────── -->
  <SourceDrawer
    v-if="drawerCitekey"
    :citekey="drawerCitekey"
    :project-id="projectId"
    :active-section-id="activeSectionId"
    @close="drawerCitekey = null"
  />

</template>
