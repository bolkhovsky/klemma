<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { library, process, curation, ApiError } from '@/api/client'
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

// Reference gaps
interface Gap {
  title: string
  authors: string | null
  year: number | null
  cited_by_count: number
  intents: string | null
  doi?: string | null
}
const gaps = ref<Gap[]>([])
const gapsDetail = ref('')

// Curation stats per citekey: { accepted, total }
const curationStats = ref<Record<string, { accepted: number; total: number }>>({})
const totalAccepted = ref(0)

// Coverage filter
const activeSectionFilter = ref<string | null>(null)

const outline = computed(() => projectStore.activeOutline ?? [])

// Coverage cells: count accepted fragments per section
const coverageCells = computed(() => {
  return outline.value.map(s => {
    const count = curatedBySection.value[s.id] || 0
    return { id: s.id, name: s.name, count }
  })
})

// Curated fragments grouped by section
const curatedBySection = ref<Record<string, number>>({})

const coveragePct = computed(() => {
  if (outline.value.length === 0) return 0
  const covered = outline.value.filter(s => (curatedBySection.value[s.id] || 0) > 0).length
  return Math.round((covered / outline.value.length) * 100)
})

const filteredSources = computed(() => {
  if (!activeSectionFilter.value) return sources.value
  return sources.value.filter(s =>
    s.sections && s.sections.includes(activeSectionFilter.value!)
  )
})

function shortAuthors(a: string | null): string {
  if (!a) return '—'
  return a.includes(',') ? a.split(',')[0]!.trim() + ' et al.' : a
}

async function loadGaps() {
  gaps.value = []
  gapsDetail.value = ''
  try {
    const data = await library.gaps()
    gaps.value = data.gaps
    gapsDetail.value = data.detail ?? ''
  } catch {
    gaps.value = []
  }
}

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

async function loadCurationStats() {
  const pid = projectStore.activeProjectId
  if (!pid) return
  try {
    // Get all curated (accepted + rejected) to compute per-source stats
    const data = await curation.curated(pid)
    const stats: Record<string, { accepted: number; total: number }> = {}
    const bySection: Record<string, number> = {}
    let accepted = 0

    for (const f of data.fragments) {
      if (!stats[f.citekey]) stats[f.citekey] = { accepted: 0, total: 0 }
      stats[f.citekey]!.total++
      if (f.verdict === 'accepted') {
        stats[f.citekey]!.accepted++
        accepted++
        const sec = f.assigned_section || ''
        if (sec) bySection[sec] = (bySection[sec] || 0) + 1
      }
    }
    curationStats.value = stats
    curatedBySection.value = bySection
    totalAccepted.value = accepted
  } catch {
    curationStats.value = {}
    curatedBySection.value = {}
    totalAccepted.value = 0
  }
}

// Total fragments per source (from processing, not curation)
const fragmentCounts = ref<Record<string, number>>({})

async function loadFragmentCounts() {
  // We'll fetch pending for each completed source to get total fragment count
  // This is expensive — skip if no project
  const pid = projectStore.activeProjectId
  if (!pid) return
  const completed = sources.value.filter(s => s.status === 'completed')
  for (const src of completed) {
    if (fragmentCounts.value[src.citekey] !== undefined) continue
    try {
      const data = await curation.pending(pid, src.citekey)
      fragmentCounts.value[src.citekey] = data.total
    } catch {
      // skip
    }
  }
}

