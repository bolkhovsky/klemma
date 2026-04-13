<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { curation, library, process, userProjects, type OutlineSection } from '../api/client'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.projectId as string)
const citekey = computed(() => route.params.citekey as string)

// Source info
const sourceTitle = ref('')
const sourceAuthors = ref('')
const sourceYear = ref<number | null>(null)
const sourceStatus = ref<string>('pending')

// Processing state
const processing = ref(false)
const jobId = ref<string | null>(null)
const jobStatus = ref('')
const jobError = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// Fragments
interface Fragment {
  fragment_id: string
  text: string
  citation_intent: string
  page: number | null
  citekey: string
  verbatim: boolean
}

const allFragments = ref<Fragment[]>([])
const totalCount = ref(0)
const loading = ref(true)
const deleting = ref(false)

// Verdicts & notes (local state, synced to API)
const verdicts = ref<Record<string, 'accepted' | 'rejected'>>({})
const suggestedIds = ref<Set<string>>(new Set())
const assignedSections = ref<Record<string, string>>({})
const notes = ref<Record<string, string>>({})
const editingNote = ref<Record<string, boolean>>({})
const openNotes = ref<Record<string, boolean>>({})

// Outline sections
const outline = ref<OutlineSection[]>([])

// Filter
const activeFilter = ref('all')

const intentLabel: Record<string, string> = {
  background: 'фон',
  method: 'метод',
  result_comparison: 'результат',
  extends: 'расширяет',
  contrasts: 'контраст',
  uses_data: 'данные',
}

const selectArrowStyle = {
  backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%230d7377'/%3E%3C/svg%3E\")",
  backgroundRepeat: 'no-repeat',
  backgroundPosition: 'right 4px center',
}

const intentColor: Record<string, string> = {
  background: 'bg-[#dbeafe] text-[#1d4ed8]',
  method: 'bg-[#ede9fe] text-[#6d28d9]',
  result_comparison: 'bg-[#dcfce7] text-[#15803d]',
  extends: 'bg-[#ccfbf1] text-[#0f766e]',
  contrasts: 'bg-[#ffedd5] text-[#c2410c]',
  uses_data: 'bg-[#fef9c3] text-[#a16207]',
}

const filteredFragments = computed(() => {
  if (activeFilter.value === 'all') return allFragments.value
  return allFragments.value.filter(f => f.citation_intent === activeFilter.value)
})

const stats = computed(() => {
  const accepted = Object.values(verdicts.value).filter(v => v === 'accepted').length
  const rejected = Object.values(verdicts.value).filter(v => v === 'rejected').length
  const curated = accepted + rejected
  const pending = totalCount.value - curated
  const pct = totalCount.value > 0 ? Math.round((curated / totalCount.value) * 100) : 0
  return { accepted, rejected, curated, pending, pct }
})

const sourceDisplay = computed(() => {
  const a = sourceAuthors.value
  const y = sourceYear.value
  if (!a) return citekey.value
  const short = a.includes(',') ? a.split(',')[0]!.trim() + ' et al.' : a
  return y ? `${short} (${y})` : short
})

// Whether to show the review UI (source processed with fragments)
const hasFragments = computed(() => sourceStatus.value === 'completed' && totalCount.value > 0)
const isPending = computed(() => sourceStatus.value === 'pending' && !processing.value)
const isProcessing = computed(() => processing.value || sourceStatus.value === 'processing')

