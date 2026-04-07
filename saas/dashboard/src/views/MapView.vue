<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { useProjectStore } from '@/stores/project'
import { curation, library } from '@/api/client'

const route = useRoute()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.projectId as string || projectStore.activeProjectId || '')

// Curated fragments
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
}

const fragments = ref<CuratedFrag[]>([])
const bySection = ref<Record<string, number>>({})
const totalAccepted = ref(0)
const loading = ref(true)

// Source metadata cache for display
const sourceCache = ref<Record<string, { title: string; authors: string; year: number | null }>>({})

// UI state
const openMenuId = ref<string | null>(null)
const confirmId = ref<string | null>(null)
const suggestPopup = ref<{ open: boolean; sectionId: string; tab: 'suggest' | 'all' }>({ open: false, sectionId: '', tab: 'suggest' })
const suggestions = ref<any[]>([])
const gapAlert = ref<{ missing_intents: string[]; message: string } | null>(null)
const suggestLoading = ref(false)

const COLLAPSE_THRESHOLD = 3
const expandedSections = ref<Set<string>>(new Set())

function toggleExpand(sectionId: string) {
  const s = new Set(expandedSections.value)
  if (s.has(sectionId)) s.delete(sectionId)
  else s.add(sectionId)
  expandedSections.value = s
}

function visibleFragments(group: { id: string; fragments: CuratedFrag[] }): CuratedFrag[] {
  if (group.fragments.length <= COLLAPSE_THRESHOLD) return group.fragments
  if (expandedSections.value.has(group.id)) return group.fragments
  return group.fragments.slice(0, COLLAPSE_THRESHOLD)
}

function hiddenCount(group: { id: string; fragments: CuratedFrag[] }): number {
  if (group.fragments.length <= COLLAPSE_THRESHOLD) return 0
  if (expandedSections.value.has(group.id)) return 0
  return group.fragments.length - COLLAPSE_THRESHOLD
}

function pluralCitation(n: number): string {
  const mod10 = n % 10, mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 'цитата'
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'цитаты'
  return 'цитат'
}

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

const outline = computed(() => projectStore.activeOutline || [])

// Group fragments by section
const sectionGroups = computed(() => {
  const groups: { id: string; name: string; fragments: CuratedFrag[] }[] = []
  const sectionMap = new Map<string, CuratedFrag[]>()

  for (const f of fragments.value) {
    const sec = f.assigned_section || ''
    if (!sectionMap.has(sec)) sectionMap.set(sec, [])
    sectionMap.get(sec)!.push(f)
  }

  // Add ALL outline sections in order (including empty)
  for (const s of outline.value) {
    const frags = sectionMap.get(s.id) || []
    groups.push({ id: s.id, name: s.name, fragments: frags })
    sectionMap.delete(s.id)
  }

  // "Не распределены" at the end
  const unassigned = sectionMap.get('') || []
  if (unassigned.length > 0) {
    groups.push({ id: '', name: 'Не распределены', fragments: unassigned })
  }

  return groups
})

function sourceDisplay(f: CuratedFrag): string {
  const cached = sourceCache.value[f.citekey]
  if (!cached) return f.citekey
  const a = cached.authors
  if (!a) return cached.title || f.citekey
  const short = a.includes(',') ? a.split(',')[0]!.trim() + ' et al.' : a
  return cached.year ? `${short}, ${cached.year}` : short
}

