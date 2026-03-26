<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import AppLayout from '@/components/AppLayout.vue'
import { userProjects, process, type OutlineSection } from '@/api/client'

const projectStore = useProjectStore()
const router = useRouter()

// Local editable copy of the outline
const sections = ref<OutlineSection[]>([])
const dirty = ref(false)
const saving = ref(false)
const saveError = ref('')

// Sync from store when active project or outline changes
watch(
  () => projectStore.activeOutline,
  (outline) => {
    sections.value = outline ? outline.map(s => ({ ...s })) : []
    dirty.value = false
  },
  { immediate: true },
)

// New section form
const newId = ref('')
const newName = ref('')
const addError = ref('')

function sortedSections() {
  return [...sections.value].sort((a, b) => {
    const ap = a.id.split('.').map(Number)
    const bp = b.id.split('.').map(Number)
    for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
      const diff = (ap[i] || 0) - (bp[i] || 0)
      if (diff !== 0) return diff
    }
    return 0
  })
}

const displaySections = computed(() => sortedSections())

function addSection() {
  const id = newId.value.trim()
  const name = newName.value.trim()
  addError.value = ''
  if (!id) { addError.value = 'Введите номер раздела'; return }
  if (!name) { addError.value = 'Введите название'; return }
  if (sections.value.some(s => s.id === id)) {
    addError.value = `Раздел ${id} уже существует`
    return
  }
  sections.value.push({ id, name })
  newId.value = ''
  newName.value = ''
  dirty.value = true
}

function removeSection(id: string) {
  sections.value = sections.value.filter(s => s.id !== id)
  dirty.value = true
}

function onNameEdit(id: string, event: Event) {
  const value = (event.target as HTMLInputElement).value
  const s = sections.value.find(s => s.id === id)
  if (s) { s.name = value; dirty.value = true }
}

async function save() {
  saving.value = true
  saveError.value = ''
  try {
    await projectStore.updateOutline(sections.value)
    dirty.value = false
  } catch {
    saveError.value = 'Ошибка сохранения. Попробуйте ещё раз.'
  } finally {
    saving.value = false
  }
}

// AI generation state
const aiContext = ref('')
const generating = ref(false)
const genError = ref('')
const genSuccess = ref('')

async function generateOutline() {
  if (!projectStore.activeProjectId || !aiContext.value.trim()) return
  generating.value = true
  genError.value = ''
  genSuccess.value = ''
  try {
    const resp = await userProjects.generateOutline(
      projectStore.activeProjectId,
      aiContext.value.trim(),
    )
    genSuccess.value = 'Генерация запущена, ожидайте…'
    pollGenerateJob(resp.job_id)
  } catch (e: unknown) {
    genError.value = e instanceof Error ? e.message : 'Ошибка запуска генерации'
    generating.value = false
  }
}

function pollGenerateJob(jobId: string) {
  const interval = setInterval(async () => {
    try {
      const resp = await process.jobStatus(jobId)
      if (resp.status === 'finished') {
        clearInterval(interval)
        generating.value = false
        genSuccess.value = 'Структура сгенерирована!'
        await projectStore.loadProjects()
      } else if (resp.status === 'failed') {
        clearInterval(interval)
        generating.value = false
        genError.value = resp.result?.detail || 'Генерация завершилась с ошибкой'
        genSuccess.value = ''
      }
    } catch {
      clearInterval(interval)
      generating.value = false
      genError.value = 'Ошибка опроса статуса задачи'
    }
  }, 3000)
}

// Danger Zone
const showDeleteConfirm = ref(false)
const deleting = ref(false)
const deleteError = ref('')

async function deleteProject() {
  if (!projectStore.activeProjectId) return
  deleting.value = true
  deleteError.value = ''
  try {
    await userProjects.delete(projectStore.activeProjectId)
    showDeleteConfirm.value = false
    await projectStore.loadProjects()
    router.push('/library')
  } catch (e: unknown) {
    deleteError.value = e instanceof Error ? e.message : 'Ошибка удаления'
  } finally {
    deleting.value = false
  }
}

const DISSERTATION_TEMPLATE: OutlineSection[] = [
  { id: '1',   name: 'Глава 1' },
  { id: '1.1', name: 'Введение в проблему' },
  { id: '1.2', name: 'Обзор литературы' },
  { id: '1.3', name: 'Анализ методов' },
  { id: '2',   name: 'Глава 2' },
  { id: '2.1', name: 'Методология' },
  { id: '2.2', name: 'Данные и эксперименты' },
  { id: '2.3', name: 'Реализация' },
  { id: '3',   name: 'Глава 3' },
  { id: '3.1', name: 'Результаты' },
  { id: '3.2', name: 'Обсуждение' },
  { id: '3.3', name: 'Выводы' },
]

