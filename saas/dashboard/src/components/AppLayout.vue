<script setup lang="ts">
import { RouterLink, useRouter, useRoute } from 'vue-router'
import { ref, computed, onMounted, watch } from 'vue'
import { auth, usage } from '@/api/client'
import { useProjectStore } from '@/stores/project'

const router = useRouter()
const route = useRoute()
const userName = ref('')

const projectStore = useProjectStore()

// Switch project, preserve current module (outline/library/etc.)
function switchProject(projectId: string) {
  projectStore.setActive(projectId)
  const currentParam = route.params.projectId as string | undefined
  if (currentParam) {
    // Navigate to same module within new project
    const modules = ['dashboard', 'outline', 'library', 'coverage', 'research']
    const suffix = route.path.slice(currentParam.length + 2) // strip /projectId/
    const module = modules.find(m => suffix === m || suffix.startsWith(m + '/')) ?? 'outline'
    router.push(`/${projectId}/${module}`)
  } else {
    // Currently on global page — go to outline of new project
    router.push(`/${projectId}/outline`)
  }
}

// Sync store when route projectId changes (browser back/forward, direct URL)
watch(() => route.params.projectId, (id) => {
  if (typeof id === 'string' && id !== projectStore.activeProjectId) {
    projectStore.setActive(id)
  }
})

// Token balance
const tokenBalance = ref<{ total_granted: number; total_used: number; remaining: number } | null>(null)

const tokenPercent = computed(() => {
  if (!tokenBalance.value || tokenBalance.value.total_granted === 0) return 0
  return Math.round((tokenBalance.value.total_used / tokenBalance.value.total_granted) * 100)
})

const tokenColor = computed(() => {
  const p = tokenPercent.value
  if (p >= 90) return 'text-[var(--color-err)]'
  if (p >= 50) return 'text-[var(--color-warn)]'
  return 'text-[var(--color-ok)]'
})

const tokenBarColor = computed(() => {
  const p = tokenPercent.value
  if (p >= 90) return 'bg-[var(--color-err)]'
  if (p >= 50) return 'bg-[var(--color-warn)]'
  return 'bg-[var(--color-ok)]'
})

