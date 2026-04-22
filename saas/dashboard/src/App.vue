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

  <!-- App shell — sidebar hosts brand / project / nav; main area is unwrapped content -->
  <div v-else class="app-shell">

    <!-- ── Sidebar ──────────────────────────────────────────────────── -->
    <aside class="app-sidebar">

      <!-- Brand -->
      <div class="sidebar-logo-row">
        <RouterLink
          :to="projectId ? `/${projectId}/library` : '/library'"
          class="sidebar-logo"
        >LitResearch</RouterLink>
      </div>

      <!-- Project switcher: labeled pill with mono uppercase label + current name + chevron -->
      <div class="project-switch-wrap" @click.stop>
        <button
          class="project-switch"
          :class="{ open: showProjectDropdown }"
          @click="showProjectDropdown = !showProjectDropdown"
        >
          <span class="project-switch-inner">
            <span class="project-switch-label">Проект</span>
            <span class="project-switch-name">
              <span v-if="projectStore.loading && !projectName" class="inline-block h-3 w-20 rounded animate-pulse" style="background: var(--color-rule)" />
              <template v-else>{{ projectName || '—' }}</template>
            </span>
          </span>
          <span class="project-switch-caret">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5l3 3 3-3"/></svg>
          </span>
        </button>
        <div v-if="showProjectDropdown" class="project-menu">
          <div class="project-menu-head">Проекты</div>
          <button
            v-for="project in projectStore.projects"
            :key="project.project_id"
            class="project-menu-item"
            :class="{ active: project.project_id === projectId }"
            @click="switchProject(project.project_id); showProjectDropdown = false"
          >
            <span class="project-menu-name">{{ project.name }}</span>
          </button>
          <div class="project-menu-divider"></div>
          <button
            class="project-menu-item new"
            @click="openWizard(); showProjectDropdown = false"
          >
            <span class="project-menu-name">+ Новый проект</span>
          </button>
        </div>
      </div>

      <!-- Nav -->
      <div class="nav-label">Навигация</div>
      <RouterLink
        v-if="effectiveProjectId"
        :to="`/${effectiveProjectId}/library`"
        class="nav-item"
        :class="{ active: route.name === 'library' || route.name === 'source' || route.name === 'fragment-review' }"
      >
        <span class="nav-icon">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M2 2h6l3 3v7a1 1 0 01-1 1H3a1 1 0 01-1-1V3a1 1 0 011-1z"/><path d="M8 2v3h3" stroke-linejoin="round"/></svg>
        </span>
        <span>Библиотека</span>
      </RouterLink>
      <RouterLink
        v-if="effectiveProjectId"
        :to="`/${effectiveProjectId}/map`"
        class="nav-item"
        :class="{ active: route.name === 'map' }"
      >
        <span class="nav-icon">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><path d="M1 3l4-1.5 4 1.5 4-1.5v9l-4 1.5-4-1.5-4 1.5z"/><path d="M5 1.5v9M9 3.5v9"/></svg>
        </span>
        <span>Карта</span>
      </RouterLink>
      <RouterLink
        v-if="projectId"
        :to="`/${projectId}/feed`"
        class="nav-item"
        :class="{ active: route.name === 'feed' }"
      >
        <span class="nav-icon">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><path d="M7 1a4.5 4.5 0 0 1 4.5 4.5v2.25l1 1.75H1.5l1-1.75V5.5A4.5 4.5 0 0 1 7 1z"/><path d="M5.75 11.5a1.3 1.3 0 0 0 2.5 0" stroke-linecap="round"/></svg>
        </span>
        <span>Лента</span>
      </RouterLink>

      <div v-if="!effectiveProjectId" class="nav-empty">
        <span v-if="projectStore.loading">Загрузка…</span>
        <span v-else>Выберите проект</span>
      </div>

      <div class="sidebar-spacer"></div>

      <!-- Credits + user (pinned to bottom) -->
      <div class="sidebar-bottom">
        <template v-if="!profileLoaded">
          <div class="flex items-center gap-2 mb-2 px-[2px] py-1">
            <div class="w-[28px] h-[28px] rounded-full flex-shrink-0 animate-pulse" style="background: var(--color-rule)" />
            <div class="flex-1 space-y-1.5">
              <div class="h-3 w-20 rounded animate-pulse" style="background: var(--color-rule)" />
              <div class="h-3 w-10 rounded animate-pulse" style="background: var(--color-rule)" />
            </div>
          </div>
        </template>
        <template v-else>
          <div v-if="tokenBalance && tokenBalance.total_granted > 0" class="credits">
            <div class="credits-row">
              <span>Кредиты</span>
              <span><span class="credits-num">{{ tokenBalance.remaining.toLocaleString() }}</span>
                <span class="credits-total"> / {{ tokenBalance.total_granted.toLocaleString() }}</span></span>
            </div>
            <div
              class="credits-bar"
              :title="`${tokenBalance.remaining.toLocaleString()} из ${tokenBalance.total_granted.toLocaleString()} кредитов · обновляются 1-го числа`"
            >
              <div class="credits-bar-fill" :class="{ low: tokenBarLow }" :style="{ width: `${100 - tokenPercent}%` }" />
            </div>
            <div class="credits-sub">в месяц · обновится 1-го</div>
          </div>
          <div class="user-row">
            <div class="user-avatar">{{ userInitials }}</div>
            <div class="user-meta">
              <span class="user-name">{{ userName || '…' }}</span>
              <span class="pro-chip">Pro</span>
            </div>
            <button class="icon-btn" @click="logout" title="Выйти" aria-label="Выйти">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
            </button>
          </div>
        </template>
      </div>
    </aside>

    <!-- ── Main area ─────────────────────────────────────────────────── -->
    <div class="app-main">
      <RouterView :key="routeKey" />
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

