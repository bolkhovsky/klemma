<script setup lang="ts">
import { RouterView, RouterLink, useRoute, useRouter } from 'vue-router'
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { auth, usage } from '@/api/client'
import { useProjectStore } from '@/stores/project'
import FileBrowser from '@/components/FileBrowser.vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const userName = ref('')
const userInitials = ref('?')
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
    const modules = ['feed', 'library', 'health', 'outline', 'coverage', 'research', 'write']
    const suffix = route.path.slice(currentParam.length + 2)
    const module = modules.find(m => suffix === m || suffix.startsWith(m + '/')) ?? 'write'
    router.push(`/${id}/${module}`)
  } else {
    router.push(`/${id}/write`)
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

onMounted(async () => {
  try {
    const [me, bal] = await Promise.all([auth.me(), usage.me()])
    userName.value = me.name ?? me.email.split('@')[0]
    const parts = userName.value.trim().split(/\s+/)
    userInitials.value = parts.length >= 2
      ? (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase()
      : userName.value.slice(0, 2).toUpperCase()
    tokenBalance.value = bal
  } catch { /* not logged in */ }
  const pid = route.params.projectId as string | undefined
  if (pid) projectStore.setActive(pid)
  await projectStore.loadProjects()
})

function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  router.push('/login')
}

// ── Create project modal ──────────────────────────────────────────────────
const showCreateModal = ref(false)
const newProjectName = ref('')
const newProjectType = ref('dissertation')
const creating = ref(false)

async function createProject() {
  if (!newProjectName.value.trim()) return
  creating.value = true
  try {
    await projectStore.createProject(newProjectName.value.trim(), newProjectType.value)
    showCreateModal.value = false
    if (projectStore.activeProjectId) router.push(`/${projectStore.activeProjectId}/outline`)
    newProjectName.value = ''
    newProjectType.value = 'dissertation'
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
        :to="projectId ? `/${projectId}/write` : '/library'"
        class="font-display font-bold tracking-[-0.4px] text-[15px] no-underline"
        style="color: var(--color-ink, #1a1a2e)"
      >k<span style="color: var(--color-accent, #0d7377)">lemma</span></RouterLink>

      <!-- Project switcher -->
      <div class="relative" @click.stop>
        <button
          @click="showProjectDropdown = !showProjectDropdown"
          class="inline-flex items-center gap-1.5 text-[13px] font-medium rounded-md px-2.5 py-1 border-none bg-transparent cursor-pointer transition-colors"
          style="color: var(--color-ink-2, #3d3d5c)"
        >
          {{ projectName || 'Проект' }}
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
            class="flex items-center gap-2.5 px-2.5 py-[7px] rounded-md text-[13px] cursor-pointer transition-colors hover:bg-[var(--color-rule-light,#f0ede8)]"
            :class="project.project_id === projectId ? 'font-medium' : ''"
            :style="project.project_id === projectId ? 'color: var(--color-accent-deep, #065a5e)' : 'color: var(--color-ink-muted, #6b6b8a)'"
          >
            <span class="w-[7px] h-[7px] rounded-full flex-shrink-0" :style="project.project_id === projectId ? 'background: var(--color-ok, #2d6a4f)' : 'background: var(--color-rule, #e8e5df)'" />
            {{ project.name }}
          </div>
          <div style="border-top: 1px solid var(--color-rule-light, #f0ede8); margin-top: 4px; padding-top: 4px">
            <div
              @click="showCreateModal = true; showProjectDropdown = false"
              class="flex items-center gap-2 px-2.5 py-[7px] rounded-md text-[13px] cursor-pointer transition-colors hover:bg-[var(--color-rule-light,#f0ede8)]"
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

        <!-- FileBrowser: project files -->
        <FileBrowser
          v-if="effectiveProjectId"
          :projectId="effectiveProjectId"
          :activeFile="null"
          @select="(f: string) => router.push({ name: 'write', params: { projectId: effectiveProjectId }, query: { file: f } })"
        />
        <div v-else class="px-3.5 py-3 text-[12px] italic" style="color: var(--color-ink-muted, #6b6b8a)">
          <div v-if="projectStore.loading">Загрузка…</div>
          <div v-else>Выберите проект</div>
        </div>

        <div class="flex-1" />

        <!-- App nav -->
        <div style="border-top: 1px solid var(--color-rule, #e8e5df); padding: 8px 0 4px">
          <RouterLink
            v-if="effectiveProjectId"
            :to="`/${effectiveProjectId}/library`"
            class="flex items-center gap-2 px-3.5 py-1.5 text-[14px] no-underline transition-colors"
            style="color: var(--color-ink-2, #3d3d5c)"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style="flex-shrink:0; color: var(--color-ink-muted, #9898b0)">
              <path d="M2 2h6l3 3v7a1 1 0 01-1 1H3a1 1 0 01-1-1V3a1 1 0 011-1z" stroke="currentColor" stroke-width="1.3"/>
              <path d="M8 2v3h3" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
              <path d="M4 7.5h6M4 9.5h4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
            </svg>
            Библиотека
          </RouterLink>
        </div>

        <!-- User profile -->
        <div style="border-top: 1px solid var(--color-rule, #e8e5df); padding: 10px 12px">
          <div class="flex items-center gap-2 mb-2 rounded-lg px-[2px] py-1 cursor-pointer" @click="logout" title="Выйти">
            <div class="w-[30px] h-[30px] rounded-full flex-shrink-0 flex items-center justify-center text-[13px] font-bold text-white" style="background: linear-gradient(135deg, #6366f1 0%, #2563eb 100%)">{{ userInitials }}</div>
            <div class="flex-1 min-w-0">
              <div class="text-[13px] font-semibold truncate" style="color: var(--color-ink, #1a1a2e)">{{ userName || '…' }}</div>
              <div class="inline-flex items-center gap-0.5 text-[11px] font-semibold rounded px-[5px] py-[1px] mt-0.5" style="color: #7c3aed; background: #f3e8ff; border: 1px solid #e9d5ff">
                <svg width="9" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"/></svg>
                Pro
              </div>
            </div>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style="flex-shrink:0; color: var(--color-ink-muted, #6b6b8a)"><path d="M4.5 3l3 3-3 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </div>
          <template v-if="tokenBalance && tokenBalance.total_granted > 0">
            <div class="flex justify-between items-center mb-1" style="font-size: 11px; color: var(--color-ink-muted, #6b6b8a)">
              <span>Кредиты</span>
              <strong style="font-family: var(--font-mono, monospace); color: var(--color-ink-2, #3d3d5c)">{{ tokenBalance.remaining.toLocaleString() }}</strong>
            </div>
            <div class="rounded-full overflow-hidden" style="height: 4px; background: var(--color-rule, #e8e5df); margin-bottom: 3px">
              <div class="h-full rounded-full transition-all" :style="{ width: `${100 - tokenPercent}%`, background: tokenBarLow ? 'linear-gradient(90deg, #f59e0b, #ef4444)' : 'linear-gradient(90deg, #6366f1, #2563eb)' }" />
            </div>
            <div class="text-right" style="font-size: 11px; color: var(--color-ink-muted, #6b6b8a)">из {{ tokenBalance.total_granted.toLocaleString() }} в месяц</div>
          </template>
        </div>
      </nav>

      <!-- ── Main content ─────────────────────────────────────────────── -->
      <div class="flex-1 flex overflow-hidden">
        <RouterView :key="routeKey" />
      </div>
    </div>
  </div>

  <!-- Create project modal -->
  <Teleport to="body">
    <div v-if="showCreateModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="showCreateModal = false">
      <div class="w-full max-w-sm rounded-xl border bg-white p-6 shadow-xl" style="border-color: var(--color-rule, #e8e5df)">
        <h2 class="mb-4 text-lg font-semibold" style="color: var(--color-ink, #1a1a2e)">Новый проект</h2>
        <div class="space-y-3">
          <div>
            <label class="mb-1 block text-xs font-medium" style="color: var(--color-ink-muted, #6b6b8a)">Название</label>
            <input v-model="newProjectName" type="text" placeholder="Моя диссертация" class="w-full rounded-md border px-3 py-2 text-sm focus:outline-none" style="border-color: var(--color-rule, #e8e5df); color: var(--color-ink, #1a1a2e)" @keydown.enter="createProject" autofocus />
          </div>
          <div>
            <label class="mb-1 block text-xs font-medium" style="color: var(--color-ink-muted, #6b6b8a)">Тип</label>
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
          <button @click="createProject" :disabled="!newProjectName.trim() || creating" class="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50 disabled:cursor-not-allowed" style="background: var(--color-accent, #0d7377)">{{ creating ? 'Создание…' : 'Создать' }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