function curationBadge(citekey: string): { text: string; cls: string } {
  const stats = curationStats.value[citekey]
  const total = fragmentCounts.value[citekey]
  if (total === undefined || total === 0) return { text: '—', cls: 'curation-none' }
  const accepted = stats?.accepted ?? 0
  const label = `${accepted}/${total}`
  if (accepted === 0) return { text: label, cls: 'curation-none' }
  if (accepted >= total) return { text: `${label} ✓`, cls: 'curation-done' }
  return { text: label, cls: 'curation-partial' }
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
    // ignore
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
      if (result.already_owned) {
        uploadSuccess.value = `${file.name} — уже в библиотеке`
      } else if (result.deduplicated) {
        uploadSuccess.value = `${file.name} — загружен (обработка не требуется)`
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

function filterBySection(sectionId: string) {
  activeSectionFilter.value = activeSectionFilter.value === sectionId ? null : sectionId
}

async function loadAll() {
  await loadSources()
  loadGaps()
  loadCurationStats()
}

onMounted(loadAll)
watch(() => projectStore.activeProjectId, loadAll)
watch(sources, () => { loadFragmentCounts() })
</script>

<template>
  <AppLayout>
    <!-- Upload zone -->
    <div class="animate-in">
      <div
        class="upload-zone"
        :class="{ 'upload-zone-hover': dragOver }"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop="onDrop"
      >
        <div v-if="uploading" class="flex items-center justify-center gap-2">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm text-[var(--color-ink-muted)]">Загрузка...</span>
        </div>
        <div v-else>
          <div class="upload-icon">&#128196;</div>
          <div class="upload-label">Загрузить PDF</div>
          <div class="upload-hint">
            Перетащите файл или
            <label class="cursor-pointer text-[var(--color-accent)] hover:text-[var(--color-accent-deep)]">
              нажмите для выбора
              <input type="file" accept=".pdf" multiple class="hidden" @change="onFileInput" />
            </label>
          </div>
        </div>
        <div v-if="uploadError" class="mt-3 text-sm text-[var(--color-err)]">{{ uploadError }}</div>
        <div v-if="uploadSuccess" class="mt-3 text-sm text-[var(--color-ok)]">{{ uploadSuccess }}</div>
      </div>
    </div>

    <!-- Coverage bar -->
    <div v-if="outline.length > 0 && sources.length > 0" class="mt-5 animate-in animate-in-delay-1">
      <div class="coverage-filter">
        <div class="flex items-center gap-3 flex-wrap">
          <span class="text-[14px] font-semibold text-[var(--color-ink)]">Покрытие по разделам</span>
          <select
            class="coverage-select"
            :value="activeSectionFilter || ''"
            @change="activeSectionFilter = ($event.target as HTMLSelectElement).value || null"
          >
            <option value="">Все разделы</option>
            <option v-for="cell in coverageCells" :key="cell.id" :value="cell.id">
              {{ cell.name || cell.id }} ({{ cell.count }})
            </option>
          </select>
          <span class="text-sm text-[var(--color-ink-muted)]">
            {{ totalAccepted }} цитат принято &middot; {{ coveragePct }}% покрыто
          </span>
        </div>
      </div>
    </div>

    <!-- Sources table -->
    <div class="mt-5 animate-in animate-in-delay-2">
      <div v-if="loading" class="flex items-center justify-center py-16">
        <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
      </div>

      <!-- Empty state -->
      <div v-else-if="sources.length === 0" class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-8">
        <h3 class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)] mb-1">С чего начать?</h3>
        <p class="text-sm text-[var(--color-ink-muted)] mb-5">Три шага до первого результата:</p>
        <div class="space-y-4">
          <div class="flex items-start gap-3">
            <div class="w-7 h-7 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center text-[13px] font-bold flex-shrink-0">1</div>
            <div>
              <div class="text-[15px] font-medium text-[var(--color-ink)]">Загрузите PDF-статьи</div>
              <div class="text-sm text-[var(--color-ink-muted)] mt-0.5">Перетащите файлы в зону выше. Минимум 3 статьи для анализа покрытия.</div>
            </div>
          </div>
          <div class="flex items-start gap-3">
            <div class="w-7 h-7 rounded-full bg-[var(--color-rule)] text-[var(--color-ink-muted)] flex items-center justify-center text-[13px] font-bold flex-shrink-0">2</div>
            <div>
              <div class="text-[15px] font-medium text-[var(--color-ink-muted)]">Дождитесь обработки</div>
              <div class="text-sm text-[var(--color-ink-muted)] mt-0.5">Klemma извлечёт цитаты, аргументы и ключевые фрагменты из каждой статьи.</div>
            </div>
          </div>
          <div class="flex items-start gap-3">
            <div class="w-7 h-7 rounded-full bg-[var(--color-rule)] text-[var(--color-ink-muted)] flex items-center justify-center text-[13px] font-bold flex-shrink-0">3</div>
            <div>
              <div class="text-[15px] font-medium text-[var(--color-ink-muted)]">Отберите цитаты</div>
              <div class="text-sm text-[var(--color-ink-muted)] mt-0.5">Нажмите на источник и отберите полезные цитаты для вашей работы.</div>
            </div>
          </div>
        </div>
      </div>

      <template v-else>
        <!-- Outline hint -->
        <div
          v-if="!projectStore.activeOutline || projectStore.activeOutline.length === 0"
          class="mb-4 flex items-start gap-3 rounded-lg border border-amber-200 bg-[var(--color-warn-bg)] px-4 py-3"
        >
          <svg class="w-5 h-5 text-[var(--color-warn)] flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"/></svg>
          <div>
            <div class="text-sm font-medium text-[var(--color-ink)]">Нет структуры работы</div>
            <div class="text-sm text-[var(--color-ink-muted)] mt-0.5">Создайте новый проект с шаблоном структуры — Klemma распределит источники по разделам автоматически.</div>
          </div>
        </div>

        <div class="text-base font-semibold text-[var(--color-ink)] mb-2.5 flex items-center gap-2">
          Мои источники <span class="text-sm font-semibold text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] px-2 py-0.5 rounded-full">{{ filteredSources.length }}</span>
        </div>
        <table class="source-table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Авторы</th>
              <th>Год</th>
              <th>Статус</th>
              <th>Цитаты</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="src in filteredSources" :key="src.citekey">
              <td>
                <RouterLink
                  :to="`/${route.params.projectId}/library/${src.citekey}`"
                  class="text-[14px] font-medium text-[var(--color-ink)] no-underline hover:text-[var(--color-accent)]"
                >{{ src.title || src.citekey }}</RouterLink>
              </td>
              <td class="text-[13px] text-[var(--color-ink-muted)]" style="max-width: 140px">{{ shortAuthors(src.authors) }}</td>
              <td class="font-mono text-[14px]">{{ src.year || '—' }}</td>
              <td>
                <div class="flex items-center gap-2">
                  <span
                    class="status-badge"
                    :class="{
                      'status-completed': src.status === 'completed',
                      'status-pending': src.status === 'pending' || src.status === 'processing',
                      'status-failed': src.status === 'failed',
                    }"
                  >
                    {{ src.status === 'completed' ? 'готово' : src.status === 'pending' ? 'ожидает' : src.status === 'processing' ? 'обработка' : 'ошибка' }}
                  </span>
                  <button
                    v-if="(src.status === 'pending' || src.status === 'failed') && !processingJobs[src.citekey]"
                    @click.stop="processSource(src.citekey, false)"
                    class="text-sm text-[var(--color-accent)] hover:text-[var(--color-accent-deep)]"
                  >обработать</button>
                  <div
                    v-if="processingJobs[src.citekey]"
                    class="h-3 w-3 animate-spin rounded-full border border-[var(--color-accent)] border-t-transparent"
                  ></div>
                </div>
              </td>
              <td>
                <span
                  class="curation-badge"
                  :class="curationBadge(src.citekey).cls"
                >{{ curationBadge(src.citekey).text }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>

    <!-- Reference gaps -->
    <div v-if="gaps.length > 0 && sources.length > 0" class="mt-8 animate-in animate-in-delay-3">
      <div class="text-base font-semibold text-[var(--color-ink)] mb-2.5 flex items-center gap-2">
        Рекомендуемая литература <span class="text-sm font-semibold text-[var(--color-ink-muted)] bg-[var(--color-rule-light)] px-2 py-0.5 rounded-full">{{ gaps.length }}</span>
      </div>
      <table class="gaps-table">
        <thead>
          <tr>
            <th>Название</th>
            <th>Авторы</th>
            <th>Год</th>
            <th>DOI</th>
            <th style="text-align: center">Ссылок</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(gap, i) in gaps" :key="i">
            <td><span class="gap-title">{{ gap.title }}</span></td>
            <td class="text-[13px] text-[var(--color-ink-muted)]">{{ shortAuthors(gap.authors) }}</td>
            <td class="font-mono text-[14px]">{{ gap.year || '—' }}</td>
            <td>
              <a
                v-if="gap.doi"
                :href="`https://doi.org/${gap.doi}`"
                target="_blank"
                rel="noopener"
                class="gap-doi"
                :title="gap.doi"
              >{{ gap.doi.length > 20 ? gap.doi.slice(0, 18) + '...' : gap.doi }}</a>
              <span v-else class="text-[var(--color-ink-muted)]">—</span>
            </td>
            <td class="gap-refs">{{ gap.cited_by_count }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else-if="gapsDetail" class="mt-8">
      <p class="text-sm text-[var(--color-ink-muted)]">{{ gapsDetail }}</p>
    </div>
  </AppLayout>
</template>

<style scoped>
/* Upload zone */
.upload-zone {
  border: 2px dashed var(--color-rule);
  border-radius: 10px;
  padding: 24px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
}
.upload-zone:hover,
.upload-zone-hover {
  border-color: var(--color-accent);
  background: var(--color-accent-pale);
}
.upload-icon { font-size: 28px; margin-bottom: 8px; }
.upload-label { font-size: 16px; font-weight: 500; color: var(--color-ink-2); }
.upload-hint { font-size: 14px; color: var(--color-ink-muted); margin-top: 4px; }

/* Coverage */
.coverage-filter {
  background: white;
  border: 1px solid var(--color-rule);
  border-radius: 10px;
  padding: 10px 16px;
}
.coverage-select {
  font-size: 14px;
  color: var(--color-accent-deep);
  background: var(--color-accent-pale);
  border: 1px solid var(--color-accent);
  border-radius: 6px;
  padding: 5px 28px 5px 10px;
  cursor: pointer;
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%230d7377'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 8px center;
  max-width: 320px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Sources table */
.source-table { width: 100%; border-collapse: collapse; }
.source-table th {
  text-align: left;
  padding: 10px 12px;
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: var(--color-ink-muted);
  border-bottom: 1px solid var(--color-rule);
}
.source-table td {
  padding: 12px 12px;
  font-size: 14px;
  border-bottom: 1px solid var(--color-rule-light);
}
.source-table tr:hover td { background: var(--color-rule-light); }

.status-badge { font-size: 14px; padding: 3px 10px; border-radius: 4px; font-weight: 500; }
.status-completed { background: var(--color-ok-bg); color: var(--color-ok); }
.status-pending { background: var(--color-warn-bg); color: var(--color-warn); }
.status-failed { background: var(--color-err-bg); color: var(--color-err); }

.curation-badge { font-size: 14px; font-family: monospace; padding: 3px 10px; border-radius: 4px; }
.curation-done { background: var(--color-ok-bg); color: var(--color-ok); border: 1px solid #a7f3d0; }
.curation-partial { background: var(--color-warn-bg); color: var(--color-warn); border: 1px solid #fcd34d; }
.curation-none { background: var(--color-rule-light); color: var(--color-ink-muted); border: 1px solid var(--color-rule); }

/* Gaps table */
.gaps-table { width: 100%; border-collapse: collapse; }
.gaps-table th {
  text-align: left;
  padding: 10px 12px;
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: var(--color-ink-muted);
  border-bottom: 1px solid var(--color-rule);
}
.gaps-table td {
  padding: 12px 12px;
  font-size: 14px;
  border-bottom: 1px solid var(--color-rule-light);
}
.gaps-table tr:hover td { background: var(--color-rule-light); }
.gap-title { color: var(--color-ink); font-weight: 500; }
.gap-doi { font-size: 14px; color: var(--color-accent); text-decoration: none; }
.gap-doi:hover { text-decoration: underline; }
.gap-refs { font-family: monospace; font-size: 14px; color: var(--color-warn); text-align: center; }
</style>
