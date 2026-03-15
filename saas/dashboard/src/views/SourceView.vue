<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { library, process, projects, ApiError } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'

interface Fragment {
  fragment_id: string
  text: string
  fragment_type: string
  page_number: number | null
  citation_intent: string | null
}

interface SourceDetail {
  citekey: string
  paper_id: string
  status: string
  title: string
  authors: string
  year: number | null
  doi: string | null
  abstract: string
  fragments: Fragment[]
}

const route = useRoute()
const router = useRouter()
const citekey = route.params.citekey as string

const source = ref<SourceDetail | null>(null)
const loading = ref(true)
const error = ref('')

// Processing state
const processing = ref(false)
const jobId = ref<string | null>(null)
const jobStatus = ref('')
const jobError = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

const intentLabel: Record<string, string> = {
  background: 'фон',
  method: 'метод',
  result_comparison: 'результат',
  extends: 'расширяет',
  contrasts: 'контраст',
  uses_data: 'данные',
}

const intentColor: Record<string, string> = {
  background: 'bg-blue-100 text-blue-700',
  method: 'bg-purple-100 text-purple-700',
  result_comparison: 'bg-green-100 text-green-700',
  extends: 'bg-teal-100 text-teal-700',
  contrasts: 'bg-orange-100 text-orange-700',
  uses_data: 'bg-yellow-100 text-yellow-700',
}

const typeLabel: Record<string, string> = {
  key_idea: 'идея',
  quote: 'цитата',
  methodology: 'методология',
  result: 'результат',
  conclusion: 'вывод',
  definition: 'определение',
}

const fragmentsByType = computed(() => {
  if (!source.value) return {}
  const groups: Record<string, Fragment[]> = {}
  for (const f of source.value.fragments) {
    const key = f.fragment_type || 'key_idea'
    if (!groups[key]) groups[key] = []
    groups[key].push(f)
  }
  return groups
})

// Section assignment state
const assignedSections = ref<string[]>([])
const newSection = ref('')
const assignLoading = ref(false)
const assignError = ref('')

async function loadSections() {
  try {
    const resp = await projects.sourceSections(citekey)
    assignedSections.value = resp.sections
  } catch {
    assignedSections.value = []
  }
}

async function addSection() {
  const section = newSection.value.trim()
  if (!section || assignedSections.value.includes(section)) return
  assignLoading.value = true
  assignError.value = ''
  try {
    const updated = [...assignedSections.value, section]
    await projects.assignSections(citekey, updated)
    assignedSections.value = updated
    newSection.value = ''
  } catch (e) {
    assignError.value = e instanceof ApiError ? e.message : 'Ошибка назначения'
  } finally {
    assignLoading.value = false
  }
}

async function removeSection(section: string) {
  assignLoading.value = true
  try {
    const updated = assignedSections.value.filter(s => s !== section)
    await projects.assignSections(citekey, updated)
    assignedSections.value = updated
  } catch {
    // ignore
  } finally {
    assignLoading.value = false
  }
}

async function loadSource() {
  loading.value = true
  error.value = ''
  try {
    source.value = await library.get(citekey)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      error.value = 'Источник не найден'
    } else {
      error.value = 'Ошибка загрузки'
    }
  } finally {
    loading.value = false
  }
}

