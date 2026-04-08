<script setup lang="ts">
import { RouterView, RouterLink, useRoute, useRouter } from 'vue-router'
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { auth, usage, drafts } from '@/api/client'
import { useProjectStore } from '@/stores/project'
// FileBrowser removed — library-first pivot

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const userName = ref('')
const userInitials = ref('?')
const profileLoaded = ref(false)
const tokenBalance = ref<{ total_granted: number; total_used: number; remaining: number } | null>(null)

const isPublicRoute = computed(() =>
  ['landing', 'login', 'register'].includes(route.name as string),
)
const isStandaloneRoute = computed(() => route.meta.standalone === true)

const projectId = computed(() => route.params.projectId as string | undefined)
const effectiveProjectId = computed(() => projectId.value || projectStore.activeProjectId || null)
const projectName = computed(() => projectStore.activeProject?.name ?? '')

// ── Project dropdown ──────────────────────────────────────────────────────
const showProjectDropdown = ref(false)
function onDocClick() { showProjectDropdown.value = false }
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

function switchProject(id: string) {
  projectStore.setActive(id)
  const currentParam = route.params.projectId as string | undefined
  if (currentParam) {
    const modules = ['feed', 'library', 'health', 'outline', 'coverage', 'research', 'map', 'write']
    const suffix = route.path.slice(currentParam.length + 2)
    const module = modules.find(m => suffix === m || suffix.startsWith(m + '/')) ?? 'library'
    router.push(`/${id}/${module}`)
  } else {
    router.push(`/${id}/library`)
  }
}

watch(() => route.params.projectId, (id) => {
  if (typeof id === 'string' && id !== projectStore.activeProjectId) projectStore.setActive(id)
})

const tokenPercent = computed(() => {
  if (!tokenBalance.value || !tokenBalance.value.total_granted) return 0
  return Math.round((tokenBalance.value.total_used / tokenBalance.value.total_granted) * 100)
})
const tokenBarLow = computed(() => tokenPercent.value >= 80)

async function loadProfile() {
  profileLoaded.value = false
  try {
    const [me, bal] = await Promise.all([auth.me(), usage.me()])
    userName.value = me.name ?? me.email.split('@')[0]
    const parts = userName.value.trim().split(/\s+/)
    userInitials.value = parts.length >= 2
      ? (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase()
      : userName.value.slice(0, 2).toUpperCase()
    tokenBalance.value = bal
  } catch { /* not logged in */ }
  finally { profileLoaded.value = true }
  const pid = route.params.projectId as string | undefined
  if (pid) projectStore.setActive(pid)
  await projectStore.loadProjects()
}

onMounted(loadProfile)

// Re-fetch profile + projects after login (route leaves login/register → app)
watch(isPublicRoute, (isPublic, wasPublic) => {
  if (wasPublic && !isPublic) loadProfile()
})

function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  projectStore.$reset()
  router.push('/login')
}

// ── Create project wizard ─────────────────────────────────────────────────
const showCreateModal = ref(false)
const wizardStep = ref(1)
const newProjectName = ref('')
const newProjectType = ref('dissertation')
const creating = ref(false)

// Step 2: outline
type OutlineMode = 'preset' | 'paste' | 'skip'
const outlineMode = ref<OutlineMode>('preset')
const outlinePasteText = ref('')

interface PresetSection { id: string; name: string }

