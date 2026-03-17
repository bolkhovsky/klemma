<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { library, process, ApiError } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const projectStore = useProjectStore()

interface Source {
  citekey: string
  title: string
  authors: string
  year: number | null
  status: string
  doi: string
  sections: string[]
}

const sources = ref<Source[]>([])
const loading = ref(true)
const deleteConfirm = ref<string | null>(null)

// Upload state
const uploading = ref(false)
const uploadError = ref('')
const uploadSuccess = ref('')
const dragOver = ref(false)

// Processing state per source
const processingJobs = ref<Record<string, string>>({})

async function loadSources() {
  loading.value = true
  try {
    const data = await library.list(projectStore.activeProjectId ?? undefined)
    sources.value = data.sources
  } catch {
    sources.value = []
  } finally {
    loading.value = false
  }
}


async function deleteSource(citekey: string) {
  try {
    await library.remove(citekey)
    deleteConfirm.value = null
    await loadSources()
  } catch {
    // silently fail
  }
}

async function processSource(citekey: string, force = false) {
  try {
    const resp = await process.submit(citekey, {
      projectId: projectStore.activeProjectId ?? undefined,
      force,
    })
    processingJobs.value[citekey] = resp.job_id
    pollJob(citekey, resp.job_id)
  } catch {
    // ignore — source detail page has full error handling
  }
}

async function pollJob(citekey: string, jobId: string) {
  const interval = setInterval(async () => {
    try {
      const resp = await process.jobStatus(jobId)
      if (resp.status === 'finished' || resp.status === 'failed') {
        clearInterval(interval)
        delete processingJobs.value[citekey]
        await loadSources()
      }
    } catch {
      clearInterval(interval)
      delete processingJobs.value[citekey]
    }
  }, 3000)
}

async function handleUpload(files: FileList | null) {
  if (!files || files.length === 0) return
  uploadError.value = ''
  uploadSuccess.value = ''
  uploading.value = true

  let uploaded = 0
  let errors = 0
  for (const file of Array.from(files)) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      errors++
      continue
    }
    try {
      const result = await library.upload(file, projectStore.activeProjectId ?? undefined)
      uploaded++
      if (result.deduplicated) {
        uploadSuccess.value = `${file.name} — уже в библиотеке (дедупликация)`
      } else if (result.job_id) {
        processingJobs.value[result.citekey] = result.job_id
        pollJob(result.citekey, result.job_id)
        uploadSuccess.value = `${file.name} — загружен, обработка запущена`
      }
    } catch (e) {
      errors++
      uploadError.value = e instanceof ApiError ? e.message : `Ошибка загрузки ${file.name}`
    }
  }

  if (uploaded > 0) {
    uploadSuccess.value = uploadSuccess.value || `Загружено: ${uploaded} файл(ов)`
    await loadSources()
  }
  if (errors > 0 && !uploadError.value) {
    uploadError.value = `Пропущено: ${errors} (не PDF)`
  }
  uploading.value = false
  dragOver.value = false
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  handleUpload(e.dataTransfer?.files ?? null)
}

function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  handleUpload(input.files)
  input.value = ''
}

onMounted(loadSources)
watch(() => projectStore.activeProjectId, loadSources)
</script>

