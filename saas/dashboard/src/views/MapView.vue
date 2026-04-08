<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { curation, library } from '@/api/client'

const route = useRoute()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.projectId as string || projectStore.activeProjectId || '')

// ── Types ───────────────────────────────────────────────────────────────────
interface CuratedFrag {
  fragment_id: string
  citekey: string
  text: string
  citation_intent: string
  assigned_section: string | null
  note: string | null
  verdict: string
  curated_at: string
  source_display?: string
  _layer?: 'accepted' | 'suggested'
}

// ── State ───────────────────────────────────────────────────────────────────
const fragments = ref<CuratedFrag[]>([])
const totalAccepted = ref(0)
const loading = ref(true)
const sourceCache = ref<Record<string, { title: string; authors: string; year: number | null }>>({})

// Selected section + tab
const selectedSectionId = ref<string>('')
const activeTab = ref<'citations' | 'suggest'>('citations')

// Fragment actions
const openMenuId = ref<string | null>(null)
const confirmId = ref<string | null>(null)
const moveFragmentId = ref<string | null>(null)

// Expand/collapse for >3 fragments
const COLLAPSE_THRESHOLD = 3
const expandedSection = ref(false)

// Suggestions
const suggestions = ref<any[]>([])
const gapAlert = ref<{ missing_intents: string[]; message: string } | null>(null)
const suggestLoading = ref(false)

// ── Intent labels & colors ──────────────────────────────────────────────────
const intentLabel: Record<string, string> = {
  background: 'фон', method: 'метод', result_comparison: 'результат',
  extends: 'расширяет', contrasts: 'контраст', uses_data: 'данные',
}
const intentColor: Record<string, string> = {
  background: 'bg-[#dbeafe] text-[#1d4ed8]',
  method: 'bg-[#ede9fe] text-[#6d28d9]',
  result_comparison: 'bg-[#dcfce7] text-[#15803d]',
  extends: 'bg-[#ccfbf1] text-[#0f766e]',
  contrasts: 'bg-[#ffedd5] text-[#c2410c]',
  uses_data: 'bg-[#fef9c3] text-[#a16207]',
}

// ── Computed ────────────────────────────────────────────────────────────────
const outline = computed(() => projectStore.activeOutline || [])

// Count of curated fragments per section
const sectionCounts = computed(() => {
  const counts: Record<string, number> = {}
  for (const f of fragments.value) {
    const sec = f.assigned_section || ''
    counts[sec] = (counts[sec] || 0) + 1
  }
  return counts
})

const emptySectionCount = computed(() =>
  outline.value.filter(s => !sectionCounts.value[s.id]).length
)

// Unique citekeys per section (for readiness calculation)
const sectionCitekeys = computed(() => {
  const map: Record<string, Set<string>> = {}
  for (const f of fragments.value) {
    const sec = f.assigned_section || ''
    if (!map[sec]) map[sec] = new Set()
    map[sec]!.add(f.citekey)
  }
  return map
})

function sectionReadiness(id: string): 'ready' | 'partial' | 'empty' {
  const count = sectionCounts.value[id] || 0
  const sources = sectionCitekeys.value[id]?.size || 0
  if (count === 0) return 'empty'
  if (count >= 5 && sources >= 3) return 'ready'
  return 'partial'
}

const readinessPct = computed(() => {
  if (outline.value.length === 0) return 0
  const ready = outline.value.filter(s => sectionReadiness(s.id) === 'ready').length
  return Math.round((ready / outline.value.length) * 100)
})

async function acceptSuggestion(f: CuratedFrag) {
  await curation.curate(projectId.value, [{
    fragment_id: f.fragment_id,
    citekey: f.citekey,
    verdict: 'accepted',
    assigned_section: f.assigned_section || selectedSectionId.value,
  }])
  await loadData()
}

