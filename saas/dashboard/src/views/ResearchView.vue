<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { research as researchApi, process as processApi } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.projectId as string)

// Report list
interface ReportSummary {
  section: string
  sectionName: string
  created_at: string
}

const reports = ref<ReportSummary[]>([])
const loading = ref(true)

// Generation form
const showForm = ref(false)
const selectedSection = ref('')
const generating = ref(false)
const genJobId = ref<string | null>(null)
const genStatus = ref('')
const genError = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// Available sections: second-level only (X.Y), exclude already-generated
const availableSections = computed(() => {
  const outline = projectStore.activeOutline ?? []
  const existing = new Set(reports.value.map(r => r.section))
  return outline.filter(s => /^\d+\.\d+$/.test(s.id) && !existing.has(s.id))
})

async function loadReports() {
  loading.value = true
  try {
    const data = await researchApi.listReports(projectId.value)
    const outline = projectStore.activeOutline ?? []
    reports.value = data.reports.map(r => {
      const entry = outline.find(s => s.id === r.section)
      return {
        section: r.section,
        sectionName: entry?.name ?? r.section,
        created_at: r.created_at,
      }
    }).sort((a, b) => {
      const ap = a.section.split('.').map(Number)
      const bp = b.section.split('.').map(Number)
      for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
        const diff = (ap[i] || 0) - (bp[i] || 0)
        if (diff !== 0) return diff
      }
      return 0
    })
  } catch {
    reports.value = []
  } finally {
    loading.value = false
  }
}

async function generate() {
  if (!selectedSection.value) return
  generating.value = true
  genError.value = ''
  genStatus.value = 'queued'

  try {
    const resp = await researchApi.generate(selectedSection.value, projectId.value)
    genJobId.value = resp.job_id
    startPolling()
  } catch (e: any) {
    genError.value = e.message || 'Ошибка запуска'
    generating.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!genJobId.value) return
    try {
      const resp = await processApi.jobStatus(genJobId.value)
      genStatus.value = resp.status
      if (resp.status === 'finished') {
        stopPolling()
        generating.value = false
        const sec = selectedSection.value
        genJobId.value = null
        selectedSection.value = ''
        showForm.value = false
        router.push(`/${projectId.value}/research/${sec}`)
      } else if (resp.status === 'failed') {
        stopPolling()
        generating.value = false
        genJobId.value = null
        genError.value = resp.result?.detail || 'Генерация завершилась с ошибкой'
      }
    } catch { /* keep polling */ }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function formatDate(iso: string) {
  try { return new Date(iso + 'Z').toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) }
  catch { return iso }
}

onMounted(loadReports)
onUnmounted(stopPolling)
</script>

<template>
  <AppLayout>
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <div v-else class="space-y-8">
      <!-- Header -->
      <div class="animate-in flex items-start justify-between">
        <div>
          <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
            Исследование
          </h1>
          <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
            Обзоры литературы по разделам вашей работы
          </p>
        </div>
        <button
          v-if="reports.length > 0 && availableSections.length > 0"
          @click="showForm = !showForm"
          class="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] transition-colors"
        >
          + Добавить обзор
        </button>
      </div>

      <!-- Generation form -->
      <div v-if="showForm" class="animate-in rounded-xl border border-[var(--color-accent)] bg-[var(--color-accent-pale)] p-5">
        <h3 class="text-sm font-semibold text-[var(--color-accent-deep)] mb-3">Новый обзор литературы</h3>
        <div class="flex items-end gap-4">
          <div class="flex-1">
            <label class="block text-sm font-medium text-[var(--color-ink-muted)] mb-1.5">Раздел</label>
            <select
              v-model="selectedSection"
              :disabled="generating"
              class="w-full rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-3 py-2.5 text-sm text-[var(--color-ink)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)] disabled:opacity-50"
            >
              <option value="">-- выберите раздел --</option>
              <option v-for="s in availableSections" :key="s.id" :value="s.id">
                {{ s.id }} &middot; {{ s.name }}
              </option>
            </select>
          </div>
          <button
            @click="generate"
            :disabled="!selectedSection || generating"
            class="rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors"
          >
            {{ generating ? 'Генерация...' : 'Сгенерировать' }}
          </button>
          <button
            v-if="!generating"
            @click="showForm = false; selectedSection = ''"
            class="rounded-lg px-4 py-2.5 text-sm text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] transition-colors"
          >
            Отмена
          </button>
        </div>

        <!-- Generation progress -->
        <div v-if="generating" class="mt-4 flex items-center gap-3">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm font-medium text-[var(--color-accent-deep)]">Генерируем обзор для {{ selectedSection }}...</span>
          <span class="text-xs text-[var(--color-ink-muted)]">{{ genStatus }}</span>
        </div>

        <div v-if="genError" class="mt-3 text-sm text-[var(--color-err)]">{{ genError }}</div>
      </div>

      <!-- Empty state -->
      <div v-if="reports.length === 0 && !showForm" class="animate-in animate-in-delay-1 rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center">
        <div class="mx-auto w-16 h-16 rounded-2xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-5">
          <svg class="w-8 h-8 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)]">
          Начните исследование
        </h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)] max-w-md mx-auto leading-relaxed">
          Сгенерируйте первый обзор литературы — система проанализирует ваши источники, составит план аргументации и покажет, чего не хватает.
        </p>
        <button
          v-if="availableSections.length > 0"
          @click="showForm = true"
          class="mt-6 inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] transition-colors shadow-sm"
        >
          Сгенерировать первый обзор
        </button>
        <p v-else class="mt-4 text-xs text-[var(--color-ink-muted)]">
          Определите структуру работы и загрузите источники, чтобы начать.
        </p>
      </div>

      <!-- Report cards -->
      <div v-if="reports.length > 0" class="animate-in animate-in-delay-1 space-y-3">
        <RouterLink
          v-for="r in reports"
          :key="r.section"
          :to="`/${projectId}/research/${r.section}`"
          class="block rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-6 py-5 hover:border-[var(--color-accent)] hover:shadow-sm transition-all group"
        >
          <div class="flex items-center justify-between">
            <div class="min-w-0">
              <div class="flex items-center gap-3">
                <span class="font-[var(--font-mono)] text-sm text-[var(--color-accent)] font-medium">{{ r.section }}</span>
                <span class="text-sm font-semibold text-[var(--color-ink)] truncate">{{ r.sectionName }}</span>
              </div>
              <p class="mt-1 text-xs text-[var(--color-ink-muted)]">
                Обзор литературы &middot; {{ formatDate(r.created_at) }}
              </p>
            </div>
            <svg
              class="w-5 h-5 text-[var(--color-ink-muted)] group-hover:text-[var(--color-accent)] transition-colors shrink-0"
              fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
            </svg>
          </div>
        </RouterLink>
      </div>
    </div>
  </AppLayout>
</template>
