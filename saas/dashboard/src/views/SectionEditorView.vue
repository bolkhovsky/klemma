<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import {
  projects as apiProjects, drafts, write as writeApi,
  process as processApi,
  type DraftFile, type DraftHeading,
} from '@/api/client'
import SourceDrawer from '@/components/SourceDrawer.vue'
import SourcePanel from '@/components/SourcePanel.vue'

const route = useRoute()
const projectId = computed(() => route.params.projectId as string)

// ── State ─────────────────────────────────────────────────────────────────
const loading = ref(true)
const loadError = ref('')
const draftFiles = ref<DraftFile[]>([])
const sectionCounts = ref<Record<string, number>>({})
const totalSources = ref(0)


const activeFile = ref('')
const activeSectionId = ref<string | null>(null)

// Draft generation
type CardState = 'idle' | 'generating' | 'diff'
const cardStates = ref<Record<string, CardState>>({})
const cardGenJobId = ref<Record<string, string>>({})
const cardGenResult = ref<Record<string, string>>({})
const cardGenBefore = ref<Record<string, string>>({})
let pollTimers: Record<string, ReturnType<typeof setInterval>> = {}

// Toast
const toastMsg = ref('')
const toastVisible = ref(false)
let toastTimer: ReturnType<typeof setTimeout> | null = null
function showToast(msg: string) {
  toastMsg.value = msg
  toastVisible.value = true
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastVisible.value = false }, 3000)
}

// ── Helpers ────────────────────────────────────────────────────────────────
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
  if (id === 'conclusion') return 99
  return 50
}

const sortedFiles = computed(() =>
  [...draftFiles.value].filter(f => f.name !== 'dissertation.md').sort((a, b) => fileOrder(a.name) - fileOrder(b.name))
)
const activeFileData = computed(() => draftFiles.value.find(f => f.name === activeFile.value))

const activeSections = computed((): DraftHeading[] => {
  const headings = activeFileData.value?.headings ?? []
  const level3 = headings.filter(h => h.level === 3)
  if (level3.length > 0) return level3
  return headings.filter(h => h.level === 2)
})

const coveragePercent = computed(() => {
  const total = Object.keys(sectionCounts.value).length
  if (!total) return 0
  return Math.round((Object.values(sectionCounts.value).filter(c => c >= 5).length / total) * 100)
})

const totalWordCount = computed(() =>
  activeSections.value.reduce((sum, h) => sum + sectionWordCount(h.section_id), 0)
)

// ── File content ──────────────────────────────────────────────────────────
const fileContent = ref<Record<string, string>>({})