function formatTokens(n: number): string {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${Math.round(n / 1000)}K`
  return String(n)
}

onMounted(async () => {
  try {
    const [me, bal] = await Promise.all([auth.me(), usage.me()])
    userName.value = me.name ?? me.email.split('@')[0]
    tokenBalance.value = bal
  } catch {
    /* ignore */
  }

  // Sync store from URL (route params are source of truth)
  const projectId = route.params.projectId as string | undefined
  if (projectId) projectStore.setActive(projectId)

  await projectStore.loadProjects()
})

function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  router.push('/login')
}

// Create project modal
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
    if (projectStore.activeProjectId) {
      router.push(`/${projectStore.activeProjectId}/outline`)
    }
    newProjectName.value = ''
    newProjectType.value = 'dissertation'
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen bg-[var(--color-paper)]">
    <!-- Sidebar -->
    <aside class="flex w-56 flex-shrink-0 flex-col border-r border-[var(--color-rule)] bg-[var(--color-paper-white)]">
      <!-- Logo -->
      <div class="flex items-center gap-2.5 px-4 py-4 border-b border-[var(--color-rule)]">
        <div class="flex h-7 w-7 items-center justify-center rounded-md bg-[var(--color-accent)] text-white font-[var(--font-display)] text-xs font-bold tracking-tight">
          Lr
        </div>
        <span class="font-[var(--font-display)] text-base font-semibold text-[var(--color-ink)] tracking-tight">
          LitResearch
        </span>
      </div>

      <!-- Global: Моя библиотека -->
      <div class="px-2 pt-3 pb-1">
        <RouterLink
          to="/library"
          class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm font-medium transition-colors"
          :class="route.path === '/library' || route.path.startsWith('/library/')
            ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
            : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4 flex-shrink-0">
            <path d="M3.5 2A1.5 1.5 0 0 0 2 3.5v9A1.5 1.5 0 0 0 3.5 14h9a1.5 1.5 0 0 0 1.5-1.5v-9A1.5 1.5 0 0 0 12.5 2h-9Zm0 1.5h9v9h-9v-9Zm3 1a.5.5 0 0 0 0 1h3a.5.5 0 0 0 0-1h-3Zm0 2.5a.5.5 0 0 0 0 1h3a.5.5 0 0 0 0-1h-3Zm0 2.5a.5.5 0 0 0 0 1H9a.5.5 0 0 0 0-1H6.5Z" />
          </svg>
          Моя библиотека
        </RouterLink>
      </div>

      <!-- Divider -->
      <div class="mx-3 my-1 border-t border-[var(--color-rule)]"></div>

      <!-- Projects section -->
      <div class="flex flex-col gap-0.5 px-2 pt-2 pb-1">
        <div class="flex items-center justify-between px-2 pb-1">
          <span class="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">
            Проекты
          </span>
          <button
            @click="showCreateModal = true"
            class="flex h-5 w-5 items-center justify-center rounded text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] hover:bg-[var(--color-accent-pale)] transition-colors"
            title="Новый проект"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-3.5 w-3.5">
              <path d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z" />
            </svg>
          </button>
        </div>

        <!-- Project list -->
        <div v-if="projectStore.loading" class="px-2 py-1 text-xs text-[var(--color-ink-muted)]">
          Загрузка…
        </div>
        <div v-else-if="projectStore.projects.length === 0" class="px-2 py-1 text-xs text-[var(--color-ink-muted)]">
          Нет проектов
        </div>
        <button
          v-for="project in projectStore.projects"
          :key="project.project_id"
          @click="switchProject(project.project_id)"
          class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors"
          :class="route.params.projectId === project.project_id
            ? 'bg-[var(--color-accent-pale)] text-[var(--color-accent-deep)] font-medium'
            : 'text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] hover:text-[var(--color-ink)]'"
        >
          <span class="truncate">{{ project.name }}</span>
        </button>
      </div>

      <!-- Divider -->
      <div class="mx-3 my-1 border-t border-[var(--color-rule)]"></div>

      <!-- Project nav links (shown only when a project is active in the URL) -->
      <nav v-if="route.params.projectId" class="flex flex-col gap-0.5 px-2">
        <RouterLink
          :to="`/${route.params.projectId}/dashboard`"
          class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm font-medium transition-colors"
          :class="route.path === `/${route.params.projectId}/dashboard`
            ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
            : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4 flex-shrink-0">
            <path d="M8.543 2.232a.75.75 0 0 0-1.085 0l-5.25 5.5A.75.75 0 0 0 2.75 9H4v4a1 1 0 0 0 1 1h1.5a.5.5 0 0 0 .5-.5V11h2v2.5a.5.5 0 0 0 .5.5H11a1 1 0 0 0 1-1V9h1.25a.75.75 0 0 0 .543-1.268l-5.25-5.5Z" />
          </svg>
          Обзор
        </RouterLink>
        <RouterLink
          :to="`/${route.params.projectId}/outline`"
          class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm font-medium transition-colors"
          :class="route.path === `/${route.params.projectId}/outline`
            ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
            : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4 flex-shrink-0">
            <path d="M2.75 2a.75.75 0 0 0 0 1.5h10.5a.75.75 0 0 0 0-1.5H2.75ZM2.75 5.5a.75.75 0 0 0 0 1.5h5.5a.75.75 0 0 0 0-1.5h-5.5ZM2 9.75A.75.75 0 0 1 2.75 9h8.5a.75.75 0 0 1 0 1.5h-8.5A.75.75 0 0 1 2 9.75ZM2.75 12.5a.75.75 0 0 0 0 1.5h4a.75.75 0 0 0 0-1.5h-4Z" />
          </svg>
          Структура
        </RouterLink>
        <RouterLink
          :to="`/${route.params.projectId}/library`"
          class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm font-medium transition-colors"
          :class="route.path.startsWith(`/${route.params.projectId}/library`)
            ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
            : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4 flex-shrink-0">
            <path d="M3.5 2A1.5 1.5 0 0 0 2 3.5v9A1.5 1.5 0 0 0 3.5 14h9a1.5 1.5 0 0 0 1.5-1.5v-9A1.5 1.5 0 0 0 12.5 2h-9Zm0 1.5h9v9h-9v-9Zm3 1a.5.5 0 0 0 0 1h3a.5.5 0 0 0 0-1h-3Zm0 2.5a.5.5 0 0 0 0 1h3a.5.5 0 0 0 0-1h-3Zm0 2.5a.5.5 0 0 0 0 1H9a.5.5 0 0 0 0-1H6.5Z" />
          </svg>
          Библиотека
        </RouterLink>
        <RouterLink
          :to="`/${route.params.projectId}/coverage`"
          class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm font-medium transition-colors"
          :class="route.path === `/${route.params.projectId}/coverage`
            ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
            : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4 flex-shrink-0">
            <path d="M1.75 2a.75.75 0 0 0 0 1.5h.75v9.25A1.5 1.5 0 0 0 4 14.25h8A1.5 1.5 0 0 0 13.5 12.75V3.5h.75a.75.75 0 0 0 0-1.5H1.75ZM12 12.75H4V3.5h8v9.25ZM6.5 6a.5.5 0 0 0 0 1h3a.5.5 0 0 0 0-1h-3Zm0 2.5a.5.5 0 0 0 0 1h3a.5.5 0 0 0 0-1h-3Z" />
          </svg>
          Покрытие
        </RouterLink>
        <RouterLink
          :to="`/${route.params.projectId}/research`"
          class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm font-medium transition-colors"
          :class="route.path === `/${route.params.projectId}/research`
            ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
            : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4 flex-shrink-0">
            <path fill-rule="evenodd" d="M9.965 11.026a5 5 0 1 1 1.06-1.06l2.755 2.754a.75.75 0 1 1-1.06 1.06l-2.755-2.754ZM10.5 7a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0Z" clip-rule="evenodd" />
          </svg>
          Исследование
        </RouterLink>
      </nav>
      <!-- Hint when no project selected -->
      <div v-else class="px-4 py-2">
        <p class="text-xs text-[var(--color-ink-muted)]">Выберите проект выше</p>
      </div>

      <!-- Spacer -->
      <div class="flex-1"></div>

      <!-- Footer: tokens + user -->
      <div class="border-t border-[var(--color-rule)] px-3 py-3 space-y-2">
        <!-- Token meter -->
        <div
          v-if="tokenBalance && tokenBalance.total_granted > 0"
          class="space-y-1"
          :title="`Использовано ${tokenBalance.total_used.toLocaleString('ru-RU')} из ${tokenBalance.total_granted.toLocaleString('ru-RU')} токенов`"
        >
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Токены</span>
            <span class="font-[var(--font-mono)] text-xs" :class="tokenColor">
              {{ formatTokens(tokenBalance.remaining) }} ост.
            </span>
          </div>
          <div class="h-1 rounded-full bg-[var(--color-rule-light)] overflow-hidden">
            <div
              class="h-full rounded-full transition-all duration-500"
              :class="tokenBarColor"
              :style="{ width: `${Math.min(tokenPercent, 100)}%` }"
            ></div>
          </div>
        </div>

        <!-- User + logout -->
        <div class="flex items-center justify-between">
          <span v-if="userName" class="truncate text-xs text-[var(--color-ink-muted)]">{{ userName }}</span>
          <button
            @click="logout"
            class="text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-err)] transition-colors"
          >
            Выйти
          </button>
        </div>
      </div>
    </aside>

    <!-- Main content -->
    <div class="flex flex-1 flex-col min-w-0">
      <!-- Low balance banner -->
      <div
        v-if="tokenBalance && tokenBalance.total_granted > 0 && tokenBalance.remaining <= 0"
        class="bg-[var(--color-err-bg)] border-b border-[var(--color-err)] px-8 py-2 text-center"
      >
        <span class="text-sm font-medium text-[var(--color-err)]">
          Токены закончились. Обработка и генерация текста недоступны. Свяжитесь с администратором.
        </span>
      </div>

      <main class="flex-1 px-8 py-8">
        <slot />
      </main>
    </div>
  </div>

  <!-- Create project modal -->
  <Teleport to="body">
    <div
      v-if="showCreateModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="showCreateModal = false"
    >
      <div class="w-full max-w-sm rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-6 shadow-xl">
        <h2 class="mb-4 font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)]">
          Новый проект
        </h2>

        <div class="space-y-3">
          <div>
            <label class="mb-1 block text-xs font-medium text-[var(--color-ink-muted)]">Название</label>
            <input
              v-model="newProjectName"
              type="text"
              placeholder="Моя диссертация"
              class="w-full rounded-md border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 text-sm text-[var(--color-ink)] placeholder:text-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none"
              @keydown.enter="createProject"
              autofocus
            />
          </div>
          <div>
            <label class="mb-1 block text-xs font-medium text-[var(--color-ink-muted)]">Тип</label>
            <select
              v-model="newProjectType"
              class="w-full rounded-md border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 text-sm text-[var(--color-ink)] focus:border-[var(--color-accent)] focus:outline-none"
            >
              <option value="dissertation">Диссертация</option>
              <option value="paper">Статья</option>
              <option value="thesis">Дипломная работа</option>
              <option value="other">Другое</option>
            </select>
          </div>
        </div>

        <div class="mt-5 flex gap-2 justify-end">
          <button
            @click="showCreateModal = false"
            class="rounded-md px-4 py-2 text-sm font-medium text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] transition-colors"
          >
            Отмена
          </button>
          <button
            @click="createProject"
            :disabled="!newProjectName.trim() || creating"
            class="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--color-accent-deep)] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {{ creating ? 'Создание…' : 'Создать' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