<template>
  <AppLayout>
    <!-- Header -->
    <div class="animate-in">
      <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">Библиотека</h1>
      <p class="mt-1 text-sm text-[var(--color-ink-muted)]">Источники для вашего исследования</p>
    </div>

    <!-- Upload zone -->
    <div
      class="mt-6 rounded-xl border-2 border-dashed p-8 text-center transition-all duration-200 animate-in animate-in-delay-1"
      :class="dragOver
        ? 'border-[var(--color-accent)] bg-[var(--color-accent-pale)]'
        : 'border-[var(--color-rule)] bg-[var(--color-paper-white)]'"
      @dragover.prevent="dragOver = true"
      @dragleave.prevent="dragOver = false"
      @drop="onDrop"
    >
      <div v-if="uploading" class="flex items-center justify-center gap-2">
        <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
        <span class="text-sm text-[var(--color-ink-muted)]">Загрузка...</span>
      </div>
      <div v-else>
        <svg class="mx-auto w-8 h-8 text-[var(--color-ink-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m6.75 12l-3-3m0 0l-3 3m3-3v6m-1.5-15H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
        </svg>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)]">
          Перетащите PDF сюда или
          <label class="cursor-pointer text-[var(--color-accent)] hover:text-[var(--color-accent-deep)]">
            выберите файл
            <input type="file" accept=".pdf" multiple class="hidden" @change="onFileInput" />
          </label>
        </p>
      </div>
      <div v-if="uploadError" class="mt-3 text-sm text-[var(--color-err)]">{{ uploadError }}</div>
      <div v-if="uploadSuccess" class="mt-3 text-sm text-[var(--color-ok)]">{{ uploadSuccess }}</div>
    </div>


    <!-- Sources list -->
    <div class="mt-6 animate-in animate-in-delay-2">
      <div v-if="loading" class="flex items-center justify-center py-16">
        <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
      </div>

      <!-- Empty state -->
      <div v-else-if="sources.length === 0" class="rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center">
        <div class="mx-auto w-16 h-16 rounded-2xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-5">
          <svg class="w-8 h-8 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.331 0 4.512.89 6.148 2.354M12 6.042c1.985-1.392 4.37-2.292 7.025-2.292.944 0 1.857.14 2.725.4v14.25A9.001 9.001 0 0018 18c-2.331 0-4.512.89-6.148 2.354M12 6.042V20.354" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)]">Библиотека пуста</h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)]">Загрузите PDF-файл выше, чтобы добавить первый источник.</p>
      </div>

      <!-- Sources table -->
      <div v-else class="overflow-hidden rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)]">
        <table class="min-w-full divide-y divide-[var(--color-rule-light)]">
          <thead class="bg-[var(--color-paper-warm)]">
            <tr>
              <th class="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Citekey</th>
              <th class="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Название</th>
              <th class="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Авторы</th>
              <th class="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Год</th>
              <th class="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Разделы</th>
              <th class="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Статус</th>
              <th class="px-5 py-3"></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--color-rule-light)]">
            <tr v-for="src in sources" :key="src.citekey" class="hover:bg-[var(--color-paper-warm)] transition-colors">
              <td class="px-5 py-3.5 text-sm">
                <RouterLink :to="`/${route.params.projectId}/library/${src.citekey}`" class="font-[var(--font-mono)] text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] hover:underline transition-colors">
                  {{ src.citekey }}
                </RouterLink>
              </td>
              <td class="max-w-xs truncate px-5 py-3.5 text-sm text-[var(--color-ink)]">{{ src.title || '—' }}</td>
              <td class="max-w-[150px] truncate px-5 py-3.5 text-sm text-[var(--color-ink-muted)]">{{ src.authors || '—' }}</td>
              <td class="px-5 py-3.5 text-sm font-[var(--font-mono)] text-[var(--color-ink-muted)]">{{ src.year || '—' }}</td>
              <td class="px-5 py-3.5">
                <div v-if="src.sections && src.sections.length > 0" class="flex flex-wrap gap-1">
                  <span
                    v-for="sec in src.sections"
                    :key="sec"
                    class="inline-block rounded-full bg-[var(--color-accent-pale)] px-2 py-0.5 text-xs font-medium text-[var(--color-accent-deep)]"
                  >{{ sec }}</span>
                </div>
                <span v-else class="text-sm text-[var(--color-ink-muted)]">—</span>
              </td>
              <td class="px-5 py-3.5">
                <div class="flex items-center gap-2">
                  <span
                    class="inline-block rounded-full px-2 py-0.5 text-xs font-medium"
                    :class="{
                      'bg-[var(--color-ok-bg)] text-[var(--color-ok)]': src.status === 'completed',
                      'bg-[var(--color-warn-bg)] text-[var(--color-warn)]': src.status === 'pending' || src.status === 'processing',
                      'bg-[var(--color-err-bg)] text-[var(--color-err)]': src.status === 'failed',
                    }"
                  >
                    {{ src.status === 'completed' ? 'готово' : src.status === 'pending' ? 'ожидает' : src.status === 'processing' ? 'обработка' : 'ошибка' }}
                  </span>
                  <!-- Inline process button -->
                  <button
                    v-if="(src.status === 'pending' || src.status === 'failed') && !processingJobs[src.citekey]"
                    @click.stop="processSource(src.citekey, false)"
                    class="text-xs text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] transition-colors"
                    title="Обработать источник"
                  >
                    обработать
                  </button>
                  <div
                    v-if="processingJobs[src.citekey]"
                    class="h-3 w-3 animate-spin rounded-full border border-[var(--color-accent)] border-t-transparent"
                  ></div>
                </div>
              </td>
              <td class="px-5 py-3.5 text-right">
                <button
                  v-if="deleteConfirm !== src.citekey"
                  @click="deleteConfirm = src.citekey"
                  class="text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-err)] transition-colors"
                >
                  Удалить
                </button>
                <span v-else class="flex items-center gap-2">
                  <button
                    @click="deleteSource(src.citekey)"
                    class="text-sm font-medium text-[var(--color-err)] hover:text-red-800"
                  >
                    Да
                  </button>
                  <button
                    @click="deleteConfirm = null"
                    class="text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
                  >
                    Нет
                  </button>
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </AppLayout>
</template>