<style scoped>
/* App shell — sidebar + content */
.app-shell {
  display: grid;
  grid-template-columns: 232px 1fr;
  min-height: 100vh;
  background: var(--color-paper);
}

/* ── Sidebar ─────────────────────────────────────────────────────────── */
.app-sidebar {
  background: var(--color-paper-2);
  border-right: 1px solid var(--color-rule);
  display: flex;
  flex-direction: column;
  padding: 20px 14px;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}

.sidebar-logo-row {
  padding: 4px 8px 28px;
}
.sidebar-logo {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 22px;
  letter-spacing: -0.01em;
  color: var(--color-ink);
  line-height: 1;
  text-decoration: none;
}
.sidebar-logo:hover { color: var(--color-ink); }

/* Project switcher: labeled pill */
.project-switch-wrap { position: relative; margin: 0 0 22px 0; }
.project-switch {
  width: 100%;
  padding: 7px 10px;
  background: white;
  border: 1px solid var(--color-rule);
  border-radius: 4px;
  color: var(--color-ink-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: border-color .12s ease, box-shadow .12s ease;
}
.project-switch:hover { border-color: var(--color-accent-rule); }
.project-switch.open {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px var(--color-accent-tint);
}
.project-switch-inner {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  min-width: 0;
}
.project-switch-label {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--color-ink-faint);
}
.project-switch-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-switch-caret {
  color: var(--color-ink-muted);
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform .15s ease, color .15s ease;
  flex-shrink: 0;
}
.project-switch:hover .project-switch-caret { color: var(--color-ink); }
.project-switch.open .project-switch-caret {
  transform: rotate(180deg);
  color: var(--color-accent);
}

.project-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  background: white;
  border: 1px solid var(--color-rule);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(30,27,24,.08), 0 2px 6px rgba(30,27,24,.04);
  padding: 6px;
  z-index: 40;
}
.project-menu-head {
  padding: 6px 8px 4px;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--color-ink-faint);
}
.project-menu-item {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 8px;
  background: transparent;
  border: none;
  border-radius: 4px;
  font: inherit;
  color: var(--color-ink-light);
  cursor: pointer;
  text-align: left;
}
.project-menu-item:hover { background: var(--color-paper-2); }
.project-menu-item.active {
  background: var(--color-accent-tint);
  color: var(--color-accent);
}
.project-menu-item.new {
  color: var(--color-accent);
  font-weight: 500;
}
.project-menu-name { font-size: 13px; font-weight: 500; }
.project-menu-item.active .project-menu-name { color: var(--color-accent); }
.project-menu-divider {
  height: 1px;
  background: var(--color-rule);
  margin: 6px 4px;
}

/* Nav */
.nav-label {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-ink-muted);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  padding: 12px 10px 6px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border-radius: 5px;
  font-size: 13px;
  color: var(--color-ink-light);
  cursor: pointer;
  line-height: 1.4;
  text-decoration: none;
}
.nav-item:hover { background: white; }
.nav-item.active {
  background: var(--color-accent-tint);
  color: var(--color-accent);
  font-weight: 500;
}
.nav-item .nav-icon {
  width: 14px;
  color: var(--color-ink-muted);
  display: inline-flex;
  flex-shrink: 0;
}
.nav-item.active .nav-icon { color: var(--color-accent); }

.nav-empty {
  padding: 10px 12px;
  font-size: 13px;
  font-style: italic;
  color: var(--color-ink-muted);
}

.sidebar-spacer { flex: 1; }

/* Bottom: credits + user */
.sidebar-bottom {
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid var(--color-rule);
}
.credits {
  padding: 10px 8px 4px;
  font-size: 11px;
  color: var(--color-ink-muted);
}
.credits-row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}
.credits-num {
  font-family: var(--font-mono);
  color: var(--color-ink-light);
  font-weight: 500;
}
.credits-total { color: var(--color-ink-faint); }
.credits-bar {
  height: 3px;
  background: var(--color-paper-3);
  border-radius: 2px;
  overflow: hidden;
  cursor: help;
}
.credits-bar-fill {
  height: 100%;
  background: var(--color-accent);
  transition: width .2s ease;
}
.credits-bar-fill.low {
  background: linear-gradient(90deg, #f59e0b, #ef4444);
}
.credits-sub {
  font-size: 10px;
  color: var(--color-ink-faint);
  margin-top: 4px;
}

.user-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 8px 4px;
}
.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--color-accent-tint);
  color: var(--color-accent);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}
.user-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}
.user-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pro-chip {
  display: inline-block;
  padding: 1px 6px;
  background: var(--color-picked-tint);
  color: var(--color-picked);
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.02em;
  flex-shrink: 0;
}
.icon-btn {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-ink-muted);
  border-radius: 4px;
  border: none;
  background: transparent;
  cursor: pointer;
}
.icon-btn:hover {
  background: var(--color-paper-3);
  color: var(--color-ink-light);
}

/* Main area */
.app-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 100vh;
}

/* Responsive: collapse sidebar on narrow screens */
@media (max-width: 860px) {
  .app-shell { grid-template-columns: 1fr; }
  .app-sidebar {
    position: static;
    height: auto;
    border-right: none;
    border-bottom: 1px solid var(--color-rule);
  }
}
</style>