const PRESETS: Record<string, PresetSection[]> = {
  dissertation: [
    { id: 'intro', name: 'Введение' },
    { id: '1', name: 'Глава 1. Обзор литературы' },
    { id: '1.1', name: 'Состояние проблемы' },
    { id: '1.2', name: 'Анализ существующих подходов' },
    { id: '1.3', name: 'Выводы по главе 1' },
    { id: '2', name: 'Глава 2. Материалы и методы' },
    { id: '2.1', name: 'Объект и предмет исследования' },
    { id: '2.2', name: 'Методика исследования' },
    { id: '2.3', name: 'Выводы по главе 2' },
    { id: '3', name: 'Глава 3. Результаты' },
    { id: '3.1', name: 'Основные результаты' },
    { id: '3.2', name: 'Анализ результатов' },
    { id: '3.3', name: 'Выводы по главе 3' },
    { id: 'conclusion', name: 'Заключение' },
  ],
  paper: [
    { id: 'intro', name: 'Introduction' },
    { id: '1', name: 'Related Work' },
    { id: '2', name: 'Methodology' },
    { id: '3', name: 'Experiments' },
    { id: '4', name: 'Results' },
    { id: '5', name: 'Discussion' },
    { id: 'conclusion', name: 'Conclusion' },
  ],
  thesis: [
    { id: 'intro', name: 'Введение' },
    { id: '1', name: 'Глава 1. Теоретическая часть' },
    { id: '1.1', name: 'Обзор литературы' },
    { id: '1.2', name: 'Теоретические основы' },
    { id: '2', name: 'Глава 2. Практическая часть' },
    { id: '2.1', name: 'Методология' },
    { id: '2.2', name: 'Результаты' },
    { id: '3', name: 'Глава 3. Обсуждение' },
    { id: 'conclusion', name: 'Заключение' },
  ],
}

const activePreset = computed((): PresetSection[] => PRESETS[newProjectType.value] ?? PRESETS.dissertation!)