// Sections for the left panel: outline + unassigned
const sectionList = computed(() => {
  const list = outline.value.map(s => ({
    id: s.id,
    name: s.name,
    count: sectionCounts.value[s.id] || 0,
  }))
  const unassigned = sectionCounts.value[''] || 0
  if (unassigned > 0) {
    list.push({ id: '', name: 'Не распределены', count: unassigned })
  }
  return list
})

// Fragments for currently selected section
const selectedFragments = computed(() =>
  fragments.value.filter(f => (f.assigned_section || '') === selectedSectionId.value)
)

const selectedSection = computed(() =>
  sectionList.value.find(s => s.id === selectedSectionId.value)
)

// Visible fragments (expand/collapse)
const visibleFragments = computed(() => {
  if (selectedFragments.value.length <= COLLAPSE_THRESHOLD || expandedSection.value) {
    return selectedFragments.value
  }
  return selectedFragments.value.slice(0, COLLAPSE_THRESHOLD)
})

const hiddenCount = computed(() => {
  if (selectedFragments.value.length <= COLLAPSE_THRESHOLD || expandedSection.value) return 0
  return selectedFragments.value.length - COLLAPSE_THRESHOLD
})

function pluralCitation(n: number): string {
  const mod10 = n % 10, mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 'цитата'
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'цитаты'
  return 'цитат'
}

// ── Source display ──────────────────────────────────────────────────────────
function sourceDisplay(f: CuratedFrag): string {
  const cached = sourceCache.value[f.citekey]
  if (!cached) return f.citekey
  const a = cached.authors
  if (!a) return cached.title || f.citekey
  const short = a.includes(',') ? a.split(',')[0]!.trim() + ' et al.' : a
  return cached.year ? `${short}, ${cached.year}` : short
}

// ── Data loading ────────────────────────────────────────────────────────────
async function loadData() {
  if (!projectId.value) return
  loading.value = true
  try {
    const [accepted, suggested] = await Promise.all([
      curation.curated(projectId.value, { verdict: 'accepted' }),
      curation.curated(projectId.value, { verdict: 'suggested' }),
    ])
    fragments.value = [
      ...accepted.fragments.map(f => ({ ...f, _layer: 'accepted' as const })),
      ...suggested.fragments.map(f => ({ ...f, _layer: 'suggested' as const })),
    ]
    totalAccepted.value = accepted.total

    // Auto-select first section if none selected
    if (!selectedSectionId.value && outline.value.length > 0) {
      selectedSectionId.value = outline.value[0]!.id
    }

    // Fetch source metadata
    const citekeys = [...new Set(data.fragments.map(f => f.citekey))]
    for (const ck of citekeys) {
      if (!sourceCache.value[ck]) {
        try {
          const src = await library.get(ck)
          sourceCache.value[ck] = { title: src.title || '', authors: src.authors || '', year: src.year }
        } catch { /* skip */ }
      }
    }
  } catch (e) {
    console.error('Failed to load curated fragments', e)
  } finally {
    loading.value = false
  }
}

// ── Section selection ───────────────────────────────────────────────────────
function selectSection(sectionId: string) {
  selectedSectionId.value = sectionId
  activeTab.value = 'citations'
  expandedSection.value = false
  suggestions.value = []
  gapAlert.value = null
  closeMenus()
}

// ── Tab switching ───────────────────────────────────────────────────────────
async function switchTab(tab: 'citations' | 'suggest') {
  activeTab.value = tab
  if (tab === 'suggest' && suggestions.value.length === 0) {
    await loadSuggestions()
  }
}

async function loadSuggestions() {
  if (!selectedSectionId.value) return
  suggestLoading.value = true
  try {
    const data = await curation.suggest(projectId.value, selectedSectionId.value)
    suggestions.value = data.suggestions
    gapAlert.value = data.gap_alert
  } catch (e) {
    console.error('Failed to load suggestions', e)
    suggestions.value = []
    gapAlert.value = null
  } finally {
    suggestLoading.value = false
  }
}

