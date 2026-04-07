<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { curation, library, userProjects, type OutlineSection } from '../api/client'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.projectId as string)
const citekey = computed(() => route.params.citekey as string)

// Source info
const sourceTitle = ref('')
const sourceAuthors = ref('')
const sourceYear = ref<number | null>(null)

// Fragments
interface Fragment {
  fragment_id: string
  text: string
  citation_intent: string
  page: number | null
  citekey: string
}

const allFragments = ref<Fragment[]>([])
const totalCount = ref(0)
const loading = ref(true)

// Verdicts & notes (local state, synced to API)
const verdicts = ref<Record<string, 'accepted' | 'rejected'>>({})
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
  return y ? `${short}, ${y}` : short
})

async function loadData() {
  loading.value = true
  try {
    // Load source info
    const src = await library.get(citekey.value)
    sourceTitle.value = src.title || ''
    sourceAuthors.value = src.authors || ''
    sourceYear.value = src.year

    // Load outline
    const projList = await userProjects.list()
    const proj = projList.projects.find(p => p.project_id === projectId.value)
    if (proj?.outline) outline.value = proj.outline

    // Load pending fragments
    const data = await curation.pending(projectId.value, citekey.value)
    allFragments.value = data.fragments
    totalCount.value = data.total

    // Load already-curated for this source to show them too
    const curated = await curation.curated(projectId.value, { citekey: citekey.value })
    for (const c of curated.fragments) {
      verdicts.value[c.fragment_id] = c.verdict as 'accepted' | 'rejected'
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
        })
      }
    }
  } catch (e) {
    console.error('Failed to load fragment review data', e)
  } finally {
    loading.value = false
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
  // Re-curate as a way to "undo" — but we need a delete. For now, just reload.
  // The backend doesn't have a delete endpoint, so we'll just refresh the view.
}

function setSection(fragmentId: string, section: string) {
  assignedSections.value[fragmentId] = section
  if (verdicts.value[fragmentId]) {
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
  if (verdicts.value[fragmentId]) {
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

onMounted(loadData)
</script>

<template>
  <div class="min-h-screen bg-[#faf9f7] text-[#1a1a2e]">
    <!-- Topbar -->
    <div class="h-12 bg-white border-b border-[#e8e5df] flex items-center px-5 gap-3">
      <router-link
        :to="`/${projectId}/library`"
        class="font-bold text-[15px] tracking-tight text-[#1a1a2e] no-underline"
      >k<span class="text-[#0d7377]">lemma</span></router-link>
      <div class="w-px h-5 bg-[#e8e5df]" />
      <div class="text-[13px] text-[#6b6b8a] flex items-center gap-1.5">
        <router-link :to="`/${projectId}/library`" class="text-[#0d7377] no-underline hover:underline">Библиотека</router-link>
        <span>&rsaquo;</span>
        <router-link :to="`/${projectId}/library/${citekey}`" class="text-[#0d7377] no-underline hover:underline">{{ sourceTitle || citekey }}</router-link>
        <span>&rsaquo;</span>
        <span class="text-[#1a1a2e]">Отбор цитат</span>
      </div>
    </div>

    <!-- Content -->
    <div class="max-w-[780px] mx-auto py-6 px-5">
      <!-- Header -->
      <div class="mb-5">
        <h1 class="text-lg font-semibold mb-1">Отбор цитат</h1>
        <div class="text-[13px] text-[#6b6b8a]">
          {{ sourceDisplay }} &mdash; {{ sourceTitle }} &middot; {{ totalCount }} фрагментов
        </div>
      </div>

      <!-- Progress -->
      <div class="bg-[#e8e5df] rounded-full h-1.5 mb-1.5 overflow-hidden">
        <div
          class="h-full rounded-full bg-gradient-to-r from-[#0d7377] to-[#10b981] transition-all duration-400"
          :style="{ width: stats.pct + '%' }"
        />
      </div>
      <div class="flex justify-between text-xs text-[#6b6b8a] mb-5">
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
          class="px-3 py-1 rounded-md text-xs font-medium border cursor-pointer transition-all"
          :class="activeFilter === filter
            ? 'bg-[#0d7377] text-white border-[#0d7377]'
            : 'bg-white text-[#6b6b8a] border-[#e8e5df] hover:border-[#ccc] hover:text-[#1a1a2e]'"
          @click="activeFilter = filter"
        >{{ filter === 'all' ? 'Все' : intentLabel[filter] || filter }}</button>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="text-center py-12 text-[#6b6b8a]">Загрузка фрагментов...</div>

      <!-- Cards -->
      <div v-else>
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
                class="text-[11px] font-medium px-2 py-0.5 rounded"
                :class="intentColor[f.citation_intent] || 'bg-gray-100 text-gray-600'"
              >{{ intentLabel[f.citation_intent] || f.citation_intent }}</span>
              <span v-if="f.page" class="font-mono text-[11px] text-[#6b6b8a]">стр. {{ f.page }}</span>
              <select
                class="text-[11px] text-[#065a5e] bg-[#e6f3f3] border border-[#0d7377] rounded py-0.5 pl-1.5 pr-4 cursor-pointer appearance-none"
                :value="assignedSections[f.fragment_id] || ''"
                @change="setSection(f.fragment_id, ($event.target as HTMLSelectElement).value)"
                :style="selectArrowStyle"
              >
                <option value="">— раздел —</option>
                <option v-for="s in outline" :key="s.id" :value="s.id">{{ s.name }}</option>
              </select>
            </div>

            <!-- Note area -->
            <div v-if="openNotes[f.fragment_id]" class="mt-2">
              <template v-if="notes[f.fragment_id] && !editingNote[f.fragment_id]">
                <div class="text-xs text-[#6b6b8a] italic leading-6">
                  {{ notes[f.fragment_id] }}
                  <button class="text-[#0d7377] not-italic cursor-pointer ml-1" @click="startEditNote(f.fragment_id)">(изм.)</button>
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
              class="text-[11px] text-[#0d7377] cursor-pointer border-none bg-transparent p-0 hover:underline"
              @click="toggleNote(f.fragment_id)"
            >добавить заметку &rarr;</button>
          </div>

          <!-- Actions -->
          <div class="flex gap-2 px-4 py-3 border-t border-[#f0ede8]">
            <template v-if="verdicts[f.fragment_id]">
              <div
                class="text-xs font-semibold flex items-center gap-1 flex-1"
                :class="verdicts[f.fragment_id] === 'accepted' ? 'text-[#2d6a4f]' : 'text-[#c62828]'"
              >
                {{ verdicts[f.fragment_id] === 'accepted' ? '✓ Принято' : '✗ Отклонено' }}
              </div>
              <button
                class="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs cursor-pointer border border-[#e8e5df] bg-white text-[#6b6b8a] hover:bg-[#f0ede8]"
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
      </div>
    </div>

    <!-- Bottom summary -->
    <div class="sticky bottom-0 bg-white border-t border-[#e8e5df] py-3 px-5 flex items-center justify-center gap-5 text-[13px] z-10">
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