async function startProcessing() {
  processing.value = true
  jobError.value = ''
  jobStatus.value = 'queued'
  try {
    const resp = await process.submit(citekey)
    jobId.value = resp.job_id
    startPolling()
  } catch (e) {
    jobError.value = e instanceof ApiError ? e.message : 'Ошибка запуска обработки'
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
        await loadSource()
      } else if (resp.status === 'failed') {
        stopPolling()
        processing.value = false
        jobError.value = resp.result?.detail || 'Обработка завершилась с ошибкой'
        await loadSource()
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

onMounted(() => {
  loadSource()
  loadSections()
})
onUnmounted(stopPolling)
</script>

<template>
  <AppLayout>
    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <!-- Error -->
    <div v-else-if="error" class="py-12 text-center">
      <p class="text-sm text-[var(--color-err)]">{{ error }}</p>
      <RouterLink to="/library" class="mt-4 inline-block text-sm text-[var(--color-accent)]">
        &larr; Вернуться в библиотеку
      </RouterLink>
    </div>

    <!-- Source detail -->
    <div v-else-if="source" class="space-y-8">
      <!-- Breadcrumb -->
      <div class="animate-in">
        <RouterLink to="/library" class="text-sm text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] transition-colors">
          &larr; Библиотека
        </RouterLink>
      </div>

      <!-- Header -->
      <div class="animate-in animate-in-delay-1">
        <div class="flex items-start justify-between gap-6">
          <div class="min-w-0">
            <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight leading-tight">
              {{ source.title || source.citekey }}
            </h1>
            <div class="mt-2 flex flex-wrap items-center gap-3 text-sm text-[var(--color-ink-muted)]">
              <span v-if="source.authors">{{ source.authors }}</span>
              <span v-if="source.year" class="font-[var(--font-mono)]">{{ source.year }}</span>
              <span class="font-[var(--font-mono)] text-xs text-[var(--color-accent)]">{{ source.citekey }}</span>
              <a
                v-if="source.doi"
                :href="`https://doi.org/${source.doi}`"
                target="_blank"
                class="text-xs text-[var(--color-accent)] hover:underline"
              >
                DOI: {{ source.doi }}
              </a>
            </div>
          </div>

          <!-- Status + Process button -->
          <div class="flex items-center gap-3 shrink-0">
            <span
              class="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium"
              :class="{
                'bg-[var(--color-ok-bg)] text-[var(--color-ok)]': source.status === 'completed',
                'bg-[var(--color-warn-bg)] text-[var(--color-warn)]': source.status === 'pending' || source.status === 'processing',
                'bg-[var(--color-err-bg)] text-[var(--color-err)]': source.status === 'failed',
              }"
            >
              {{ source.status === 'completed' ? 'готово' : source.status === 'pending' ? 'ожидает' : source.status === 'processing' ? 'обработка...' : 'ошибка' }}
            </span>

            <button
              v-if="source.status === 'pending' || source.status === 'failed'"
              @click="startProcessing"
              :disabled="processing"
              class="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors"
            >
              {{ processing ? 'Обработка...' : 'Обработать' }}
            </button>
          </div>
        </div>
      </div>

      <!-- Processing indicator -->
      <div v-if="processing" class="animate-in rounded-xl border border-[var(--color-accent)] bg-[var(--color-accent-pale)] p-5">
        <div class="flex items-center gap-3">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm font-medium text-[var(--color-accent-deep)]">
            Извлекаем фрагменты из PDF...
          </span>
          <span class="text-xs text-[var(--color-ink-muted)]">{{ jobStatus }}</span>
        </div>
      </div>

      <!-- Job error -->
      <div v-if="jobError" class="rounded-xl border border-[var(--color-err)] bg-[var(--color-err-bg)] p-4">
        <p class="text-sm text-[var(--color-err)]">{{ jobError }}</p>
      </div>

      <!-- Abstract -->
      <div v-if="source.abstract" class="animate-in animate-in-delay-2 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-6">
        <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider mb-3">
          Аннотация
        </h2>
        <p class="text-sm text-[var(--color-ink-light)] leading-relaxed">{{ source.abstract }}</p>
      </div>

      <!-- Section assignment -->
      <div class="animate-in animate-in-delay-3 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-6">
        <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider mb-4">
          Разделы диссертации
        </h2>

        <!-- Assigned sections -->
        <div v-if="assignedSections.length > 0" class="flex flex-wrap gap-2 mb-4">
          <span
            v-for="section in assignedSections"
            :key="section"
            class="inline-flex items-center gap-1.5 rounded-full bg-[var(--color-accent-pale)] px-3 py-1 text-sm font-medium text-[var(--color-accent-deep)]"
          >
            {{ section }}
            <button
              @click="removeSection(section)"
              class="text-[var(--color-accent)] hover:text-[var(--color-err)] transition-colors"
              :disabled="assignLoading"
            >
              &times;
            </button>
          </span>
        </div>
        <p v-else class="text-sm text-[var(--color-ink-muted)] mb-4">
          Не назначен ни одному разделу.
        </p>

        <!-- Add section form -->
        <form @submit.prevent="addSection" class="flex items-center gap-2">
          <input
            v-model="newSection"
            placeholder="Номер раздела (например: 1.2.3)"
            class="flex-1 rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 text-sm text-[var(--color-ink)] placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
          />
          <button
            type="submit"
            :disabled="assignLoading || !newSection.trim()"
            class="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors"
          >
            Назначить
          </button>
        </form>
        <div v-if="assignError" class="mt-2 text-sm text-[var(--color-err)]">{{ assignError }}</div>
      </div>

      <!-- Fragments -->
      <div v-if="source.fragments.length > 0" class="animate-in animate-in-delay-3 space-y-6">
        <div class="flex items-center justify-between">
          <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
            Фрагменты
          </h2>
          <span class="font-[var(--font-mono)] text-sm text-[var(--color-ink-muted)]">
            {{ source.fragments.length }}
          </span>
        </div>

        <!-- Fragment groups by type -->
        <div v-for="(frags, type) in fragmentsByType" :key="type" class="space-y-3">
          <h3 class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
            {{ typeLabel[type] || type }}
          </h3>

          <div class="space-y-2">
            <div
              v-for="f in frags"
              :key="f.fragment_id"
              class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-4 hover:border-[var(--color-rule)] transition-colors"
            >
              <p class="text-sm text-[var(--color-ink)] leading-relaxed">{{ f.text }}</p>
              <div class="mt-3 flex flex-wrap items-center gap-2">
                <span v-if="f.page_number" class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">
                  стр. {{ f.page_number }}
                </span>
                <span
                  v-if="f.citation_intent"
                  class="rounded-full px-2 py-0.5 text-xs font-medium"
                  :class="intentColor[f.citation_intent] || 'bg-gray-100 text-gray-600'"
                >
                  {{ intentLabel[f.citation_intent] || f.citation_intent }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- No fragments empty state -->
      <div
        v-else-if="source.status === 'completed'"
        class="rounded-xl border-2 border-dashed border-[var(--color-rule)] p-12 text-center"
      >
        <p class="text-sm text-[var(--color-ink-muted)]">Фрагменты не найдены.</p>
      </div>

      <div
        v-else-if="source.status === 'pending' && !processing"
        class="rounded-xl border-2 border-dashed border-[var(--color-rule)] p-12 text-center"
      >
        <div class="mx-auto w-12 h-12 rounded-xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-4">
          <svg class="w-6 h-6 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)]">
          Источник ещё не обработан
        </h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)]">
          Нажмите «Обработать», чтобы извлечь ключевые фрагменты из PDF.
        </p>
        <button
          @click="startProcessing"
          :disabled="processing"
          class="mt-5 inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors"
        >
          Обработать
        </button>
      </div>
    </div>
  </AppLayout>
</template>