function applyTemplate() {
  sections.value = DISSERTATION_TEMPLATE.map(s => ({ ...s }))
  dirty.value = true
}
</script>

<template>
  <AppLayout>
    <div class="space-y-6">
      <!-- Header -->
      <div class="animate-in flex items-start justify-between gap-4">
        <div>
          <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
            Структура
          </h1>
          <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
            Главы и разделы диссертации — используются для назначения источников и карты покрытия
          </p>
        </div>
        <button
          v-if="dirty"
          @click="save"
          :disabled="saving"
          class="shrink-0 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors"
        >
          {{ saving ? 'Сохранение…' : 'Сохранить' }}
        </button>
      </div>

      <div v-if="saveError" class="rounded-lg border border-[var(--color-err)] bg-[var(--color-err-bg)] px-4 py-3 text-sm text-[var(--color-err)]">
        {{ saveError }}
      </div>

      <!-- No project selected -->
      <div v-if="!projectStore.activeProjectId" class="rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center">
        <p class="text-sm text-[var(--color-ink-muted)]">Выберите проект в боковом меню.</p>
      </div>

      <!-- Empty state: no sections defined yet — show AI generation + template -->
      <div v-else-if="sections.length === 0 && !dirty" class="space-y-4">
        <!-- AI generation panel -->
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-6">
          <div class="flex items-start gap-3 mb-4">
            <div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-accent-pale)]">
              <svg class="h-5 w-5 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
              </svg>
            </div>
            <div>
              <h3 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink)]">
                Сгенерировать структуру с AI
              </h3>
              <p class="mt-0.5 text-xs text-[var(--color-ink-muted)]">
                Введите план-проспект, тезисы или ключевые слова. AI построит главы и разделы.
              </p>
            </div>
          </div>
          <textarea
            v-model="aiContext"
            placeholder="Например: Диссертация о применении нейронных сетей для прогнозирования морского льда. Основные направления: физические модели, LSTM-подходы, спутниковые данные AMSR2, верификация на данных Арктики 2010–2020."
            rows="5"
            :disabled="generating"
            class="w-full rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2.5 text-sm text-[var(--color-ink)] placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none resize-none disabled:opacity-50"
          ></textarea>
          <div class="mt-3 flex items-center gap-3">
            <button
              @click="generateOutline"
              :disabled="generating || !aiContext.trim()"
              class="inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <div v-if="generating" class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent"></div>
              {{ generating ? 'Генерация…' : 'Сгенерировать' }}
            </button>
            <span v-if="genSuccess" class="text-sm text-[var(--color-ok)]">{{ genSuccess }}</span>
            <span v-if="genError" class="text-sm text-[var(--color-err)]">{{ genError }}</span>
          </div>
        </div>

        <!-- Template fallback -->
        <div class="rounded-xl border-2 border-dashed border-[var(--color-rule)] p-8 text-center">
          <p class="text-sm text-[var(--color-ink-muted)]">
            Или загрузите стандартный шаблон диссертации (3 главы):
          </p>
          <button
            @click="applyTemplate"
            class="mt-3 inline-flex items-center gap-2 rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-4 py-2 text-sm font-medium text-[var(--color-ink)] hover:bg-[var(--color-rule-light)] transition-colors"
          >
            Загрузить шаблон
          </button>
        </div>
      </div>

      <!-- Section list -->
      <div v-else class="animate-in space-y-2">
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden">
          <!-- Column headers -->
          <div class="grid grid-cols-[80px_1fr_36px] gap-3 px-4 py-2 bg-[var(--color-paper-warm)] border-b border-[var(--color-rule-light)]">
            <span class="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Номер</span>
            <span class="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Название</span>
            <span></span>
          </div>

          <!-- Section rows -->
          <div
            v-for="(section, i) in displaySections"
            :key="section.id"
            class="grid grid-cols-[80px_1fr_36px] items-center gap-3 px-4 py-2"
            :class="{ 'border-t border-[var(--color-rule-light)]': i > 0 }"
          >
            <span class="font-[var(--font-mono)] text-sm font-medium text-[var(--color-accent)]">
              {{ section.id }}
            </span>
            <input
              :value="section.name"
              @input="onNameEdit(section.id, $event)"
              class="w-full rounded-md border border-transparent bg-transparent px-2 py-1 text-sm text-[var(--color-ink)] hover:border-[var(--color-rule)] focus:border-[var(--color-accent)] focus:outline-none focus:bg-[var(--color-paper)]"
              :placeholder="`Раздел ${section.id}`"
            />
            <button
              @click="removeSection(section.id)"
              class="flex h-7 w-7 items-center justify-center rounded text-[var(--color-ink-muted)] hover:text-[var(--color-err)] hover:bg-[var(--color-err-bg)] transition-colors"
              title="Удалить раздел"
            >
              <svg class="h-3.5 w-3.5" viewBox="0 0 16 16" fill="currentColor">
                <path d="M5.28 4.22a.75.75 0 0 0-1.06 1.06L6.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L8 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L9.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L8 6.94 5.28 4.22Z" />
              </svg>
            </button>
          </div>
        </div>

        <!-- Template reset hint -->
        <p class="text-xs text-[var(--color-ink-muted)] px-1">
          <button @click="applyTemplate" class="text-[var(--color-accent)] hover:underline">
            Сбросить до шаблона (3 главы)
          </button>
        </p>
      </div>

      <!-- AI regenerate (compact, shown when outline exists) -->
      <div v-if="projectStore.activeProjectId && sections.length > 0" class="animate-in rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-5">
        <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider mb-3">
          Перегенерировать с AI
        </h2>
        <div class="flex gap-2">
          <input
            v-model="aiContext"
            placeholder="Новый контекст или правки (план-проспект, тезисы…)"
            :disabled="generating"
            class="flex-1 rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 text-sm text-[var(--color-ink)] placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none disabled:opacity-50"
            @keydown.enter.prevent="generateOutline"
          />
          <button
            @click="generateOutline"
            :disabled="generating || !aiContext.trim()"
            class="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-accent)] px-3 py-2 text-sm font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent-pale)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <div v-if="generating" class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"></div>
            {{ generating ? '…' : 'AI' }}
          </button>
        </div>
        <div class="mt-1.5 flex gap-3">
          <span v-if="genSuccess" class="text-xs text-[var(--color-ok)]">{{ genSuccess }}</span>
          <span v-if="genError" class="text-xs text-[var(--color-err)]">{{ genError }}</span>
        </div>
      </div>

      <!-- Add section form -->
      <div v-if="projectStore.activeProjectId" class="animate-in rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-5">
        <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider mb-4">
          Добавить раздел
        </h2>
        <form @submit.prevent="addSection" class="flex items-start gap-2">
          <input
            v-model="newId"
            placeholder="1.4"
            class="w-20 shrink-0 rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 font-[var(--font-mono)] text-sm text-[var(--color-ink)] placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none"
          />
          <input
            v-model="newName"
            placeholder="Название раздела"
            class="flex-1 rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 text-sm text-[var(--color-ink)] placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none"
            @keydown.enter.prevent="addSection"
          />
          <button
            type="submit"
            :disabled="!newId.trim() || !newName.trim()"
            class="shrink-0 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors"
          >
            Добавить
          </button>
        </form>
        <p v-if="addError" class="mt-2 text-sm text-[var(--color-err)]">{{ addError }}</p>
      </div>
      <!-- Danger Zone -->
      <div class="animate-in rounded-xl border border-[var(--color-err)] bg-[var(--color-paper-white)] p-5">
        <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-err)] uppercase tracking-wider mb-3">
          Опасная зона
        </h2>

        <div v-if="!showDeleteConfirm" class="flex items-center justify-between gap-4">
          <p class="text-sm text-[var(--color-ink-muted)]">
            Удалить проект «{{ projectStore.activeProject?.name }}» безвозвратно.
            Источники и фрагменты в общей библиотеке не затрагиваются.
          </p>
          <button
            @click="showDeleteConfirm = true"
            class="shrink-0 rounded-lg border border-[var(--color-err)] px-4 py-2 text-sm font-medium text-[var(--color-err)] hover:bg-[var(--color-err-bg)] transition-colors"
          >
            Удалить проект
          </button>
        </div>

        <div v-else class="space-y-3">
          <p class="text-sm font-medium text-[var(--color-err)]">
            Удалить «{{ projectStore.activeProject?.name }}»? Это действие необратимо — черновик и данные проекта будут удалены.
          </p>
          <div class="flex items-center gap-3">
            <button
              @click="deleteProject"
              :disabled="deleting"
              class="rounded-lg bg-[var(--color-err)] px-4 py-2 text-sm font-semibold text-white hover:opacity-80 disabled:opacity-50 transition-opacity"
            >
              {{ deleting ? 'Удаление…' : 'Да, удалить' }}
            </button>
            <button
              @click="showDeleteConfirm = false"
              :disabled="deleting"
              class="text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] disabled:opacity-50 transition-colors"
            >
              Отмена
            </button>
            <span v-if="deleteError" class="text-sm text-[var(--color-err)]">{{ deleteError }}</span>
          </div>
        </div>
      </div>

    </div>
  </AppLayout>
</template>