watch(activeFile, async (name) => {
  if (name && !fileContent.value[name]) {
    try { fileContent.value[name] = (await drafts.get(projectId.value, name)).content }
    catch { /* empty */ }
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

function sectionWordCount(id: string): number {
  const text = currentSectionText(id)
  return text ? text.trim().split(/\s+/).filter(Boolean).length : 0
}

function renderWithCitekeys(text: string): string {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\[@([\w\d_:.-]+)\]/g, '<span class="citekey-link" data-citekey="$1">[@$1]</span>')
    .replace(/\n/g, '<br>')
}

// ── Citekey click → drawer ────────────────────────────────────────────────
const drawerCitekey = ref<string | null>(null)
function handleCitekeyClick(e: MouseEvent) {
  const key = (e.target as HTMLElement).dataset?.citekey
  if (key) drawerCitekey.value = key
}

// ── Right panel sources ───────────────────────────────────────────────────
const assignedSources = ref<string[]>([])
const panelLoading = ref(false)
const addSourceOpen = ref(false)

// Computed so it always reflects current file content + API assignments — no timing issues
const panelSources = computed((): string[] => {
  const id = activeSectionId.value
  if (!id) return []
  const text = currentSectionText(id)
  const fromText = [...text.matchAll(/\[@([\w\d_:.-]+)\]/g)].map(m => m[1]!)
  return [...new Set([...assignedSources.value, ...fromText])]
})

watch(activeSectionId, async (id) => {
  assignedSources.value = []
  if (!id) return
  panelLoading.value = true
  try { assignedSources.value = (await apiProjects.sectionSources(id)).citekeys }
  catch { assignedSources.value = [] }
  finally { panelLoading.value = false }
})

function onPanelAttach(citekey: string) {
  if (!assignedSources.value.includes(citekey)) assignedSources.value.push(citekey)
}
function onPanelDetach(citekey: string) {
  assignedSources.value = assignedSources.value.filter(k => k !== citekey)
}
async function detachSource(citekey: string) {
  assignedSources.value = assignedSources.value.filter(k => k !== citekey)
  const id = activeSectionId.value
  if (id) try { await apiProjects.detachSection(citekey, id) } catch { /* ignore */ }
}

function isUsedInText(citekey: string): boolean {
  const id = activeSectionId.value
  return id ? currentSectionText(id).includes(`[@${citekey}]`) : false
}

// ── Draft generation ──────────────────────────────────────────────────────
function canGenerate(_id: string): boolean { return totalSources.value > 0 }

async function runGenerate(sectionId: string) {
  if (!canGenerate(sectionId)) return
  cardStates.value[sectionId] = 'generating'
  cardGenBefore.value[sectionId] = currentSectionText(sectionId)
  try {
    const resp = await writeApi.draft(sectionId, projectId.value, 'Подбери релевантные цитаты из источников для этого раздела. Только цитаты с [@citekey] ссылками, без авторского текста и связок.')
    cardGenJobId.value[sectionId] = resp.job_id
    startPoll(sectionId)
  } catch (e: any) {
    cardStates.value[sectionId] = 'idle'
    showToast(`Ошибка: ${e.message}`)
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
        if (resp.result?.status === 'error') { cardStates.value[sectionId] = 'idle'; showToast(resp.result.detail || 'Ошибка') }
        else { cardGenResult.value[sectionId] = text; cardStates.value[sectionId] = 'diff' }
      } else if (resp.status === 'failed') {
        stopPoll(sectionId); cardStates.value[sectionId] = 'idle'
        showToast(resp.result?.detail || 'Ошибка генерации')
      }
    } catch { /* keep polling */ }
  }, 3000)
}

function stopPoll(id: string) { if (pollTimers[id]) { clearInterval(pollTimers[id]); delete pollTimers[id] } }

async function acceptDraft(sectionId: string) {
  const text = cardGenResult.value[sectionId] ?? ''
  if (!text || !activeFile.value) return
  const h = activeSections.value.find(h => h.section_id === sectionId)
  try {
    await drafts.upsertSection(projectId.value, activeFile.value, sectionId, text, h?.full_title)
    cardStates.value[sectionId] = 'idle'
    fileContent.value[activeFile.value] = (await drafts.get(projectId.value, activeFile.value)).content
    showToast('Правки приняты')
  } catch (e: any) { showToast(`Ошибка: ${e.message}`) }
}

function rejectDraft(id: string) { cardStates.value[id] = 'idle'; cardGenResult.value[id] = '' }

// ── Badges ────────────────────────────────────────────────────────────────
function getBadges(id: string): { label: string; cls: string }[] {
  const c = sectionCounts.value[id] ?? 0
  const wc = sectionWordCount(id)
  const state = cardStates.value[id] ?? 'idle'
  const b: { label: string; cls: string }[] = []
  if (wc > 0) b.push({ label: `${wc} сл`, cls: 'text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200' })
  if (c === 0) {
    if (wc === 0) b.push({ label: 'нет черновика', cls: 'text-[var(--color-err)] bg-[var(--color-err-bg)] border border-red-200 font-semibold' })
    b.push({ label: '0 источников', cls: 'text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] border border-[var(--color-rule)]' })
  } else {
    b.push({ label: `${c} ист.`, cls: c < 5 ? 'text-[var(--color-warn)] bg-[var(--color-warn-bg)] border border-amber-200 font-semibold' : 'text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200' })
  }
  if (state === 'generating') b.push({ label: 'генерация…', cls: 'text-[var(--color-warn)] bg-[var(--color-warn-bg)] border border-amber-200' })
  else if (state === 'diff') b.push({ label: 'правки готовы', cls: 'text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200' })
  return b
}