// ── Fragment actions ────────────────────────────────────────────────────────
function toggleMenu(fragId: string) {
  openMenuId.value = openMenuId.value === fragId ? null : fragId
}

function closeMenus() {
  openMenuId.value = null
}

function showMove(fragId: string) {
  closeMenus()
  moveFragmentId.value = fragId
}

async function moveToSection(fragId: string, sectionId: string) {
  await curation.update(projectId.value, fragId, { assigned_section: sectionId })
  moveFragmentId.value = null
  await loadData()
}

function showConfirm(fragId: string) {
  closeMenus()
  confirmId.value = fragId
}

async function excludeFragment(fragId: string) {
  await curation.update(projectId.value, fragId, { verdict: 'rejected' })
  confirmId.value = null
  await loadData()
}

async function addSuggested(s: any) {
  await curation.curate(projectId.value, [{
    fragment_id: s.fragment_id,
    citekey: s.citekey,
    verdict: 'accepted',
    assigned_section: selectedSectionId.value,
  }])
  s._added = true
  await loadData()
}

// ── Lifecycle ───────────────────────────────────────────────────────────────
onMounted(async () => {
  if (!projectStore.activeProject) await projectStore.loadProjects()
  await loadData()
})

watch(projectId, loadData)
</script>

<template>
  <!-- Loading -->
  <div v-if="loading" class="flex-1 flex items-center justify-center text-[#6b6b8a]">Загрузка...</div>

  <!-- Empty state: no outline -->
  <div v-else-if="outline.length === 0 && fragments.length === 0" class="flex-1 flex items-center justify-center">
    <div class="text-center py-12">
      <div class="text-4xl mb-3">&#128218;</div>
      <h3 class="text-lg font-semibold text-[#1a1a2e] mb-2">Нет отобранных цитат</h3>
      <p class="text-[15px] text-[#6b6b8a] leading-6">
        Загрузите источник в <router-link :to="`/${projectId}/library`" class="text-[#0d7377] no-underline hover:underline">Библиотеку</router-link>
        и отберите цитаты
      </p>
    </div>
  </div>

  <!-- Two-panel layout -->
  <template v-else>
    <!-- Left: sections panel -->
    <div class="w-[272px] flex-shrink-0 bg-white overflow-y-auto" style="border-right: 1px solid #e8e5df">
      <div class="px-4 pt-4 pb-3" style="border-bottom: 1px solid #e8e5df">
        <h2 class="text-[15px] font-semibold text-[#1a1a2e] mb-0.5">Разделы</h2>
        <div class="text-xs text-[#6b6b8a] mb-1.5">{{ totalAccepted }} {{ pluralCitation(totalAccepted) }} &middot; {{ emptySectionCount }} без цитат</div>
        <div v-if="outline.length > 0" class="flex items-center gap-2">
          <div class="flex-1 h-1.5 bg-[#e8e5df] rounded-full overflow-hidden">
            <div class="h-full bg-[#0d7377] rounded-full transition-all" :style="{ width: readinessPct + '%' }"></div>
          </div>
          <span class="text-[11px] font-semibold text-[#0d7377]">{{ readinessPct }}%</span>
        </div>
      </div>

      <div
        v-for="s in sectionList"
        :key="s.id"
        class="flex items-center gap-2 px-4 py-2.5 cursor-pointer transition-colors"
        :class="s.id === selectedSectionId
          ? 'bg-[#e6f3f3] border-l-[3px] border-l-[#0d7377] pl-[13px]'
          : 'hover:bg-[#f0ede8] border-l-[3px] border-l-transparent'"
        style="border-bottom: 1px solid #f0ede8"
        @click="selectSection(s.id)"
      >
        <span
          class="font-mono text-xs font-semibold px-[7px] py-0.5 rounded flex-shrink-0"
          :class="s.id ? 'text-[#0d7377]' : 'text-[#6b6b8a]'"
          :style="s.id === selectedSectionId ? 'background: white' : 'background: #e6f3f3'"
        >{{ s.id || '?' }}</span>
        <span class="text-[13px] text-[#3d3d5c] flex-1 leading-snug">{{ s.name }}</span>
        <span v-if="s.id && sectionReadiness(s.id) === 'ready'" class="text-[10px] text-[#2e7d32] flex-shrink-0">&#9679;</span>
        <span v-else-if="s.id && sectionReadiness(s.id) === 'partial'" class="text-[10px] text-[#f9a825] flex-shrink-0">&#9679;</span>
        <span v-if="s.count > 0" class="text-xs font-semibold text-[#6b6b8a] bg-[#f0ede8] px-[7px] py-0.5 rounded-full flex-shrink-0">{{ s.count }}</span>
        <span v-else class="text-xs font-medium text-[#b45309] flex-shrink-0">0</span>
      </div>
    </div>

    <!-- Right: content panel -->
    <div class="flex-1 overflow-y-auto" style="background: #faf9f7" @click="closeMenus">
      <!-- Panel header -->
      <div class="px-6 pt-5">
        <h2 class="text-[17px] font-semibold text-[#1a1a2e] mb-0.5">
          <span v-if="selectedSection?.id" class="font-mono text-sm font-semibold text-[#0d7377] bg-[#e6f3f3] px-2.5 py-0.5 rounded mr-2">{{ selectedSection.id }}</span>
          {{ selectedSection?.name || 'Выберите раздел' }}
        </h2>
        <div class="text-[13px] text-[#6b6b8a]">{{ selectedFragments.length }} {{ pluralCitation(selectedFragments.length) }} в разделе</div>
      </div>

      <!-- Tabs -->
      <div class="flex gap-0 px-6 mt-4" style="border-bottom: 1px solid #e8e5df">
        <button
          class="px-4 py-2 text-[13px] font-medium cursor-pointer border-b-2 transition-colors bg-transparent"
          :class="activeTab === 'citations' ? 'text-[#065a5e] border-[#0d7377]' : 'text-[#6b6b8a] border-transparent hover:text-[#1a1a2e]'"
          @click="switchTab('citations')"
        >
          Цитаты
          <span class="text-[11px] font-semibold ml-1 px-1.5 py-0.5 rounded-full" :class="activeTab === 'citations' ? 'bg-[#e6f3f3] text-[#0d7377]' : 'bg-[#f0ede8] text-[#6b6b8a]'">{{ selectedFragments.length }}</span>
        </button>
        <button
          v-if="selectedSection?.id"
          class="px-4 py-2 text-[13px] font-medium cursor-pointer border-b-2 transition-colors bg-transparent"
          :class="activeTab === 'suggest' ? 'text-[#065a5e] border-[#0d7377]' : 'text-[#6b6b8a] border-transparent hover:text-[#1a1a2e]'"
          @click="switchTab('suggest')"
        >
          Подобрать
        </button>
      </div>

      <!-- Tab: Citations -->
      <div v-if="activeTab === 'citations'" class="px-6 py-4">
        <!-- Empty section -->
        <div v-if="selectedFragments.length === 0" class="text-center py-12">
          <div class="text-3xl mb-2 opacity-50">&#128237;</div>
          <p class="text-sm text-[#6b6b8a] mb-3">В этом разделе пока нет цитат</p>
          <a v-if="selectedSection?.id" class="text-sm text-[#0d7377] font-medium cursor-pointer hover:underline" @click="switchTab('suggest')">Подобрать цитаты &rarr;</a>
        </div>

        <!-- Fragment cards -->
        <template v-else>
          <div
            v-for="f in visibleFragments"
            :key="f.fragment_id"
            class="frag-card relative rounded-[10px] px-4 py-3.5 mb-2.5 transition-colors"
            :class="f._layer === 'suggested'
              ? 'bg-[#fafff9] border border-dashed border-[#b2dfdb] hover:border-[#80cbc4]'
              : 'bg-white border border-[#e8e5df] hover:border-[#d4d0ca]'"
          >
            <!-- Accept button for suggested -->
            <button
              v-if="f._layer === 'suggested'"
              class="absolute top-2.5 right-2.5 text-xs font-medium text-[#0d7377] bg-[#e6f3f3] px-3 py-1.5 rounded-lg border-none cursor-pointer hover:bg-[#b2dfdb]"
              @click.stop="acceptSuggestion(f)"
            >&#10003; Принять</button>
            <!-- Menu trigger for accepted -->
            <button
              v-else
              class="frag-menu-btn absolute top-2.5 right-2.5 w-7 h-7 rounded-md border-none bg-transparent cursor-pointer text-[16px] text-[#6b6b8a] flex items-center justify-center hover:bg-[#f0ede8] hover:text-[#1a1a2e]"
              @click.stop="toggleMenu(f.fragment_id)"
            >&#8943;</button>

            <!-- Dropdown menu -->
            <div
              v-if="openMenuId === f.fragment_id"
              class="absolute top-9 right-2.5 bg-white border border-[#e8e5df] rounded-lg shadow-lg z-20 min-w-[180px] overflow-hidden"
              @click.stop
            >
              <button class="block w-full px-3.5 py-2.5 text-sm border-none bg-transparent cursor-pointer text-left text-[#3d3d5c] hover:bg-[#f0ede8]" @click="showMove(f.fragment_id)">Переместить в другой раздел</button>
              <div class="h-px bg-[#f0ede8]" />
              <button class="block w-full px-3.5 py-2.5 text-sm border-none bg-transparent cursor-pointer text-left text-[#c62828] hover:bg-[#fff0f0]" @click="showConfirm(f.fragment_id)">Исключить из подборки</button>
            </div>

            <div class="text-sm leading-[1.7] text-[#3d3d5c] mb-2 pr-8">{{ f.text }}</div>
            <div class="flex items-center gap-2 flex-wrap">
              <span
                v-if="f.citation_intent"
                class="text-xs font-medium px-2.5 py-0.5 rounded"
                :class="intentColor[f.citation_intent] || 'bg-gray-100 text-gray-600'"
              >{{ intentLabel[f.citation_intent] || f.citation_intent }}</span>
              <router-link :to="`/${projectId}/library/${f.citekey}/review`" class="text-[13px] text-[#0d7377] no-underline hover:underline">{{ sourceDisplay(f) }}</router-link>
            </div>
            <div v-if="f.note" class="text-[13px] text-[#6b6b8a] italic mt-1.5 leading-6">{{ f.note }}</div>

            <!-- Confirm bar -->
            <div v-if="confirmId === f.fragment_id" class="flex items-center gap-2 pt-2.5 mt-2 border-t border-[#f0ede8] bg-[#fff0f0] -mx-4 -mb-3.5 px-4 py-2.5 rounded-b-[10px]">
              <span class="text-[13px] text-[#c62828] flex-1">Исключить цитату из подборки?</span>
              <button class="text-[13px] px-3.5 py-1 rounded bg-white text-[#6b6b8a] border border-[#e8e5df] cursor-pointer hover:bg-[#f0ede8]" @click="confirmId = null">Отмена</button>
              <button class="text-[13px] px-3.5 py-1 rounded bg-[#c62828] text-white border-none cursor-pointer font-medium hover:bg-[#a31f1f]" @click="excludeFragment(f.fragment_id)">Исключить</button>
            </div>

            <!-- Move section picker -->
            <div v-if="moveFragmentId === f.fragment_id" class="pt-2.5 mt-2 border-t border-[#f0ede8] -mx-4 -mb-3.5 px-4 py-2.5 rounded-b-[10px] bg-[#f9f8f6]">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[13px] font-medium text-[#3d3d5c]">Переместить в раздел:</span>
                <button class="text-[13px] text-[#6b6b8a] cursor-pointer bg-transparent border-none hover:text-[#1a1a2e]" @click="moveFragmentId = null">Отмена</button>
              </div>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="sec in outline.filter(sec => sec.id !== selectedSectionId)"
                  :key="sec.id"
                  class="text-[13px] px-2.5 py-1 rounded-md border border-[#e8e5df] bg-white text-[#3d3d5c] cursor-pointer hover:border-[#0d7377] hover:text-[#0d7377] transition-colors"
                  @click="moveToSection(f.fragment_id, sec.id)"
                >{{ sec.id }}. {{ sec.name }}</button>
              </div>
            </div>
          </div>

          <!-- Expand/collapse -->
          <span
            v-if="selectedFragments.length > COLLAPSE_THRESHOLD"
            class="text-sm text-[#0d7377] cursor-pointer inline-block py-1.5 hover:underline"
            @click="expandedSection = !expandedSection"
          >{{ expandedSection
              ? 'свернуть'
              : `+ ещё ${hiddenCount} ${pluralCitation(hiddenCount)}` }}</span>
        </template>
      </div>

      <!-- Tab: Suggest -->
      <div v-if="activeTab === 'suggest'" class="py-4">
        <!-- Gap alert -->
        <div v-if="gapAlert" class="mx-6 mb-4 bg-[#fef3c7] border border-[#fcd34d] rounded-lg px-4 py-3">
          <div class="text-[13px] font-semibold text-[#b45309]">{{ gapAlert.message }}</div>
        </div>

        <!-- Loading -->
        <div v-if="suggestLoading" class="text-center py-12 text-[#6b6b8a] text-sm">Загрузка рекомендаций...</div>

        <!-- No suggestions -->
        <div v-else-if="suggestions.length === 0" class="text-center py-12 text-[#6b6b8a] text-sm">Нет рекомендаций для этого раздела</div>

        <!-- Suggestion cards -->
        <template v-else>
          <div class="px-6">
            <div
              v-for="s in suggestions"
              :key="s.fragment_id"
              class="bg-white border border-[#e8e5df] rounded-[10px] px-4 py-3.5 mb-2.5 transition-colors hover:border-[#d4d0ca]"
            >
              <div class="text-sm leading-[1.7] text-[#3d3d5c] mb-2">{{ s.text }}</div>
              <div class="flex items-center gap-2 flex-wrap">
                <span
                  v-if="s.citation_intent"
                  class="text-xs font-medium px-2.5 py-0.5 rounded"
                  :class="intentColor[s.citation_intent] || 'bg-gray-100 text-gray-600'"
                >{{ intentLabel[s.citation_intent] || s.citation_intent }}</span>
                <span class="text-[13px] text-[#0d7377]">{{ s.source }}</span>
                <span
                  class="text-xs font-medium"
                  :class="s.match_reason === 'intent_match' ? 'text-[#2d6a4f]' : 'text-[#0d7377]'"
                >{{ s.match_reason === 'intent_match' ? 'intent match' : 'похожий контекст' }}</span>
                <button
                  class="ml-auto px-4 py-1.5 rounded-md border text-[13px] font-medium cursor-pointer whitespace-nowrap shrink-0"
                  :class="s._added ? 'bg-[#e8f5e9] text-[#2d6a4f] border-[#a7f3d0]' : 'bg-white text-[#0d7377] border-[#0d7377] hover:bg-[#e6f3f3]'"
                  :disabled="s._added"
                  @click="addSuggested(s)"
                >{{ s._added ? 'Добавлено' : 'Добавить' }}</button>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </template>
</template>

<style scoped>
.frag-card:hover .frag-menu-btn {
  opacity: 1 !important;
}
.frag-menu-btn {
  opacity: 0;
  transition: opacity 0.15s;
}
</style>