async function loadData() {
  loading.value = true
  try {
    // Load source info
    const src = await library.get(citekey.value)
    sourceTitle.value = src.title || ''
    sourceAuthors.value = src.authors || ''
    sourceYear.value = src.year
    sourceStatus.value = src.status || 'pending'

    // Load outline
    const projList = await userProjects.list()
    const proj = projList.projects.find(p => p.project_id === projectId.value)
    if (proj?.outline) outline.value = proj.outline

    // Only load fragments if source is processed
    if (sourceStatus.value === 'completed') {
      // Load pending fragments
      const data = await curation.pending(projectId.value, citekey.value)
      allFragments.value = data.fragments
      totalCount.value = data.total

      // Pre-populate sections from backend suggestions
      for (const f of data.fragments) {
        if ((f as any).suggested_section && !assignedSections.value[f.fragment_id]) {
          assignedSections.value[f.fragment_id] = (f as any).suggested_section
        }
      }

      // Load already-curated for this source to show them too
      const curated = await curation.curated(projectId.value, { citekey: citekey.value })
      for (const c of curated.fragments) {
        if (c.verdict === 'suggested') {
          // Suggested stays "pending" in review — pre-fill section from suggestion
          if (c.assigned_section && !assignedSections.value[c.fragment_id]) {
            assignedSections.value[c.fragment_id] = c.assigned_section
          }
          suggestedIds.value.add(c.fragment_id)
        } else {
          verdicts.value[c.fragment_id] = c.verdict as 'accepted' | 'rejected'
        }
        if (c.assigned_section) assignedSections.value[c.fragment_id] = c.assigned_section
        if (c.note) {
          notes.value[c.fragment_id] = c.note
          openNotes.value[c.fragment_id] = true
        }
        // Add to allFragments if not already there
        if (!allFragments.value.find(f => f.fragment_id === c.fragment_id)) {
          allFragments.value.push({
            fragment_id: c.fragment_id,
            text: c.text,
            citation_intent: c.citation_intent,
            page: null,
            citekey: c.citekey,
            verbatim: false,
          })
        }
      }
    }
  } catch (e) {
    console.error('Failed to load fragment review data', e)
  } finally {
    loading.value = false
  }
}

