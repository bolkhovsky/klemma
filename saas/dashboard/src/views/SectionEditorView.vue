<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  auth, usage, userProjects, projects as apiProjects, drafts, write as writeApi,
  process as processApi,
  type DraftFile, type DraftHeading,
} from '@/api/client'

const route = useRoute()
const router = useRouter()

const projectId = computed(() => route.params.projectId as string)

// ── Load state ─────────────────────────────────────────────────────────────
const loading = ref(true)
const loadError = ref('')
const draftFiles = ref<DraftFile[]>([])
const sectionCounts = ref<Record<string, number>>({})  // section_id → source count
const totalSources = ref(0)
const projectName = ref('')
const userName = ref('')
const userInitials = ref('?')
const tokenBalance = ref<{ total_granted: number; total_used: number; remaining: number } | null>(null)

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
  [...draftFiles.value].sort((a, b) => fileOrder(a.name) - fileOrder(b.name))
)

const activeFileData = computed(() =>
  draftFiles.value.find(f => f.name === activeFile.value)
)

const activeSections = computed((): DraftHeading[] =>
  (activeFileData.value?.headings ?? []).filter(h => h.level === 3)
)

const coveragePercent = computed(() => {
  const total = Object.keys(sectionCounts.value).length
  if (!total) return 0
  const covered = Object.values(sectionCounts.value).filter(c => c >= 5).length
  return Math.round((covered / total) * 100)
})

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
  const state = cardStates.value[id] ?? 'idle'
  const badges = []
  if (c === 0) {
    badges.push({ label: 'нет черновика', cls: 'text-[var(--color-err)] bg-[var(--color-err-bg)] border border-red-200 font-semibold' })
    badges.push({ label: '0 источников', cls: 'text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] border border-[var(--color-rule)]' })
  } else {
    if (c < MIN_SOURCES_WARN) {
      badges.push({ label: `${c} источников`, cls: 'text-[var(--color-warn)] bg-[var(--color-warn-bg)] border border-amber-200 font-semibold' })
    } else {
      badges.push({ label: `${c} источников`, cls: 'text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200' })
    }
    if (state === 'accepted') {
      badges.push({ label: 'черновик', cls: 'text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200' })
    }
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

function currentSectionText(sectionId: string): string {
  // Find section heading line + next heading line in active file content
  // We'll store it from the last fetch — for now return empty (diff will show just before=empty)
  return ''
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

// ── Data loading ─────────────────────────────────────────────────────────────
async function loadAll() {
  loading.value = true
  loadError.value = ''
  try {
    const [filesData, coverageData, projectsData, meData, usageData] = await Promise.allSettled([
      drafts.list(projectId.value),
      apiProjects.coverage(),
      userProjects.list(),
      auth.me(),
      usage.me(),
    ])

    if (filesData.status === 'fulfilled') {
      draftFiles.value = filesData.value.files
    }
    if (coverageData.status === 'fulfilled') {
      sectionCounts.value = coverageData.value.sections
      totalSources.value = coverageData.value.total_sources
    }
    if (projectsData.status === 'fulfilled') {
      const p = projectsData.value.projects.find(p => p.project_id === projectId.value)
      if (p) projectName.value = p.name
    }
    if (meData.status === 'fulfilled') {
      userName.value = meData.value.name || meData.value.email
      const parts = (meData.value.name || meData.value.email || '?').split(' ')
      userInitials.value = parts.map(w => w[0]?.toUpperCase() ?? '').slice(0, 2).join('')
    }
    if (usageData.status === 'fulfilled') {
      tokenBalance.value = usageData.value
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
      if (sec === 'intro') activeFile.value = 'intro.md'
      else if (sec === 'conclusion') activeFile.value = 'conclusion.md'
      else if (chapter) activeFile.value = `chapter_${chapter}.md`
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

// ── Sync ─────────────────────────────────────────────────────────────────────
function handleSync() {
  showToast('Синхронизация — используйте klemma pull/push из CLI')
}

// ── Token bar ─────────────────────────────────────────────────────────────────
const tokenPercent = computed(() => {
  if (!tokenBalance.value || tokenBalance.value.total_granted === 0) return 0
  return Math.round((tokenBalance.value.total_used / tokenBalance.value.total_granted) * 100)
})

const tokenBarCls = computed(() => {
  const p = tokenPercent.value
  if (p >= 90) return 'bg-[var(--color-err)]'
  if (p >= 75) return 'bg-[var(--color-warn)]'
  return 'bg-gradient-to-r from-violet-400 to-[var(--color-accent)]'
})
</script>

<template>
  <div class="flex flex-col h-screen bg-[var(--color-paper)]">

    <!-- ── Topbar ───────────────────────────────────────────────────────── -->
    <header class="h-12 flex-shrink-0 bg-white border-b border-[var(--color-rule)] flex items-center gap-3 px-5">
      <!-- Logo -->
      <RouterLink :to="`/${projectId}/map`" class="font-bold text-[15px] tracking-tight text-[var(--color-ink)] hover:opacity-80">
        k<span class="text-[var(--color-accent)]">lemma</span>
      </RouterLink>
      <!-- Project badge -->
      <span v-if="projectName" class="text-xs font-mono bg-[var(--color-rule-light)] border border-[var(--color-rule)] rounded px-2 py-0.5 text-[var(--color-ink-muted)] max-w-[180px] truncate">
        {{ projectName }}
      </span>

      <div class="flex-1" />

      <!-- Stats -->
      <span class="text-[11px] font-mono text-[var(--color-ink-muted)] hidden sm:block">
        {{ totalSources }} источников · {{ sortedFiles.length }} файлов · {{ coveragePercent }}%
      </span>

      <!-- Bell → Feed -->
      <RouterLink
        :to="`/${projectId}/feed`"
        class="relative w-8 h-8 rounded-lg flex items-center justify-center border border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] transition-colors"
        title="Лента событий"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>
        </svg>
        <span class="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-red-500 border border-white" />
      </RouterLink>

      <!-- Sync -->
      <button
        @click="handleSync"
        class="flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-lg border border-[var(--color-rule)] bg-transparent text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] transition-colors"
        title="Синхронизировать с локальным черновиком"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99"/>
        </svg>
        Синхронизировать
      </button>
    </header>

    <!-- ── Workspace ────────────────────────────────────────────────────── -->
    <div class="flex flex-1 overflow-hidden">

      <!-- ── Sidebar ───────────────────────────────────────────────────── -->
      <nav class="w-44 flex-shrink-0 bg-white border-r border-[var(--color-rule)] flex flex-col overflow-y-auto">
        <!-- Chapter structure -->
        <div class="px-3.5 pt-3 pb-1.5">
          <div class="text-[10px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-1.5">Структура</div>
        </div>
        <div
          v-for="file in sortedFiles"
          :key="file.name"
          @click="activeFile = file.name; activeSectionId = null"
          :class="[
            'flex items-center gap-2 px-3.5 py-1.5 text-[13px] cursor-pointer transition-colors',
            activeFile === file.name
              ? 'bg-[var(--color-accent-pale)] text-[var(--color-accent-deep)] font-medium'
              : 'text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)]'
          ]"
        >
          <span :class="[
            'w-1.5 h-1.5 rounded-full flex-shrink-0',
            activeFile === file.name ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-rule)]'
          ]" />
          {{ fileDisplayName(file.name) }}
        </div>

        <div class="flex-1" />

        <!-- App nav -->
        <div class="border-t border-[var(--color-rule)] pt-1 pb-1">
          <div class="text-[10px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] px-3.5 pt-2 pb-1">Навигация</div>
          <RouterLink
            :to="`/${projectId}/map`"
            class="flex items-center gap-2 px-3.5 py-1.5 text-[13px] text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" class="flex-shrink-0">
              <rect x="1" y="1" width="5.5" height="5.5" rx="1.2" stroke="currentColor" stroke-width="1.3"/>
              <rect x="7.5" y="1" width="5.5" height="5.5" rx="1.2" stroke="currentColor" stroke-width="1.3"/>
              <rect x="1" y="7.5" width="5.5" height="5.5" rx="1.2" stroke="currentColor" stroke-width="1.3"/>
              <rect x="7.5" y="7.5" width="5.5" height="5.5" rx="1.2" stroke="currentColor" stroke-width="1.3"/>
            </svg>
            Карта
          </RouterLink>
          <RouterLink
            :to="`/${projectId}/library`"
            class="flex items-center gap-2 px-3.5 py-1.5 text-[13px] text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" class="flex-shrink-0">
              <path d="M2 2h6l3 3v7a1 1 0 01-1 1H3a1 1 0 01-1-1V3a1 1 0 011-1z" stroke="currentColor" stroke-width="1.3"/>
              <path d="M8 2v3h3" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
              <path d="M4 7.5h6M4 9.5h4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
            </svg>
            Библиотека
          </RouterLink>
        </div>

        <!-- User profile + credits -->
        <div class="border-t border-[var(--color-rule)] px-3 py-2.5">
          <div class="flex items-center gap-2 mb-2.5">
            <div class="w-7 h-7 rounded-full flex-shrink-0 bg-gradient-to-br from-violet-400 to-[var(--color-accent)] flex items-center justify-center text-[11px] font-bold text-white">
              {{ userInitials }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="text-[12px] font-semibold text-[var(--color-ink)] truncate">{{ userName || '...' }}</div>
              <div class="inline-flex items-center gap-0.5 text-[10px] font-semibold text-violet-600 bg-violet-50 border border-violet-200 rounded px-1 mt-0.5">
                <svg width="8" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"/></svg>
                Pro
              </div>
            </div>
          </div>
          <!-- Credits bar -->
          <template v-if="tokenBalance">
            <div class="flex justify-between items-center text-[10px] text-[var(--color-ink-muted)] mb-1">
              <span>Кредиты</span>
              <span class="font-semibold font-mono text-[var(--color-ink-muted)]">{{ tokenBalance.remaining.toLocaleString() }}</span>
            </div>
            <div class="h-1 rounded-full bg-[var(--color-rule)] overflow-hidden mb-1">
              <div :class="['h-full rounded-full transition-all', tokenBarCls]" :style="{ width: `${100 - tokenPercent}%` }" />
            </div>
            <div class="text-[10px] text-right text-[var(--color-ink-muted)]">
              из {{ tokenBalance.total_granted.toLocaleString() }}
            </div>
          </template>
        </div>
      </nav>

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
            <h1 class="text-[18px] font-semibold tracking-tight text-[var(--color-ink)]">
              {{ fileDisplayName(activeFile) }}
            </h1>
            <p class="text-[12px] font-mono text-[var(--color-ink-muted)] mt-1">
              {{ activeFile }} · {{ activeFileData.word_count }} слов · {{ activeSections.length }} разделов
            </p>
          </div>

          <!-- No sections in file -->
          <div v-if="activeSections.length === 0" class="text-center py-8 text-[var(--color-ink-muted)] text-sm">
            Нет подразделов в этом файле.
            <RouterLink :to="`/${projectId}/edit/${activeFile}`" class="text-[var(--color-accent)] underline ml-1">
              Открыть редактор
            </RouterLink>
          </div>

          <!-- Section cards -->
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
            <!-- Card header -->
            <div
              @click="activeSectionId = activeSectionId === heading.section_id ? null : heading.section_id"
              class="flex items-center gap-2.5 px-4 py-3 cursor-pointer border-b border-[var(--color-rule-light)] hover:bg-[var(--color-rule-light)] transition-colors"
            >
              <!-- Section ID badge -->
              <span class="text-[11px] font-mono font-semibold text-[var(--color-accent)] bg-[var(--color-accent-pale)] rounded px-1.5 py-0.5 flex-shrink-0">
                {{ heading.section_id }}
              </span>
              <!-- Title -->
              <span class="text-[14px] font-medium text-[var(--color-ink)] flex-1">
                {{ heading.full_title.replace(/^[\d.]+\s*/, '') }}
              </span>
              <!-- Badges -->
              <div class="flex gap-1.5 flex-shrink-0">
                <span
                  v-for="badge in getBadges(heading.section_id)"
                  :key="badge.label"
                  :class="['text-[10px] font-mono rounded px-1.5 py-0.5', badge.cls]"
                >
                  {{ badge.label }}
                </span>
              </div>
              <!-- Chevron -->
              <svg :class="['w-4 h-4 text-[var(--color-ink-muted)] flex-shrink-0 transition-transform', activeSectionId === heading.section_id ? 'rotate-180' : '']" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/>
              </svg>
            </div>

            <!-- Card body — only when active -->
            <template v-if="activeSectionId === heading.section_id">

              <!-- STATE: idle / prompt / accepted — show action area -->
              <div v-if="cardStates[heading.section_id] !== 'generating' && cardStates[heading.section_id] !== 'diff'" class="px-4 py-4">

                <!-- State 0: no sources -->
                <div v-if="sectionDraftState(heading.section_id) === 0" class="text-center py-5">
                  <div class="w-10 h-10 rounded-full bg-[var(--color-err-bg)] flex items-center justify-center mx-auto mb-3">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--color-err)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                    </svg>
                  </div>
                  <p class="text-[13px] text-[var(--color-ink-muted)] mb-1">Нет источников для этого раздела</p>
                  <p class="text-[11px] text-[var(--color-ink-muted)] opacity-70 mb-4">Добавьте источники в разделе Библиотека</p>
                  <button disabled class="inline-flex items-center gap-1.5 opacity-40 cursor-not-allowed bg-[var(--color-ink-muted)] text-white rounded-lg px-4 py-2 text-[13px] font-medium">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                    Написать черновик
                  </button>
                  <p class="text-[11px] text-[var(--color-err)] bg-[var(--color-err-bg)] border border-red-200 rounded-lg px-3 py-2 mt-3 inline-flex items-center gap-1.5">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>
                    Добавьте хотя бы 1 источник
                  </p>
                </div>

                <!-- State 1-2: has sources, show prompt or idle -->
                <div v-else>
                  <!-- State 1 warning note -->
                  <p v-if="sectionDraftState(heading.section_id) === 1" class="text-[11px] text-amber-700 bg-[var(--color-warn-bg)] border border-amber-200 rounded-lg px-3 py-2 mb-3 flex items-start gap-1.5">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mt-0.5 flex-shrink-0"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                    Мало источников ({{ sectionCounts[heading.section_id] ?? 0 }}/{{ MIN_SOURCES_WARN }}) — черновик может быть поверхностным
                  </p>

                  <!-- State 2 accepted note -->
                  <p v-if="cardStates[heading.section_id] === 'accepted'" class="text-[11px] text-[var(--color-ok)] bg-[var(--color-ok-bg)] border border-green-200 rounded-lg px-3 py-2 mb-3 flex items-center gap-1.5">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                    Черновик сохранён. Можно перегенерировать.
                  </p>

                  <!-- Prompt box (shows when state=prompt or always for state 2) -->
                  <div :id="`prompt-${heading.section_id}`" v-if="cardStates[heading.section_id] === 'prompt'" class="bg-[#fafaf9] border border-[var(--color-rule)] rounded-lg px-3.5 py-3 mb-3">
                    <label class="text-[10px] font-semibold text-[var(--color-ink-muted)] uppercase tracking-[0.5px] block mb-2">Запрос к черновику</label>
                    <!-- Preset chips -->
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
                    <!-- Textarea + submit -->
                    <div class="flex gap-2 items-end">
                      <textarea
                        v-model="cardPromptText[heading.section_id]"
                        class="flex-1 font-sans text-[13px] border border-[var(--color-rule)] rounded-md px-2.5 py-1.5 resize-none bg-white text-[var(--color-ink)] focus:outline-none focus:border-[var(--color-accent)] min-h-[36px] max-h-24"
                        placeholder="Уточнения к черновику (опционально)…"
                        rows="2"
                      />
                      <button
                        @click="runGenerate(heading.section_id)"
                        class="flex-shrink-0 bg-[var(--color-accent)] text-white border-none rounded-md px-3.5 py-1.5 text-[12px] font-medium cursor-pointer hover:bg-[var(--color-accent-deep)] whitespace-nowrap"
                      >
                        Написать черновик →
                      </button>
                    </div>
                    <button @click="cardStates[heading.section_id] = 'idle'" class="text-[11px] text-[var(--color-ink-muted)] mt-1.5 hover:text-[var(--color-ink)]">Отмена</button>
                  </div>

                  <!-- Generate button (idle / accepted) -->
                  <button
                    v-if="cardStates[heading.section_id] !== 'prompt'"
                    @click="openPrompt(heading.section_id)"
                    :class="[
                      'inline-flex items-center gap-1.5 text-white border-none rounded-lg px-4 py-2 text-[13px] font-medium cursor-pointer transition-colors',
                      getDraftButton(heading.section_id).cls
                    ]"
                    :disabled="getDraftButton(heading.section_id).disabled"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                    {{ getDraftButton(heading.section_id).label }}
                  </button>
                </div>

                <!-- Op-btns row -->
                <div class="flex flex-wrap gap-1.5 pt-3 mt-3 border-t border-[var(--color-rule-light)]">
                  <!-- Primary: Close gaps -->
                  <RouterLink
                    :to="`/${projectId}/library`"
                    class="inline-flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-md border-[var(--color-accent)] text-white bg-[var(--color-accent)] hover:bg-[var(--color-accent-deep)] transition-colors"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                    Закрыть пробелы
                  </RouterLink>
                  <!-- Secondary: Research -->
                  <RouterLink
                    :to="`/${projectId}/research/${heading.section_id}`"
                    class="inline-flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-md border border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] hover:text-[var(--color-ink)] transition-colors"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
                    Исследовать
                  </RouterLink>
                  <!-- Edit raw -->
                  <RouterLink
                    :to="`/${projectId}/edit/${activeFile}?section=${heading.section_id}`"
                    class="inline-flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-md border border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] hover:text-[var(--color-ink)] transition-colors"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    Редактировать
                  </RouterLink>
                </div>
              </div>

              <!-- STATE: generating -->
              <div v-else-if="cardStates[heading.section_id] === 'generating'" class="flex flex-col items-center py-7 gap-3">
                <div class="w-7 h-7 rounded-full border-[2.5px] border-[var(--color-rule)] border-t-amber-500 animate-spin" />
                <p class="text-[13px] text-[var(--color-ink-muted)]">Генерация черновика…</p>
                <p class="text-[11px] font-mono text-[var(--color-ink-muted)] opacity-60">{{ heading.section_id }}</p>
              </div>

              <!-- STATE: diff -->
              <div v-else-if="cardStates[heading.section_id] === 'diff'">
                <!-- Diff header -->
                <div class="flex items-center gap-2 px-4 py-2 bg-[var(--color-ok-bg)] border-b border-green-200 text-[12px] text-[var(--color-ok)]">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                  Черновик готов
                  <span class="flex-1 text-[11px] text-[var(--color-ink-muted)] italic ml-1">
                    {{ cardSelectedPreset[heading.section_id] ? `стиль: ${cardSelectedPreset[heading.section_id]}` : '' }}
                  </span>
                </div>
                <!-- Before / after columns -->
                <div class="grid grid-cols-2 divide-x divide-[var(--color-rule-light)]">
                  <div class="px-4 py-3 bg-[#fff8f8]">
                    <div class="text-[10px] font-semibold uppercase tracking-[0.5px] text-[var(--color-err)] mb-2">Было</div>
                    <p class="text-[13px] text-[var(--color-ink-muted)] leading-relaxed italic">
                      {{ cardGenBefore[heading.section_id] || '(пустой раздел)' }}
                    </p>
                  </div>
                  <div class="px-4 py-3 bg-[#f8fff9]">
                    <div class="text-[10px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ok)] mb-2">Станет</div>
                    <p class="text-[13px] text-[var(--color-ink)] leading-relaxed whitespace-pre-wrap">
                      {{ cardGenResult[heading.section_id] }}
                    </p>
                  </div>
                </div>
                <!-- Diff actions -->
                <div class="flex items-center gap-2 px-4 py-2.5 border-t border-[var(--color-rule-light)] bg-white">
                  <button
                    @click="acceptDraft(heading.section_id)"
                    class="inline-flex items-center gap-1.5 bg-[var(--color-ok)] text-white border-none rounded-md px-3.5 py-1.5 text-[12px] font-medium cursor-pointer hover:bg-green-700 transition-colors"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                    Принять
                  </button>
                  <button
                    @click="rejectDraft(heading.section_id)"
                    class="inline-flex items-center gap-1.5 bg-transparent text-[var(--color-err)] border border-red-200 rounded-md px-3.5 py-1.5 text-[12px] cursor-pointer hover:bg-[var(--color-err-bg)] transition-colors"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    Отклонить
                  </button>
                  <button
                    @click="editPrompt(heading.section_id)"
                    class="inline-flex items-center gap-1.5 bg-transparent text-[var(--color-ink-muted)] border border-[var(--color-rule)] rounded-md px-3 py-1.5 text-[12px] cursor-pointer hover:bg-[var(--color-rule-light)] transition-colors"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    Изменить запрос
                  </button>
                </div>
              </div>

            </template>
          </div>
        </template>
      </main>
    </div>

    <!-- ── Toast ────────────────────────────────────────────────────────── -->
    <div :class="['fixed bottom-5 right-5 bg-[var(--color-ink)] text-white px-4 py-2 rounded-lg text-[13px] shadow-lg transition-all duration-250 pointer-events-none z-50', toastVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10']">
      {{ toastMsg }}
    </div>
  </div>
</template>