function parseOutlineText(text: string): PresetSection[] {
  const sections: PresetSection[] = []
  let chapterNum = 0
  let subNum = 0
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    // Detect chapter-level: starts with "Глава", "Chapter", number, or ##
    const isChapter = /^(глава|chapter|\d+\.|##\s)/i.test(line)
    if (isChapter) {
      chapterNum++
      subNum = 0
      sections.push({ id: String(chapterNum), name: line.replace(/^##\s*/, '') })
    } else if (line.toLowerCase() === 'введение' || line.toLowerCase() === 'introduction') {
      sections.push({ id: 'intro', name: line })
    } else if (line.toLowerCase() === 'заключение' || line.toLowerCase() === 'conclusion') {
      sections.push({ id: 'conclusion', name: line })
    } else {
      // Subsection
      subNum++
      const id = chapterNum > 0 ? `${chapterNum}.${subNum}` : `0.${subNum}`
      sections.push({ id, name: line.replace(/^[-•*]\s*/, '').replace(/^###\s*/, '') })
    }
  }
  return sections
}

function openWizard() {
  wizardStep.value = 1
  newProjectName.value = ''
  newProjectType.value = 'dissertation'
  outlineMode.value = 'preset'
  outlinePasteText.value = ''
  showCreateModal.value = true
}

function wizardNext() {
  if (wizardStep.value === 1 && newProjectName.value.trim()) wizardStep.value = 2
}

async function createProject() {
  if (!newProjectName.value.trim()) return
  creating.value = true
  try {
    const project = await projectStore.createProject(newProjectName.value.trim(), newProjectType.value)
    // Save outline
    let sections: PresetSection[] = []
    if (outlineMode.value === 'preset') sections = activePreset.value
    else if (outlineMode.value === 'paste' && outlinePasteText.value.trim()) sections = parseOutlineText(outlinePasteText.value)

    if (sections.length > 0 && projectStore.activeProjectId) {
      try {
        await projectStore.updateOutline(sections)
        // Scaffold multi-file draft structure from outline (ADR-016)
        await drafts.scaffold(projectStore.activeProjectId)
      } catch { /* outline/drafts scaffold failed — not critical */ }
    }

    showCreateModal.value = false
    if (projectStore.activeProjectId) router.push(`/${projectStore.activeProjectId}/library`)
  } finally { creating.value = false }
}

const routeKey = computed(() => (route.params.projectId as string) ?? route.path)
</script>

<template>
  <!-- Public / standalone routes: no chrome -->
  <RouterView v-if="isPublicRoute || isStandaloneRoute" :key="routeKey" />

  <!-- App shell -->
  <div v-else class="flex flex-col h-screen" style="background: var(--color-paper-bg, #faf9f7)">

    <!-- ── Topbar ─────────────────────────────────────────────────────── -->
    <header class="h-12 flex-shrink-0 bg-white flex items-center gap-3 px-5 z-20" style="border-bottom: 1px solid var(--color-rule, #e8e5df)">
      <RouterLink
        :to="projectId ? `/${projectId}/library` : '/library'"
        class="font-display font-bold tracking-[-0.4px] text-base no-underline"
        style="color: var(--color-ink, #1a1a2e)"
      >k<span style="color: var(--color-accent, #0d7377)">lemma</span></RouterLink>

      <!-- Project switcher -->
      <div class="relative" @click.stop>
        <button
          @click="showProjectDropdown = !showProjectDropdown"
          class="inline-flex items-center gap-1.5 text-sm font-medium rounded-md px-2.5 py-1 border-none bg-transparent cursor-pointer transition-colors"
          style="color: var(--color-ink-2, #3d3d5c)"
        >
          <span v-if="projectStore.loading && !projectName" class="inline-block h-3 w-16 rounded animate-pulse" style="background: var(--color-rule, #e8e5df)" />
          <template v-else>{{ projectName || 'Проект' }}</template>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 4.5l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <div
          v-if="showProjectDropdown"
          class="absolute top-full left-0 mt-1.5 rounded-[9px] p-[5px] z-50"
          style="background: white; border: 1px solid var(--color-rule, #e8e5df); min-width: 200px; box-shadow: 0 8px 24px rgba(0,0,0,0.11)"
        >
          <div
            v-for="project in projectStore.projects" :key="project.project_id"
            @click="switchProject(project.project_id); showProjectDropdown = false"
            class="flex items-center gap-2.5 px-2.5 py-[7px] rounded-md text-sm cursor-pointer transition-colors hover:bg-[var(--color-rule-light,#f0ede8)]"
            :class="project.project_id === projectId ? 'font-medium' : ''"
            :style="project.project_id === projectId ? 'color: var(--color-accent-deep, #065a5e)' : 'color: var(--color-ink-muted, #6b6b8a)'"
          >
            <span class="w-[7px] h-[7px] rounded-full flex-shrink-0" :style="project.project_id === projectId ? 'background: var(--color-ok, #2d6a4f)' : 'background: var(--color-rule, #e8e5df)'" />
            {{ project.name }}
          </div>
          <div style="border-top: 1px solid var(--color-rule-light, #f0ede8); margin-top: 4px; padding-top: 4px">
            <div
              @click="openWizard(); showProjectDropdown = false"
              class="flex items-center gap-2 px-2.5 py-[7px] rounded-md text-sm cursor-pointer transition-colors hover:bg-[var(--color-rule-light,#f0ede8)]"
              style="color: var(--color-ink-muted, #6b6b8a)"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" class="w-3 h-3 flex-shrink-0"><path d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z"/></svg>
              Новый проект
            </div>
          </div>
        </div>
      </div>

      <div class="flex-1" />

      <!-- Bell -->
      <RouterLink
        v-if="projectId"
        :to="`/${projectId}/feed`"
        class="relative w-8 h-8 rounded-[7px] flex items-center justify-center border transition-colors no-underline"
        style="border-color: var(--color-rule, #e8e5df); color: var(--color-ink-muted, #6b6b8a)"
        title="Лента событий"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M8 1.5a5 5 0 0 1 5 5v2.5l1 2H2l1-2V6.5a5 5 0 0 1 5-5z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
          <path d="M6.5 13.5a1.5 1.5 0 0 0 3 0" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
        </svg>
      </RouterLink>
    </header>

    <!-- ── Workspace ──────────────────────────────────────────────────── -->
    <div class="flex flex-1 overflow-hidden">

      <!-- ── Sidebar ──────────────────────────────────────────────────── -->
      <nav class="flex-shrink-0 flex flex-col overflow-y-auto" style="width: 176px; background: white; border-right: 1px solid var(--color-rule, #e8e5df)">

        <!-- Nav links -->
        <div class="py-2">
          <div class="px-3.5 py-1 text-[13px] font-semibold uppercase tracking-wide" style="color: var(--color-ink-muted, #6b6b8a)">Навигация</div>
          <RouterLink
            v-if="effectiveProjectId"
            :to="`/${effectiveProjectId}/library`"
            class="flex items-center gap-2 px-3.5 py-2 text-[14px] no-underline transition-colors rounded-md mx-1.5"
            :class="route.name === 'library' || route.name === 'source' || route.name === 'fragment-review' ? 'font-semibold' : ''"
            :style="route.name === 'library' || route.name === 'source' || route.name === 'fragment-review' ? 'color: var(--color-accent-deep, #065a5e); background: var(--color-accent-pale, #e6f3f3)' : 'color: var(--color-ink-2, #3d3d5c)'"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style="flex-shrink:0" :style="route.name === 'library' || route.name === 'source' || route.name === 'fragment-review' ? 'color: var(--color-accent, #0d7377)' : 'color: var(--color-ink-muted, #9898b0)'">
              <path d="M2 2h6l3 3v7a1 1 0 01-1 1H3a1 1 0 01-1-1V3a1 1 0 011-1z" stroke="currentColor" stroke-width="1.3"/>
              <path d="M8 2v3h3" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
            </svg>
            Библиотека
          </RouterLink>
          <RouterLink
            v-if="effectiveProjectId"
            :to="`/${effectiveProjectId}/map`"
            class="flex items-center gap-2 px-3.5 py-2 text-[14px] no-underline transition-colors rounded-md mx-1.5"
            :class="route.name === 'map' ? 'font-semibold' : ''"
            :style="route.name === 'map' ? 'color: var(--color-accent-deep, #065a5e); background: var(--color-accent-pale, #e6f3f3)' : 'color: var(--color-ink-2, #3d3d5c)'"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style="flex-shrink:0" :style="route.name === 'map' ? 'color: var(--color-accent, #0d7377)' : 'color: var(--color-ink-muted, #9898b0)'">
              <path d="M1 3l4-1.5 4 1.5 4-1.5v9l-4 1.5-4-1.5-4 1.5z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
              <path d="M5 1.5v9M9 3.5v9" stroke="currentColor" stroke-width="1.3"/>
            </svg>
            Карта
          </RouterLink>
          <div v-if="!effectiveProjectId" class="px-3.5 py-3 text-[13px] italic" style="color: var(--color-ink-muted, #6b6b8a)">
            <div v-if="projectStore.loading">Загрузка…</div>
            <div v-else>Выберите проект</div>
          </div>
        </div>

        <div class="flex-1" />

        <!-- User profile -->
        <div style="border-top: 1px solid var(--color-rule, #e8e5df); padding: 10px 12px">
          <!-- Skeleton while loading -->
          <template v-if="!profileLoaded">
            <div class="flex items-center gap-2 mb-2 px-[2px] py-1">
              <div class="w-[30px] h-[30px] rounded-full flex-shrink-0 animate-pulse" style="background: var(--color-rule, #e8e5df)" />
              <div class="flex-1 space-y-1.5">
                <div class="h-3 w-20 rounded animate-pulse" style="background: var(--color-rule, #e8e5df)" />
                <div class="h-3 w-10 rounded animate-pulse" style="background: var(--color-rule, #e8e5df)" />
              </div>
            </div>
            <div class="h-1 rounded-full animate-pulse mb-1" style="background: var(--color-rule, #e8e5df)" />
            <div class="h-2.5 w-16 rounded animate-pulse ml-auto" style="background: var(--color-rule, #e8e5df)" />
          </template>
          <!-- Loaded -->
          <template v-else>
            <div class="flex items-center gap-2 mb-2 rounded-lg px-[2px] py-1">
              <div class="w-[30px] h-[30px] rounded-full flex-shrink-0 flex items-center justify-center text-[14px] font-bold text-white" style="background: linear-gradient(135deg, #6366f1 0%, #2563eb 100%)">{{ userInitials }}</div>
              <div class="flex-1 min-w-0">
                <div class="text-[14px] font-semibold truncate" style="color: var(--color-ink, #1a1a2e)">{{ userName || '…' }}</div>
                <div class="inline-flex items-center gap-0.5 text-[12px] font-semibold rounded px-[5px] py-[1px] mt-0.5" style="color: #7c3aed; background: #f3e8ff; border: 1px solid #e9d5ff">
                  <svg width="9" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"/></svg>
                  Pro
                </div>
              </div>
              <button @click="logout" title="Выйти" class="w-6 h-6 flex items-center justify-center rounded-md transition-colors hover:bg-[var(--color-err-bg,#fff0f0)] flex-shrink-0" style="color: var(--color-ink-muted, #6b6b8a)">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
              </button>
            </div>
            <template v-if="tokenBalance && tokenBalance.total_granted > 0">
              <div class="flex justify-between items-center mb-1" style="font-size: 12px; color: var(--color-ink-muted, #6b6b8a)">
                <span>Кредиты</span>
                <strong style="font-family: var(--font-mono, monospace); color: var(--color-ink-2, #3d3d5c)">{{ tokenBalance.remaining.toLocaleString() }}</strong>
              </div>
              <div
                class="rounded-full overflow-hidden cursor-help"
                style="height: 4px; background: var(--color-rule, #e8e5df); margin-bottom: 3px"
                :title="`${tokenBalance.remaining.toLocaleString()} из ${tokenBalance.total_granted.toLocaleString()} кредитов · ~${Math.floor(tokenBalance.remaining / 12000)} статей осталось · обновляются 1-го числа`"
              >
                <div class="h-full rounded-full transition-all" :style="{ width: `${100 - tokenPercent}%`, background: tokenBarLow ? 'linear-gradient(90deg, #f59e0b, #ef4444)' : 'linear-gradient(90deg, #6366f1, #2563eb)' }" />
              </div>
              <div class="text-right" style="font-size: 12px; color: var(--color-ink-muted, #6b6b8a)">из {{ tokenBalance.total_granted.toLocaleString() }} в месяц</div>
            </template>
          </template>
        </div>
      </nav>

      <!-- ── Main content ─────────────────────────────────────────────── -->
      <div class="flex-1 flex overflow-hidden">
        <RouterView :key="routeKey" />
      </div>
    </div>
  </div>

  <!-- Create project wizard -->
  <Teleport to="body">
    <div v-if="showCreateModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="showCreateModal = false">
      <div class="w-full max-w-md rounded-xl border bg-white shadow-xl" style="border-color: var(--color-rule, #e8e5df)">

        <!-- Progress bar -->
        <div class="flex gap-1 px-6 pt-5 mb-4">
          <div class="h-1 flex-1 rounded-full transition-all" :style="{ background: 'var(--color-accent, #0d7377)' }" />
          <div class="h-1 flex-1 rounded-full transition-all" :style="{ background: wizardStep >= 2 ? 'var(--color-accent, #0d7377)' : 'var(--color-rule, #e8e5df)' }" />
        </div>

        <!-- Step 1: Name + Type -->
        <div v-if="wizardStep === 1" class="px-6 pb-6">
          <h2 class="text-lg font-semibold mb-1" style="color: var(--color-ink, #1a1a2e)">Новый проект</h2>
          <p class="text-sm mb-4" style="color: var(--color-ink-muted, #6b6b8a)">Шаг 1 из 2 — название и тип работы</p>
          <div class="space-y-3">
            <div>
              <label class="mb-1 block text-sm font-medium" style="color: var(--color-ink-muted, #6b6b8a)">Название</label>
              <input v-model="newProjectName" type="text" placeholder="Моя диссертация" class="w-full rounded-md border px-3 py-2 text-sm focus:outline-none" style="border-color: var(--color-rule, #e8e5df); color: var(--color-ink, #1a1a2e)" @keydown.enter="wizardNext" autofocus />
            </div>
            <div>
              <label class="mb-1 block text-sm font-medium" style="color: var(--color-ink-muted, #6b6b8a)">Тип</label>
              <select v-model="newProjectType" class="w-full rounded-md border px-3 py-2 text-sm focus:outline-none" style="border-color: var(--color-rule, #e8e5df); color: var(--color-ink, #1a1a2e)">
                <option value="dissertation">Диссертация</option>
                <option value="paper">Статья</option>
                <option value="thesis">Дипломная работа</option>
                <option value="other">Другое</option>
              </select>
            </div>
          </div>
          <div class="mt-5 flex gap-2 justify-end">
            <button @click="showCreateModal = false" class="rounded-md px-4 py-2 text-sm font-medium" style="color: var(--color-ink-muted, #6b6b8a)">Отмена</button>
            <button @click="wizardNext" :disabled="!newProjectName.trim()" class="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50 disabled:cursor-not-allowed" style="background: var(--color-accent, #0d7377)">Далее</button>
          </div>
        </div>

        <!-- Step 2: Outline -->
        <div v-else class="px-6 pb-6">
          <h2 class="text-lg font-semibold mb-1" style="color: var(--color-ink, #1a1a2e)">Структура работы</h2>
          <p class="text-sm mb-4" style="color: var(--color-ink-muted, #6b6b8a)">Шаг 2 из 2 — Klemma распределит источники по разделам</p>

          <!-- Mode selector -->
          <div class="flex gap-1.5 mb-4 rounded-lg p-1" style="background: var(--color-rule-light, #f0ede8)">
            <button
              v-for="opt in ([
                { key: 'preset', label: 'Шаблон' },
                { key: 'paste', label: 'Вставить' },
                { key: 'skip', label: 'Пропустить' },
              ] as { key: OutlineMode; label: string }[])"
              :key="opt.key"
              @click="outlineMode = opt.key"
              :class="['flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-all',
                outlineMode === opt.key ? 'bg-white shadow-sm text-[var(--color-ink)]' : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]']"
            >{{ opt.label }}</button>
          </div>

          <!-- Preset mode -->
          <div v-if="outlineMode === 'preset'">
            <div class="text-sm font-medium mb-2" style="color: var(--color-ink-muted, #6b6b8a)">
              Стандартная структура для типа «{{ { dissertation: 'Диссертация', paper: 'Статья', thesis: 'Дипломная работа' }[newProjectType] || newProjectType }}»
            </div>
            <div class="rounded-lg border overflow-hidden max-h-52 overflow-y-auto" style="border-color: var(--color-rule, #e8e5df)">
              <div
                v-for="sec in activePreset" :key="sec.id"
                :class="['px-3 py-1.5 text-sm border-b last:border-b-0',
                  sec.id.includes('.') ? 'pl-7 text-[var(--color-ink-muted)]' : 'font-medium text-[var(--color-ink)]']"
                style="border-color: var(--color-rule-light, #f0ede8)"
              >
                <span class="font-mono text-[12px] mr-2" style="color: var(--color-accent, #0d7377)">{{ sec.id }}</span>
                {{ sec.name }}
              </div>
            </div>
            <p class="text-[12px] mt-2" style="color: var(--color-ink-muted, #6b6b8a)">Структуру можно изменить позже</p>
          </div>

          <!-- Paste mode -->
          <div v-else-if="outlineMode === 'paste'">
            <div class="text-sm font-medium mb-2" style="color: var(--color-ink-muted, #6b6b8a)">
              Вставьте оглавление — каждый раздел на новой строке
            </div>
            <textarea
              v-model="outlinePasteText"
              rows="8"
              placeholder="Введение
Глава 1. Обзор литературы
  1.1 Состояние проблемы
  1.2 Анализ подходов
Глава 2. Методы
  2.1 Методика
Заключение"
              class="w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none resize-none"
              style="border-color: var(--color-rule, #e8e5df); color: var(--color-ink, #1a1a2e)"
            />
            <p v-if="outlinePasteText.trim()" class="text-[12px] mt-1" style="color: var(--color-ok, #2d6a4f)">
              Распознано разделов: {{ parseOutlineText(outlinePasteText).length }}
            </p>
          </div>

          <!-- Skip mode -->
          <div v-else class="rounded-lg border px-4 py-6 text-center" style="border-color: var(--color-rule, #e8e5df)">
            <div class="text-3xl mb-2">📝</div>
            <p class="text-sm" style="color: var(--color-ink-muted, #6b6b8a)">Без структуры — можно добавить позже.<br/>Источники не будут привязаны к разделам.</p>
          </div>

          <div class="mt-5 flex gap-2 justify-between">
            <button @click="wizardStep = 1" class="rounded-md px-4 py-2 text-sm font-medium" style="color: var(--color-ink-muted, #6b6b8a)">Назад</button>
            <button @click="createProject" :disabled="creating" class="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50 disabled:cursor-not-allowed" style="background: var(--color-accent, #0d7377)">{{ creating ? 'Создание…' : 'Создать проект' }}</button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