async function startProcessing(force = false) {
  processing.value = true
  jobError.value = ''
  jobStatus.value = 'queued'
  try {
    const resp = await process.submit(citekey.value, { projectId: projectId.value, force })
    jobId.value = resp.job_id
    startPolling()
  } catch (e: any) {
    jobError.value = e?.message || 'Ошибка запуска обработки'
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
        // Task may return {status: "error", detail: "..."} even when RQ job is "finished"
        if (resp.result?.status === 'error') {
          jobError.value = resp.result.detail || 'Обработка завершилась с ошибкой'
          sourceStatus.value = 'failed'
        } else {
          sourceStatus.value = 'completed'
          await loadData()
        }
      } else if (resp.status === 'failed') {
        stopPolling()
        processing.value = false
        jobError.value = resp.result?.detail || 'Обработка завершилась с ошибкой'
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

async function setVerdict(fragmentId: string, verdict: 'accepted' | 'rejected') {
  verdicts.value[fragmentId] = verdict
  const frag = allFragments.value.find(f => f.fragment_id === fragmentId)
  if (!frag) return
  await curation.curate(projectId.value, [{
    fragment_id: fragmentId,
    citekey: frag.citekey,
    verdict,
    assigned_section: assignedSections.value[fragmentId] || undefined,
    note: notes.value[fragmentId] || undefined,
  }])
}

async function undo(fragmentId: string) {
  delete verdicts.value[fragmentId]
  delete notes.value[fragmentId]
  delete openNotes.value[fragmentId]
}

function setSection(fragmentId: string, section: string) {
  assignedSections.value[fragmentId] = section
  if (verdicts.value[fragmentId] || suggestedIds.value.has(fragmentId)) {
    curation.update(projectId.value, fragmentId, { assigned_section: section })
  }
}

function toggleNote(fragmentId: string) {
  openNotes.value[fragmentId] = !openNotes.value[fragmentId]
}

function startEditNote(fragmentId: string) {
  editingNote.value[fragmentId] = true
}

async function saveNote(fragmentId: string, text: string) {
  if (!text.trim()) return
  notes.value[fragmentId] = text.trim()
  editingNote.value[fragmentId] = false
  if (verdicts.value[fragmentId] || suggestedIds.value.has(fragmentId)) {
    await curation.update(projectId.value, fragmentId, { note: text.trim() })
  }
}

async function acceptAllRemaining() {
  const pending = allFragments.value.filter(f => !verdicts.value[f.fragment_id])
  const decisions = pending.map(f => ({
    fragment_id: f.fragment_id,
    citekey: f.citekey,
    verdict: 'accepted' as const,
    assigned_section: assignedSections.value[f.fragment_id] || undefined,
    note: notes.value[f.fragment_id] || undefined,
  }))
  for (const f of pending) verdicts.value[f.fragment_id] = 'accepted'
  if (decisions.length > 0) {
    await curation.curate(projectId.value, decisions)
  }
}

async function deleteSource() {
  if (!confirm('Удалить источник из библиотеки? Это действие нельзя отменить.')) return
  deleting.value = true
  try {
    await library.remove(citekey.value)
    router.push(`/${projectId.value}/library`)
  } catch (e: any) {
    alert(e?.message || 'Ошибка удаления')
    deleting.value = false
  }
}

onMounted(loadData)
onUnmounted(stopPolling)
</script>

<template>
  <div class="w-full overflow-y-auto" style="background: var(--color-paper-bg, #faf9f7)">
    <!-- Content -->
    <div class="max-w-[780px] mx-auto py-6 px-5">
      <!-- Breadcrumb -->
      <div class="mb-5">
        <router-link :to="`/${projectId}/library`" class="text-sm text-[#0d7377] no-underline hover:underline">&larr; Библиотека</router-link>
      </div>
      <!-- Loading -->
      <div v-if="loading" class="flex items-center justify-center py-24">
        <div class="h-5 w-5 animate-spin rounded-full border-2 border-[#0d7377] border-t-transparent"></div>
      </div>

      <template v-else>
        <!-- Header -->
        <div class="mb-5">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h1 class="text-lg font-semibold mb-1">{{ sourceTitle || citekey }}</h1>
              <div class="text-sm text-[#6b6b8a]">
                {{ sourceDisplay }}
                <template v-if="totalCount > 0"> &middot; {{ totalCount }} фрагментов</template>
              </div>
            </div>
            <button
              @click="deleteSource"
              :disabled="deleting"
              class="shrink-0 mt-1 p-1.5 rounded-md text-[#6b6b8a] hover:text-[#c62828] hover:bg-[#fff0f0] transition-colors cursor-pointer border-none bg-transparent"
              title="Удалить источник"
            >
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
              </svg>
            </button>
          </div>
        </div>

        <!-- Processing state: pending -->
        <div v-if="isPending" class="rounded-xl border-2 border-dashed border-[#e8e5df] p-12 text-center">
          <div class="mx-auto w-12 h-12 rounded-xl bg-[#e6f3f3] flex items-center justify-center mb-4">
            <svg class="w-6 h-6 text-[#0d7377]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
            </svg>
          </div>
          <h3 class="text-lg font-semibold text-[#1a1a2e]">Источник ещё не обработан</h3>
          <p class="mt-2 text-sm text-[#6b6b8a]">Нажмите «Обработать», чтобы извлечь фрагменты из PDF.</p>
          <button
            @click="startProcessing(false)"
            class="mt-5 inline-flex items-center gap-2 rounded-lg bg-[#0d7377] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[#065a5e] transition-colors cursor-pointer border-none"
          >Обработать</button>
        </div>

        <!-- Processing state: in progress -->
        <div v-if="isProcessing" class="rounded-xl border border-[#0d7377] bg-[#e6f3f3] p-5">
          <div class="flex items-center gap-3">
            <div class="h-4 w-4 animate-spin rounded-full border-2 border-[#0d7377] border-t-transparent"></div>
            <span class="text-sm font-medium text-[#065a5e]">Извлекаем фрагменты из PDF...</span>
            <span class="text-[13px] text-[#6b6b8a]">{{ jobStatus }}</span>
          </div>
        </div>

        <!-- Job error -->
        <div v-if="jobError" class="mt-3 rounded-xl border border-[#c62828] bg-[#fff0f0] p-4">
          <p class="text-sm text-[#c62828]">{{ jobError }}</p>
          <button
            @click="startProcessing(true)"
            class="mt-2 text-sm text-[#0d7377] cursor-pointer border-none bg-transparent hover:underline"
          >Попробовать снова</button>
        </div>

        <!-- Failed state -->
        <div v-if="sourceStatus === 'failed' && !processing && !jobError" class="rounded-xl border border-[#c62828] bg-[#fff0f0] p-5 text-center">
          <p class="text-sm text-[#c62828] mb-3">Обработка завершилась с ошибкой</p>
          <button
            @click="startProcessing(true)"
            class="inline-flex items-center gap-2 rounded-lg bg-[#0d7377] px-4 py-2 text-sm font-semibold text-white hover:bg-[#065a5e] transition-colors cursor-pointer border-none"
          >Переобработать</button>
        </div>

        <!-- Completed with no fragments -->
        <div v-if="sourceStatus === 'completed' && totalCount === 0 && !loading" class="rounded-xl border-2 border-dashed border-[#e8e5df] p-12 text-center">
          <p class="text-sm text-[#6b6b8a]">Фрагменты не найдены.</p>
          <button
            @click="startProcessing(true)"
            class="mt-3 text-sm text-[#0d7377] cursor-pointer border-none bg-transparent hover:underline"
          >Переобработать</button>
        </div>

        <!-- Fragment review UI (matching prototype exactly) -->
        <template v-if="hasFragments">
          <!-- Progress -->
          <div class="bg-[#e8e5df] rounded-full h-1.5 mb-1.5 overflow-hidden">
            <div
              class="h-full rounded-full bg-gradient-to-r from-[#0d7377] to-[#10b981] transition-all duration-400"
              :style="{ width: stats.pct + '%' }"
            />
          </div>
          <div class="flex justify-between text-[13px] text-[#6b6b8a] mb-5">
            <span>Отобрано: {{ stats.curated }} из {{ totalCount }}</span>
            <span>
              <span class="text-[#2d6a4f] font-semibold">{{ stats.accepted }}</span> принято &middot;
              <span class="text-[#c62828] font-semibold">{{ stats.rejected }}</span> отклонено
            </span>
          </div>

          <!-- Filters -->
          <div class="flex gap-1.5 mb-4 flex-wrap">
            <button
              v-for="filter in ['all', 'background', 'method', 'result_comparison', 'extends', 'contrasts', 'uses_data']"
              :key="filter"
              class="px-3 py-1 rounded-md text-[13px] font-medium border cursor-pointer transition-all"
              :class="activeFilter === filter
                ? 'bg-[#0d7377] text-white border-[#0d7377]'
                : 'bg-white text-[#6b6b8a] border-[#e8e5df] hover:border-[#ccc] hover:text-[#1a1a2e]'"
              @click="activeFilter = filter"
            >{{ filter === 'all' ? 'Все' : intentLabel[filter] || filter }}</button>
          </div>

          <!-- Cards -->
          <div
            v-for="f in filteredFragments"
            :key="f.fragment_id"
            class="bg-white border rounded-[10px] mb-3 overflow-hidden transition-all"
            :class="{
              'border-[#86efac] bg-[#f0fdf4]': verdicts[f.fragment_id] === 'accepted',
              'border-[#fca5a5] bg-[#fef2f2] opacity-70': verdicts[f.fragment_id] === 'rejected',
              'border-[#e8e5df] hover:border-[#d4d0ca]': !verdicts[f.fragment_id],
            }"
          >
            <div class="p-4">
              <div class="text-sm leading-7 text-[#3d3d5c] mb-3">{{ f.text }}</div>
              <div class="flex items-center gap-1.5 flex-wrap mb-3">
                <span
                  v-if="f.citation_intent"
                  class="text-[12px] font-medium px-2 py-0.5 rounded"
                  :class="intentColor[f.citation_intent] || 'bg-gray-100 text-gray-600'"
                >{{ intentLabel[f.citation_intent] || f.citation_intent }}</span>
                <span
                  v-if="f.verbatim"
                  class="text-[12px] font-medium px-2 py-0.5 rounded bg-[#dcfce7] text-[#15803d]"
                  title="Дословная цитата из источника"
                >📜 цитата</span>
                <span
                  v-else
                  class="text-[12px] font-medium px-2 py-0.5 rounded bg-gray-100 text-[#6b6b8a] border border-[#fbbf24]"
                  title="Парафраз — проверьте перед цитированием"
                >✏️ парафраз</span>
                <span v-if="f.page" class="font-mono text-[12px] text-[#6b6b8a]">стр. {{ f.page }}</span>
                <select
                  class="text-[13px] text-[#065a5e] bg-[#e6f3f3] border border-[#0d7377] rounded py-1 pl-2 pr-6 cursor-pointer appearance-none max-w-[220px] truncate"
                  :value="assignedSections[f.fragment_id] || ''"
                  @change="setSection(f.fragment_id, ($event.target as HTMLSelectElement).value)"
                  :style="selectArrowStyle"
                >
                  <option value="">— раздел —</option>
                  <option v-for="s in outline" :key="s.id" :value="s.id">{{ s.id }}. {{ s.name }}</option>
                </select>
              </div>

              <!-- Note area -->
              <div v-if="openNotes[f.fragment_id]" class="mt-2">
                <template v-if="notes[f.fragment_id] && !editingNote[f.fragment_id]">
                  <div class="text-[13px] text-[#6b6b8a] italic leading-6">
                    {{ notes[f.fragment_id] }}
                    <button class="text-[#0d7377] not-italic cursor-pointer ml-1 border-none bg-transparent" @click="startEditNote(f.fragment_id)">(изм.)</button>
                  </div>
                </template>
                <template v-else>
                  <textarea
                    class="w-full border border-[#e8e5df] rounded-md p-2 text-[13px] font-sans resize-y min-h-12 text-[#3d3d5c] focus:outline-none focus:border-[#0d7377]"
                    placeholder="Как использовать эту цитату..."
                    :value="notes[f.fragment_id] || ''"
                    @blur="saveNote(f.fragment_id, ($event.target as HTMLTextAreaElement).value)"
                  />
                </template>
              </div>
              <button
                v-if="!openNotes[f.fragment_id]"
                class="text-[13px] text-[#0d7377] cursor-pointer border-none bg-transparent p-0 hover:underline"
                @click="toggleNote(f.fragment_id)"
              >добавить заметку &rarr;</button>
            </div>

            <!-- Actions -->
            <div class="flex gap-2 px-4 py-3 border-t border-[#f0ede8]">
              <template v-if="verdicts[f.fragment_id]">
                <div
                  class="text-[13px] font-semibold flex items-center gap-1 flex-1"
                  :class="verdicts[f.fragment_id] === 'accepted' ? 'text-[#2d6a4f]' : 'text-[#c62828]'"
                >
                  {{ verdicts[f.fragment_id] === 'accepted' ? '✓ Принято' : '✗ Отклонено' }}
                </div>
                <button
                  class="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-[13px] cursor-pointer border border-[#e8e5df] bg-white text-[#6b6b8a] hover:bg-[#f0ede8]"
                  @click="undo(f.fragment_id)"
                >Отменить</button>
              </template>
              <template v-else>
                <button
                  class="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border-none bg-[#2d6a4f] text-white hover:bg-[#1b5e3a]"
                  @click="setVerdict(f.fragment_id, 'accepted')"
                >✓ Принять</button>
                <button
                  class="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer bg-white text-[#c62828] border border-[#fca5a5] hover:bg-[#fff0f0]"
                  @click="setVerdict(f.fragment_id, 'rejected')"
                >✗ Отклонить</button>
              </template>
            </div>
          </div>

          <!-- Batch accept -->
          <button
            v-if="stats.pending > 0"
            class="w-full mt-2 py-2.5 rounded-lg text-[13px] font-medium cursor-pointer border-none bg-[#0d7377] text-white hover:bg-[#065a5e] flex items-center justify-center"
            @click="acceptAllRemaining"
          >Принять все оставшиеся</button>

          <!-- Reprocess button for completed sources -->
          <button
            @click="startProcessing(true)"
            class="w-full mt-3 py-2 rounded-lg text-[13px] font-medium cursor-pointer bg-white text-[#6b6b8a] border border-[#e8e5df] hover:text-[#0d7377] hover:border-[#0d7377] transition-colors"
          >Переобработать</button>
        </template>
      </template>
    </div>

    <!-- Bottom summary (only when fragments exist) -->
    <div v-if="hasFragments" class="sticky bottom-0 bg-white border-t border-[#e8e5df] py-3 px-5 flex items-center justify-center gap-5 text-[13px] z-10">
      <div class="flex items-center gap-1">
        <div class="w-2 h-2 rounded-full bg-[#2d6a4f]" />
        {{ stats.accepted }} принято
      </div>
      <div class="flex items-center gap-1">
        <div class="w-2 h-2 rounded-full bg-[#c62828]" />
        {{ stats.rejected }} отклонено
      </div>
      <div class="flex items-center gap-1">
        <div class="w-2 h-2 rounded-full bg-[#e8e5df]" />
        {{ stats.pending }} осталось
      </div>
    </div>
  </div>
</template>
