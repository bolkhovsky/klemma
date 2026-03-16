<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { write, process as processApi, projects } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'

const router = useRouter()

const sections = ref<string[]>([])
const selectedSection = ref('')
const loading = ref(true)

// Job state
const activeJob = ref<{ id: string; type: string; section: string } | null>(null)
const jobStatus = ref('')
const jobResult = ref<{ status: string; content?: string; detail?: string } | null>(null)
const jobError = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// Result history
const results = ref<{ section: string; type: string; content: string; timestamp: string }[]>([])

async function loadSections() {
  loading.value = true
  try {
    const cov = await projects.coverage()
    sections.value = Object.keys(cov.sections).sort((a, b) => {
      const ap = a.split('.').map(Number)
      const bp = b.split('.').map(Number)
      for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
        const diff = (ap[i] || 0) - (bp[i] || 0)
        if (diff !== 0) return diff
      }
      return 0
    })
    if (sections.value.length > 0 && !selectedSection.value) {
      selectedSection.value = sections.value[0]!
    }
  } catch {
    router.push('/login')
  } finally {
    loading.value = false
  }
}

async function submitJob(type: 'research' | 'draft') {
  if (!selectedSection.value) return
  jobError.value = ''
  jobResult.value = null
  jobStatus.value = 'queued'

  try {
    const resp = type === 'research'
      ? await write.research(selectedSection.value)
      : await write.draft(selectedSection.value)

    activeJob.value = { id: resp.job_id, type, section: selectedSection.value }
    startPolling()
  } catch (e: any) {
    jobError.value = e.message || 'Ошибка запуска'
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!activeJob.value) return
    try {
      const resp = await processApi.jobStatus(activeJob.value.id)
      jobStatus.value = resp.status
      if (resp.status === 'finished') {
        stopPolling()
        jobResult.value = resp.result
        const job = activeJob.value
        if (resp.result?.content && job) {
          results.value.unshift({
            section: job.section,
            type: job.type,
            content: resp.result.content,
            timestamp: new Date().toLocaleTimeString('ru-RU'),
          })
        }
        activeJob.value = null
      } else if (resp.status === 'failed') {
        stopPolling()
        jobResult.value = resp.result
        jobError.value = resp.result?.detail || 'Генерация завершилась с ошибкой'
        activeJob.value = null
      }
    } catch {
      // keep polling
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text)
}

function downloadMarkdown(content: string, section: string, type: string) {
  const blob = new Blob([content], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${type}_${section}.md`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(loadSections)
onUnmounted(stopPolling)
</script>

<template>
  <AppLayout>
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <div v-else class="space-y-8">
      <!-- Header -->
      <div class="animate-in">
        <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
          Генерация текста
        </h1>
        <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
          Исследовательские обзоры и черновики разделов
        </p>
      </div>

      <!-- Empty state: no sections -->
      <div v-if="sections.length === 0" class="animate-in animate-in-delay-1 rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center">
        <div class="mx-auto w-12 h-12 rounded-xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-4">
          <svg class="w-6 h-6 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)]">Нет разделов с источниками</h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)] max-w-md mx-auto">
          Сначала назначьте источники разделам на странице покрытия.
        </p>
      </div>

      <!-- Generator panel -->
      <div v-else class="animate-in animate-in-delay-1 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-6">
        <div class="flex items-end gap-4">
          <!-- Section selector -->
          <div class="flex-1">
            <label class="block text-sm font-medium text-[var(--color-ink-muted)] mb-1.5">Раздел</label>
            <select
              v-model="selectedSection"
              class="w-full rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2.5 text-sm text-[var(--color-ink)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
            >
              <option v-for="s in sections" :key="s" :value="s">{{ s }}</option>
            </select>
          </div>

          <!-- Action buttons -->
          <button
            @click="submitJob('research')"
            :disabled="!!activeJob || !selectedSection"
            class="rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-50 transition-colors"
          >
            Обзор литературы
          </button>
          <button
            @click="submitJob('draft')"
            :disabled="!!activeJob || !selectedSection"
            class="rounded-lg border border-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-[var(--color-accent)] hover:bg-[var(--color-accent-pale)] disabled:opacity-50 transition-colors"
          >
            Черновик раздела
          </button>
        </div>
      </div>

      <!-- Active job indicator -->
      <div v-if="activeJob" class="animate-in rounded-xl border border-[var(--color-accent)] bg-[var(--color-accent-pale)] p-5">
        <div class="flex items-center gap-3">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm font-medium text-[var(--color-accent-deep)]">
            {{ activeJob.type === 'research' ? 'Генерируем обзор литературы' : 'Генерируем черновик' }}
            для раздела {{ activeJob.section }}...
          </span>
          <span class="text-xs text-[var(--color-ink-muted)]">{{ jobStatus }}</span>
        </div>
      </div>

      <!-- Job error -->
      <div v-if="jobError" class="rounded-xl border border-[var(--color-err)] bg-[var(--color-err-bg)] p-4">
        <p class="text-sm text-[var(--color-err)]">{{ jobError }}</p>
      </div>

      <!-- Stub result notice -->
      <div v-if="jobResult && jobResult.status === 'pending'" class="rounded-xl border border-[var(--color-warn)] bg-[var(--color-warn-bg)] p-4">
        <p class="text-sm text-[var(--color-warn)]">{{ jobResult.detail }}</p>
      </div>

      <!-- Results -->
      <div v-if="results.length > 0" class="space-y-6 animate-in animate-in-delay-2">
        <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
          Результаты
        </h2>

        <div
          v-for="(r, i) in results"
          :key="i"
          class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden"
        >
          <!-- Result header -->
          <div class="flex items-center justify-between px-5 py-3 bg-[var(--color-paper-warm)] border-b border-[var(--color-rule-light)]">
            <div class="flex items-center gap-3">
              <span class="font-[var(--font-mono)] text-xs text-[var(--color-accent)]">{{ r.section }}</span>
              <span
                class="rounded-full px-2 py-0.5 text-xs font-medium"
                :class="r.type === 'research' ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'"
              >
                {{ r.type === 'research' ? 'обзор' : 'черновик' }}
              </span>
              <span class="text-xs text-[var(--color-ink-muted)]">{{ r.timestamp }}</span>
            </div>
            <div class="flex items-center gap-2">
              <button
                @click="copyToClipboard(r.content)"
                class="text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] transition-colors"
                title="Скопировать"
              >
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184" />
                </svg>
              </button>
              <button
                @click="downloadMarkdown(r.content, r.section, r.type)"
                class="text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] transition-colors"
                title="Скачать .md"
              >
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                </svg>
              </button>
            </div>
          </div>

          <!-- Result content -->
          <div class="px-5 py-4">
            <div class="prose prose-sm max-w-none text-[var(--color-ink-light)] leading-relaxed whitespace-pre-wrap font-[var(--font-body)]">{{ r.content }}</div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
