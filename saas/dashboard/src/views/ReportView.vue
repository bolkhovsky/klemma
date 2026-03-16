<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { research as researchApi, process as processApi } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = route.params.projectId as string
const section = route.params.section as string

const report = ref<{ text: string; created_at: string; model: string } | null>(null)
const loading = ref(true)
const error = ref('')

// Regeneration state
const regenerating = ref(false)
const regenJobId = ref<string | null>(null)
const regenStatus = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

const sectionName = ref('')

async function loadReport() {
  loading.value = true
  error.value = ''
  try {
    const data = await researchApi.getReport(projectId, section)
    report.value = { text: data.report_text, created_at: data.created_at, model: data.model }
  } catch {
    error.value = 'Обзор не найден'
  } finally {
    loading.value = false
  }
}

function resolveSectionName() {
  const outline = projectStore.activeOutline ?? []
  const entry = outline.find(s => s.id === section)
  sectionName.value = entry?.name ?? section
}

async function regenerate() {
  regenerating.value = true
  regenStatus.value = 'queued'
  try {
    const resp = await researchApi.generate(section, projectId)
    regenJobId.value = resp.job_id
    startPolling()
  } catch (e: any) {
    error.value = e.message || 'Ошибка запуска'
    regenerating.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!regenJobId.value) return
    try {
      const resp = await processApi.jobStatus(regenJobId.value)
      regenStatus.value = resp.status
      if (resp.status === 'finished') {
        stopPolling()
        regenerating.value = false
        regenJobId.value = null
        await loadReport()
      } else if (resp.status === 'failed') {
        stopPolling()
        regenerating.value = false
        regenJobId.value = null
        error.value = resp.result?.detail || 'Генерация завершилась с ошибкой'
      }
    } catch { /* keep polling */ }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function copyToClipboard() {
  if (report.value) navigator.clipboard.writeText(report.value.text)
}

function formatDate(iso: string) {
  try { return new Date(iso + 'Z').toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }) }
  catch { return iso }
}

onMounted(() => {
  resolveSectionName()
  loadReport()
})
</script>

<template>
  <AppLayout>
    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <!-- Error -->
    <div v-else-if="error && !report" class="py-12 text-center">
      <p class="text-sm text-[var(--color-err)]">{{ error }}</p>
      <RouterLink :to="`/${projectId}/research`" class="mt-4 inline-block text-sm text-[var(--color-accent)]">
        &larr; Назад к исследованиям
      </RouterLink>
    </div>

    <!-- Report -->
    <div v-else-if="report" class="space-y-6">
      <!-- Breadcrumb -->
      <div class="animate-in">
        <RouterLink :to="`/${projectId}/research`" class="text-sm text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] transition-colors">
          &larr; Исследование
        </RouterLink>
      </div>

      <!-- Header -->
      <div class="animate-in animate-in-delay-1">
        <div class="flex items-start justify-between gap-6">
          <div>
            <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
              <span class="font-[var(--font-mono)] text-[var(--color-accent)]">{{ section }}</span>
              <span class="mx-2 text-[var(--color-rule)]">/</span>
              {{ sectionName }}
            </h1>
            <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
              Обзор литературы
              <span v-if="report.created_at"> &middot; {{ formatDate(report.created_at) }}</span>
              <span v-if="report.model"> &middot; {{ report.model }}</span>
            </p>
          </div>

          <div class="flex items-center gap-2 shrink-0">
            <button
              @click="copyToClipboard"
              class="rounded-lg border border-[var(--color-rule)] px-3 py-2 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)] transition-colors"
              title="Скопировать"
            >
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184" />
              </svg>
            </button>
            <button
              @click="regenerate"
              :disabled="regenerating"
              class="rounded-lg border border-[var(--color-rule)] px-4 py-2 text-sm font-medium text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)] disabled:opacity-50 transition-colors"
            >
              {{ regenerating ? 'Генерация...' : 'Обновить' }}
            </button>
          </div>
        </div>
      </div>

      <!-- Regeneration progress -->
      <div v-if="regenerating" class="animate-in rounded-xl border border-[var(--color-accent)] bg-[var(--color-accent-pale)] p-4">
        <div class="flex items-center gap-3">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm font-medium text-[var(--color-accent-deep)]">Обновляем обзор...</span>
          <span class="text-xs text-[var(--color-ink-muted)]">{{ regenStatus }}</span>
        </div>
      </div>

      <!-- Error banner -->
      <div v-if="error" class="rounded-xl border border-[var(--color-err)] bg-[var(--color-err-bg)] p-4">
        <p class="text-sm text-[var(--color-err)]">{{ error }}</p>
      </div>

      <!-- Report content -->
      <div class="animate-in animate-in-delay-2 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden">
        <div class="px-8 py-8">
          <div class="prose prose-sm max-w-none text-[var(--color-ink-light)] leading-relaxed whitespace-pre-wrap font-[var(--font-body)]">{{ report.text }}</div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