// ── Data loading ──────────────────────────────────────────────────────────
async function loadAll() {
  loading.value = true
  loadError.value = ''
  try {
    const [filesData, coverageData] = await Promise.allSettled([
      drafts.list(projectId.value), apiProjects.coverage(),
    ])
    if (filesData.status === 'fulfilled') draftFiles.value = filesData.value.files
    if (coverageData.status === 'fulfilled') { sectionCounts.value = coverageData.value.sections; totalSources.value = coverageData.value.total_sources }

    // Auto-scaffold multi-file drafts if outline exists but no files yet
    if (draftFiles.value.length === 0) {
      try {
        const result = await drafts.scaffold(projectId.value)
        draftFiles.value = result.files
      } catch { /* scaffold not available or no outline */ }
    }
  } catch (e: any) { loadError.value = e.message ?? 'Ошибка загрузки' }
  finally {
    loading.value = false
    if (!activeFile.value && draftFiles.value.length > 0) {
      activeFile.value = [...draftFiles.value].sort((a, b) => fileOrder(a.name) - fileOrder(b.name))[0]?.name ?? ''
    }
    await handleQuerySection()
  }
}

async function handleQuerySection() {
  const sec = route.query.section as string | undefined
  const file = route.query.file as string | undefined
  if (file) { activeFile.value = file; if (!sec) activeSectionId.value = null }
  if (sec) {
    if (!file) {
      const ch = sec.split('.')[0]
      if (!ch || ch === 'intro' || ch === '0') activeFile.value = 'intro.md'
      else if (ch === 'conclusion') activeFile.value = 'conclusion.md'
      else activeFile.value = `chapter_${ch}.md`
    }
    activeSectionId.value = sec
    await nextTick()
    document.getElementById(`section-${sec}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

onMounted(loadAll)
watch(() => [route.query.section, route.query.file], () => handleQuerySection())
onUnmounted(() => { Object.keys(pollTimers).forEach(stopPoll); if (toastTimer) clearTimeout(toastTimer) })
</script>

<template>
  <div class="flex flex-1 overflow-hidden">

    <!-- ── Main ─────────────────────────────────────────────────────── -->
    <main class="flex-1 overflow-y-auto px-6 py-6">

      <div v-if="loading" class="flex items-center justify-center h-40 text-[var(--color-ink-muted)]">
        <svg class="animate-spin w-5 h-5 mr-2" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-dasharray="31.4" stroke-dashoffset="10"/></svg>
        Загрузка…
      </div>

      <div v-else-if="loadError" class="text-[var(--color-err)] text-sm p-4 bg-[var(--color-err-bg)] rounded-lg">{{ loadError }}</div>

      <div v-else-if="draftFiles.length === 0" class="text-center py-16 px-6">
        <div class="text-4xl mb-4">📄</div>
        <h2 class="text-[15px] font-semibold text-[var(--color-ink)] mb-2">Черновиков пока нет</h2>
        <p class="text-[13px] text-[var(--color-ink-muted)] mb-5 leading-relaxed">Загрузите PDF-статьи в библиотеку, а Klemma поможет написать черновик на основе источников.</p>
        <RouterLink
          :to="`/${projectId}/library`"
          class="inline-flex items-center gap-2 bg-[var(--color-accent)] text-white rounded-md px-4 py-2 text-[13px] font-semibold hover:bg-[var(--color-accent-deep)] transition-colors no-underline"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          Загрузить PDF
        </RouterLink>
      </div>

      <template v-else-if="activeFileData">
        <!-- Chapter heading -->
        <div class="mb-5">
          <h1 class="font-display text-[18px] font-semibold tracking-tight text-[var(--color-ink)]">{{ fileDisplayName(activeFile) }}</h1>
          <p class="text-[13px] font-mono text-[var(--color-ink-muted)] mt-1">{{ activeFile }} · {{ activeFileData.word_count }} слов · {{ activeSections.length }} разделов</p>
        </div>

        <div v-if="activeSections.length === 0" class="text-center py-8 text-[var(--color-ink-muted)] text-sm">
          Нет подразделов. Добавьте <code class="font-mono bg-[var(--color-rule-light)] px-1 rounded">##</code> заголовки в черновик.
        </div>

        <!-- Section cards -->
        <div
          v-for="heading in activeSections" :key="heading.section_id"
          :id="`section-${heading.section_id}`"
          :class="['bg-white border rounded-[10px] mb-3.5 overflow-hidden transition-all',
            activeSectionId === heading.section_id ? 'border-[var(--color-accent)] shadow-[0_0_0_3px_rgba(13,115,119,0.08)]'
            : cardStates[heading.section_id] === 'diff' ? 'border-[var(--color-ok)] shadow-[0_0_0_3px_rgba(45,106,79,0.08)]'
            : cardStates[heading.section_id] === 'generating' ? 'border-amber-400 shadow-[0_0_0_3px_rgba(180,83,9,0.07)]'
            : 'border-[var(--color-rule)] hover:border-[#d4d0ca]']"
        >
          <!-- Header -->
          <div @click="activeSectionId = activeSectionId === heading.section_id ? null : heading.section_id"
            class="flex items-center gap-2.5 px-4 py-3 cursor-pointer border-b border-[var(--color-rule-light)] hover:bg-[var(--color-rule-light)] transition-colors">
            <span class="text-[11px] font-mono font-semibold text-[var(--color-accent)] bg-[var(--color-accent-pale)] rounded px-1.5 py-0.5 flex-shrink-0">{{ heading.section_id }}</span>
            <span class="text-[14px] font-medium text-[var(--color-ink)] flex-1">{{ heading.full_title.replace(/^[\d.]+\s*/, '') }}</span>
            <span v-for="badge in getBadges(heading.section_id)" :key="badge.label" :class="['text-[11px] font-mono rounded px-1.5 py-0.5', badge.cls]">{{ badge.label }}</span>
          </div>

          <!-- Generating -->
          <div v-if="cardStates[heading.section_id] === 'generating'" class="flex flex-col items-center py-7 gap-3">
            <div class="w-7 h-7 rounded-full border-[2.5px] border-[var(--color-rule)] border-t-amber-500 animate-spin" />
            <p class="text-[13px] text-[var(--color-ink-muted)]">Анализирую источники…</p>
          </div>

          <!-- Diff -->
          <div v-else-if="cardStates[heading.section_id] === 'diff'">
            <div class="flex items-center gap-2 px-4 py-2 bg-[var(--color-ok-bg)] border-b border-green-200 text-[12px] text-[var(--color-ok)]">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
              Klemma предлагает правки
            </div>
            <div class="grid grid-cols-2 divide-x divide-[var(--color-rule-light)]">
              <div class="px-4 py-3 bg-[#fff8f8]">
                <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-err)] mb-2">Было</div>
                <p class="text-[13px] text-[var(--color-ink-muted)] leading-relaxed italic">{{ cardGenBefore[heading.section_id] || '(пустой раздел)' }}</p>
              </div>
              <div class="px-4 py-3 bg-[#f8fff9]">
                <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ok)] mb-2">Предложение</div>
                <div v-html="renderWithCitekeys(cardGenResult[heading.section_id] ?? '')" @click="handleCitekeyClick($event)"
                  class="text-[13px] text-[var(--color-ink)] leading-relaxed [&_.citekey-link]:text-[var(--color-accent)] [&_.citekey-link]:underline [&_.citekey-link]:decoration-dotted [&_.citekey-link]:cursor-pointer" />
              </div>
            </div>
            <div class="flex items-center gap-2 px-4 py-2.5 border-t border-[var(--color-rule-light)]">
              <button @click="acceptDraft(heading.section_id)" class="inline-flex items-center gap-1.5 bg-[var(--color-ok)] text-white rounded-md px-3.5 py-1.5 text-[13px] font-medium cursor-pointer hover:bg-green-700 transition-colors">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg> Принять
              </button>
              <button @click="rejectDraft(heading.section_id)" class="inline-flex items-center gap-1.5 text-[var(--color-err)] border border-red-200 rounded-md px-3.5 py-1.5 text-[13px] cursor-pointer hover:bg-[var(--color-err-bg)] transition-colors">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Отклонить
              </button>
            </div>
          </div>

          <!-- Idle -->
          <div v-else>
            <!-- Prose -->
            <div v-if="currentSectionText(heading.section_id)" class="px-4 pt-4 pb-3">
              <div v-html="renderWithCitekeys(currentSectionText(heading.section_id))" @click="handleCitekeyClick($event)"
                class="text-[14px] leading-[1.75] text-[var(--color-ink-2,#3d3d5c)] [&_.citekey-link]:font-mono [&_.citekey-link]:text-[13px] [&_.citekey-link]:text-[var(--color-accent)] [&_.citekey-link]:bg-[var(--color-accent-pale)] [&_.citekey-link]:rounded [&_.citekey-link]:px-1 [&_.citekey-link]:cursor-pointer [&_.citekey-link:hover]:underline" />
            </div>
            <div v-else class="px-4 py-5 text-center text-[13px] text-[var(--color-ink-muted)]">
              {{ canGenerate(heading.section_id) ? 'Черновик ещё не написан — подберите источники' : 'Загрузите PDF в библиотеку' }}
            </div>

            <!-- Single action button -->
            <div class="px-4 py-2.5 border-t border-[var(--color-rule-light)]">
              <button
                @click="runGenerate(heading.section_id)"
                :disabled="!canGenerate(heading.section_id)"
                :class="['inline-flex items-center gap-1.5 text-[13px] px-3.5 py-1.5 rounded-md font-medium transition-colors',
                  canGenerate(heading.section_id)
                    ? 'bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-deep)] cursor-pointer'
                    : 'bg-[var(--color-rule-light)] text-[var(--color-ink-muted)] cursor-not-allowed']"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                Подобрать источники
              </button>
            </div>
          </div>
        </div>
      </template>
    </main>

    <!-- ── Right panel ──────────────────────────────────────────────── -->
    <aside class="w-64 flex-shrink-0 bg-[var(--color-paper-white)] border-l border-[var(--color-rule)] flex flex-col overflow-y-auto">

      <!-- Active section -->
      <div class="border-b border-[var(--color-rule)] px-4 py-3">
        <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-1">Активная секция</div>
        <template v-if="activeSectionId">
          <div class="text-[12px] font-semibold text-[var(--color-accent-deep)] leading-snug mb-0.5">
            {{ activeSections.find(h => h.section_id === activeSectionId)?.full_title || activeSectionId }}
          </div>
          <div class="text-[11px] text-[var(--color-ink-muted)]">{{ sectionWordCount(activeSectionId) }} сл · {{ panelSources.length }} ист.</div>
        </template>
        <div v-else class="text-[12px] text-[var(--color-ink-muted)] italic">Выберите раздел</div>
      </div>

      <!-- Add source -->
      <div class="border-b border-[var(--color-rule)] px-4 py-3">
        <button @click="addSourceOpen = true"
          class="w-full flex items-center justify-center gap-2 py-2 rounded-md bg-[var(--color-accent)] text-white text-[13px] font-semibold hover:bg-[var(--color-accent-deep)] transition-colors"
          title="Найти в вашей библиотеке и прикрепить к разделу">
          <svg width="15" height="15" viewBox="0 0 15 15" fill="none"><circle cx="7.5" cy="7.5" r="6.5" stroke="white" stroke-width="1.4"/><path d="M7.5 4.5v6M4.5 7.5h6" stroke="white" stroke-width="1.6" stroke-linecap="round"/></svg>
          Найти в библиотеке
        </button>
      </div>

      <!-- Sources list -->
      <div class="border-b border-[var(--color-rule)] px-4 py-3 flex-1">
        <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-2">Источники секции</div>
        <div v-if="panelLoading" class="text-[12px] text-[var(--color-ink-muted)]">Загрузка…</div>
        <div v-else-if="panelSources.length === 0" class="text-[12px] text-[var(--color-ink-muted)] italic">Нет источников</div>
        <div v-for="key in panelSources" :key="key"
          :class="['flex items-center gap-1.5 px-2 py-1.5 rounded mb-1 group cursor-pointer hover:bg-[var(--color-rule-light)] transition-colors',
            isUsedInText(key) ? 'bg-[var(--color-ok-bg)]' : '']"
          @click="drawerCitekey = key"
        >
          <span :class="['font-mono text-[11px] flex-1 truncate', isUsedInText(key) ? 'text-[var(--color-ok)]' : 'text-[var(--color-ink-muted)]']">
            @{{ key }}
            <template v-if="isUsedInText(key)"> ✓</template>
          </span>
          <button @click.stop="detachSource(key)" class="text-[var(--color-ink-muted)] opacity-0 group-hover:opacity-100 hover:text-[var(--color-err)] transition-all" title="Открепить">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
      </div>

      <!-- Stats -->
      <div class="px-4 py-3">
        <div class="text-[11px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-2">Статистика</div>
        <div class="flex justify-between text-[13px] mb-1"><span class="text-[var(--color-ink-muted)]">Слов</span><span class="font-mono font-semibold">{{ totalWordCount }}</span></div>
        <div class="flex justify-between text-[13px] mb-1"><span class="text-[var(--color-ink-muted)]">Источников</span><span class="font-mono font-semibold">{{ totalSources }}</span></div>
        <div class="flex justify-between text-[13px] mb-1"><span class="text-[var(--color-ink-muted)]">Разделов</span><span class="font-mono font-semibold">{{ activeSections.length }}</span></div>
        <div class="mt-2 bg-[var(--color-rule-light)] rounded h-1.5 overflow-hidden">
          <div :style="`width:${coveragePercent}%`" class="h-full bg-[var(--color-accent)] rounded transition-all duration-500" />
        </div>
        <div class="text-[10px] text-[var(--color-ink-muted)] mt-1 text-right">{{ coveragePercent }}% покрыто</div>
      </div>
    </aside>
  </div>

  <!-- Toast -->
  <div :class="['fixed bottom-5 right-5 bg-[var(--color-ink)] text-white px-4 py-2 rounded-lg text-[13px] shadow-lg transition-all duration-250 pointer-events-none z-50', toastVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10']">{{ toastMsg }}</div>

  <!-- Add source drawer -->
  <Teleport to="body">
    <template v-if="addSourceOpen">
      <div @click="addSourceOpen = false" class="fixed inset-0 bg-black/15 z-[99]" />
      <aside class="fixed right-0 top-0 h-full w-80 bg-white shadow-2xl border-l border-[var(--color-rule)] z-[100] flex flex-col">
        <div class="flex items-center gap-3 px-4 py-3 border-b border-[var(--color-rule)] bg-[var(--color-rule-light)] flex-shrink-0">
          <div class="text-[13px] font-semibold text-[var(--color-ink)] flex-1">Найти в библиотеке</div>
          <button @click="addSourceOpen = false" class="w-7 h-7 flex items-center justify-center rounded-md border border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:text-[var(--color-err)] transition-all">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <SourcePanel :section-id="activeSectionId ?? ''" :project-id="projectId" :is-demo-mode="false"
          :section-name="activeSections.find(h => h.section_id === activeSectionId)?.full_title ?? ''"
          @attach="onPanelAttach" @detach="onPanelDetach" class="flex-1 overflow-y-auto" />
      </aside>
    </template>
  </Teleport>

  <!-- Source detail drawer -->
  <SourceDrawer v-if="drawerCitekey" :citekey="drawerCitekey" :project-id="projectId" :active-section-id="activeSectionId" @close="drawerCitekey = null" />
</template>