async function loadData() {
  if (!projectId.value) return
  loading.value = true
  try {
    const data = await curation.curated(projectId.value, { verdict: 'accepted' })
    fragments.value = data.fragments
    bySection.value = data.by_section
    totalAccepted.value = data.total

    // Fetch source metadata for unique citekeys
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

function toggleMenu(fragId: string) {
  openMenuId.value = openMenuId.value === fragId ? null : fragId
}

function closeMenus() {
  openMenuId.value = null
}

const moveFragmentId = ref<string | null>(null)

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

async function openSuggestPopup(sectionId: string) {
  suggestPopup.value = { open: true, sectionId, tab: 'suggest' }
  suggestLoading.value = true
  try {
    const data = await curation.suggest(projectId.value, sectionId)
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

function closeSuggestPopup() {
  suggestPopup.value = { open: false, sectionId: '', tab: 'suggest' }
  suggestions.value = []
  gapAlert.value = null
}

async function addSuggested(s: any) {
  await curation.curate(projectId.value, [{
    fragment_id: s.fragment_id,
    citekey: s.citekey,
    verdict: 'accepted',
    assigned_section: suggestPopup.value.sectionId,
  }])
  // Mark as added visually
  s._added = true
  // Reload main data
  await loadData()
}

onMounted(async () => {
  if (!projectStore.activeProject) await projectStore.loadProjects()
  await loadData()
})

watch(projectId, loadData)
</script>

<template>
  <AppLayout>
    <div class="py-5 px-6" @click="closeMenus">
      <!-- Header -->
      <div class="mb-5">
        <h1 class="text-lg font-semibold text-[#1a1a2e] mb-1">Карта цитат</h1>
        <div class="text-sm text-[#6b6b8a]">
          {{ totalAccepted }} принятых цитат &middot; {{ sectionGroups.length }} разделов
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="text-center py-12 text-[#6b6b8a]">Загрузка...</div>

      <!-- Empty state -->
      <div v-else-if="fragments.length === 0" class="text-center py-12">
        <div class="text-4xl mb-3">&#128218;</div>
        <h3 class="text-lg font-semibold text-[#1a1a2e] mb-2">Нет отобранных цитат</h3>
        <p class="text-[15px] text-[#6b6b8a] leading-6">
          Загрузите источник в <router-link :to="`/${projectId}/library`" class="text-[#0d7377] no-underline hover:underline">Библиотеку</router-link>
          и отберите цитаты
        </p>
      </div>

      <!-- Section groups -->
      <div v-else>
        <div v-for="group in sectionGroups" :key="group.id" class="mb-5">
          <!-- Section header -->
          <div class="flex items-center gap-2 py-2.5">
            <span
              class="font-mono text-sm font-semibold px-2.5 py-0.5 rounded"
              :class="group.id ? 'bg-[#e6f3f3] text-[#0d7377]' : 'bg-[#f0ede8] text-[#6b6b8a]'"
            >{{ group.id || '?' }}</span>
            <span class="text-[16px] font-medium flex-1" :class="group.id ? 'text-[#1a1a2e]' : 'text-[#6b6b8a] italic'">{{ group.name }}</span>
            <span v-if="group.fragments.length > 0" class="text-sm font-semibold text-[#6b6b8a] bg-[#f0ede8] px-2.5 py-0.5 rounded-full">{{ group.fragments.length }}</span>
          </div>

          <!-- Fragment cards -->
          <div class="pl-1">
            <div
              v-for="f in visibleFragments(group)"
              :key="f.fragment_id"
              class="relative bg-white border border-[#e8e5df] rounded-lg px-3.5 py-3 mb-2 transition-colors hover:border-[#d4d0ca]"
            >
              <!-- Menu trigger -->
              <button
                class="absolute top-2.5 right-2.5 w-7 h-7 rounded-md border-none bg-transparent cursor-pointer text-[16px] text-[#6b6b8a] flex items-center justify-center opacity-0 hover:bg-[#f0ede8] hover:text-[#1a1a2e] transition-opacity"
                :class="{ '!opacity-100': openMenuId === f.fragment_id }"
                @click.stop="toggleMenu(f.fragment_id)"
              >&#8943;</button>

              <!-- Dropdown menu -->
              <div
                v-if="openMenuId === f.fragment_id"
                class="absolute top-9 right-2.5 bg-white border border-[#e8e5df] rounded-lg shadow-lg z-20 min-w-[180px] overflow-hidden"
                @click.stop
              >
                <button class="block w-full px-3.5 py-2.5 text-sm border-none bg-transparent cursor-pointer text-left text-[#3d3d5c] hover:bg-[#f0ede8]" @click="showMove(f.fragment_id)">Переместить в другой раздел</button>
                <div class="h-px bg-[#f0ede8] mx-0" />
                <button class="block w-full px-3.5 py-2.5 text-sm border-none bg-transparent cursor-pointer text-left text-[#c62828] hover:bg-[#fff0f0]" @click="showConfirm(f.fragment_id)">Исключить из подборки</button>
              </div>

              <div class="text-sm leading-7 text-[#3d3d5c] mb-2 pr-8">{{ f.text }}</div>
              <div class="flex items-center gap-2 flex-wrap">
                <span
                  v-if="f.citation_intent"
                  class="text-sm font-medium px-2.5 py-0.5 rounded"
                  :class="intentColor[f.citation_intent] || 'bg-gray-100 text-gray-600'"
                >{{ intentLabel[f.citation_intent] || f.citation_intent }}</span>
                <router-link :to="`/${projectId}/library/${f.citekey}/review`" class="text-sm text-[#0d7377] no-underline hover:underline">{{ sourceDisplay(f) }}</router-link>
              </div>
              <div v-if="f.note" class="text-sm text-[#6b6b8a] italic mt-1.5 leading-6">{{ f.note }}</div>

              <!-- Confirm bar -->
              <div v-if="confirmId === f.fragment_id" class="flex items-center gap-2 pt-2.5 mt-2 border-t border-[#f0ede8] bg-[#fff0f0] -mx-3.5 -mb-3 px-3.5 py-2.5 rounded-b-lg">
                <span class="text-sm text-[#c62828] flex-1">Исключить цитату из подборки?</span>
                <button class="text-sm px-3.5 py-1 rounded bg-white text-[#6b6b8a] border border-[#e8e5df] cursor-pointer hover:bg-[#f0ede8]" @click="confirmId = null">Отмена</button>
                <button class="text-sm px-3.5 py-1 rounded bg-[#c62828] text-white border-none cursor-pointer font-medium hover:bg-[#a31f1f]" @click="excludeFragment(f.fragment_id)">Исключить</button>
              </div>

              <!-- Move section picker -->
              <div v-if="moveFragmentId === f.fragment_id" class="pt-2.5 mt-2 border-t border-[#f0ede8] -mx-3.5 -mb-3 px-3.5 py-2.5 rounded-b-lg bg-[#f9f8f6]">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[13px] font-medium text-[#3d3d5c]">Переместить в раздел:</span>
                  <button class="text-[13px] text-[#6b6b8a] cursor-pointer bg-transparent border-none hover:text-[#1a1a2e]" @click="moveFragmentId = null">Отмена</button>
                </div>
                <div class="flex flex-wrap gap-1.5">
                  <button
                    v-for="s in outline.filter(s => s.id !== group.id)"
                    :key="s.id"
                    class="text-[13px] px-2.5 py-1 rounded-md border border-[#e8e5df] bg-white text-[#3d3d5c] cursor-pointer hover:border-[#0d7377] hover:text-[#0d7377] transition-colors"
                    @click="moveToSection(f.fragment_id, s.id)"
                  >{{ s.id }}. {{ s.name }}</button>
                </div>
              </div>
            </div>

            <!-- Expand/collapse link -->
            <span
              v-if="group.fragments.length > COLLAPSE_THRESHOLD"
              class="text-sm text-[#0d7377] cursor-pointer inline-block py-1.5 hover:underline"
              @click="toggleExpand(group.id)"
            >{{ expandedSections.has(group.id)
                ? 'свернуть'
                : `+ ещё ${hiddenCount(group)} ${pluralCitation(hiddenCount(group))}` }}</span>

            <!-- Suggest button -->
            <button
              v-if="group.id"
              class="flex w-fit items-center gap-1.5 mt-3 px-4 py-2 bg-[#0d7377] border-none rounded-md text-white text-sm font-medium cursor-pointer hover:bg-[#065a5e] transition-colors"
              @click="openSuggestPopup(group.id)"
            >Подобрать цитаты</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Suggest popup overlay -->
    <div
      v-if="suggestPopup.open"
      class="fixed inset-0 bg-black/30 z-50 flex items-center justify-center"
      @click.self="closeSuggestPopup"
    >
      <div class="bg-white rounded-xl shadow-2xl w-[520px] max-h-[70vh] overflow-hidden flex flex-col">
        <!-- Popup header -->
        <div class="px-5 py-4 border-b border-[#e8e5df] flex items-center justify-between">
          <h3 class="text-[16px] font-semibold">Подобрать цитаты для раздела <span class="text-[#0d7377]">{{ suggestPopup.sectionId }}</span></h3>
          <button class="w-7 h-7 border-none bg-transparent cursor-pointer text-lg text-[#6b6b8a] rounded-md flex items-center justify-center hover:bg-[#f0ede8] hover:text-[#1a1a2e]" @click="closeSuggestPopup">&times;</button>
        </div>

        <!-- Popup tabs -->
        <div class="flex gap-0 border-b border-[#e8e5df]">
          <button
            class="px-4 py-2 text-sm font-medium cursor-pointer border-b-2 transition-colors"
            :class="suggestPopup.tab === 'suggest' ? 'text-[#065a5e] border-[#0d7377]' : 'text-[#6b6b8a] border-transparent hover:text-[#1a1a2e]'"
            @click="suggestPopup.tab = 'suggest'"
          >Рекомендации</button>
          <button
            class="px-4 py-2 text-sm font-medium cursor-pointer border-b-2 transition-colors"
            :class="suggestPopup.tab === 'all' ? 'text-[#065a5e] border-[#0d7377]' : 'text-[#6b6b8a] border-transparent hover:text-[#1a1a2e]'"
            @click="suggestPopup.tab = 'all'"
          >Все фрагменты</button>
        </div>

        <!-- Popup body -->
        <div class="px-5 py-3 overflow-y-auto flex-1">
          <div v-if="suggestLoading" class="text-center py-8 text-[#6b6b8a]">Загрузка рекомендаций...</div>
          <template v-else-if="suggestPopup.tab === 'suggest'">
            <!-- Gap alert -->
            <div v-if="gapAlert" class="bg-[#fef3c7] border border-[#fcd34d] rounded-lg px-3.5 py-2.5 mb-3 text-sm leading-6">
              <span class="font-semibold text-[#b45309]">{{ gapAlert.message }}</span>
            </div>

            <div v-if="suggestions.length === 0" class="text-center py-6 text-[#6b6b8a] text-sm">Нет рекомендаций</div>

            <div v-for="s in suggestions" :key="s.fragment_id" class="flex items-start gap-2.5 py-2.5 border-b border-[#f0ede8] last:border-b-0">
              <div class="flex-1">
                <div class="text-sm leading-6 text-[#3d3d5c]">{{ s.text }}</div>
                <div class="flex items-center gap-1.5 mt-1">
                  <span class="text-[13px] font-medium px-2 py-0.5 rounded" :class="intentColor[s.citation_intent] || 'bg-gray-100 text-gray-600'">{{ intentLabel[s.citation_intent] || s.citation_intent }}</span>
                  <span class="text-sm text-[#0d7377]">{{ s.source }}</span>
                  <span class="text-[13px] font-medium" :class="s.match_reason === 'intent_match' ? 'text-[#2d6a4f]' : 'text-[#0d7377]'">{{ s.match_reason === 'intent_match' ? 'intent match' : 'похожий контекст' }}</span>
                </div>
              </div>
              <button
                class="px-3.5 py-1.5 rounded-md border text-sm font-medium cursor-pointer whitespace-nowrap self-center shrink-0"
                :class="s._added ? 'bg-[#e8f5e9] text-[#2d6a4f] border-[#a7f3d0]' : 'bg-white text-[#0d7377] border-[#0d7377] hover:bg-[#e6f3f3]'"
                :disabled="s._added"
                @click="addSuggested(s)"
              >{{ s._added ? 'Добавлено' : 'Добавить' }}</button>
            </div>
          </template>

          <!-- All fragments tab (placeholder — search not wired yet) -->
          <template v-else>
            <div class="text-center py-8 text-[#6b6b8a] text-sm">Поиск по всем фрагментам будет доступен в следующей версии</div>
          </template>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
/* Make fragment cards show menu trigger on hover */
.relative:hover > button:first-child {
  opacity: 1 !important;
}
</style>
