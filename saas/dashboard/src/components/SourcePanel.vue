<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { projects as apiProjects, library as apiLibrary } from '@/api/client'

interface LibraryItem {
  source: string
  sourceTitle: string
  year: number | null
  text: string
}

const props = defineProps<{
  sectionId: string | null
  projectId: string
  isDemoMode?: boolean
  sectionName?: string
}>()

const emit = defineEmits<{
  attach: [citekey: string]
  detach: [citekey: string]
}>()

const attachedCitekeys = ref<string[]>([])
const libraryItems = ref<LibraryItem[]>([])
const searchQuery = ref('')
const searchResults = ref<LibraryItem[]>([])
const searching = ref(false)
const loading = ref(false)

let loadTimer: ReturnType<typeof setTimeout> | null = null
let searchTimeout: ReturnType<typeof setTimeout> | null = null

const attachedItems = computed<LibraryItem[]>(() =>
  attachedCitekeys.value.map(citekey => {
    const found = libraryItems.value.find(l => l.source === citekey)
    return found ?? { source: citekey, sourceTitle: citekey, year: null, text: '' }
  })
)

async function ensureLibraryLoaded() {
  if (props.isDemoMode || libraryItems.value.length > 0) return
  try {
    const data = await apiLibrary.list(props.projectId)
    libraryItems.value = data.sources.map((s: any) => ({
      source: s.citekey,
      sourceTitle: s.title || s.citekey,
      year: s.year ?? null,
      text: s.abstract || '',
    }))
  } catch { /* silent */ }
}

async function loadAttached(sectionId: string) {
  loading.value = true
  try {
    const data = await apiProjects.sectionSources(sectionId)
    attachedCitekeys.value = data.citekeys
  } catch {
    attachedCitekeys.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => props.sectionId,
  (id) => {
    if (loadTimer) clearTimeout(loadTimer)
    searchQuery.value = ''
    searchResults.value = []
    if (!id || !props.projectId || props.isDemoMode) {
      attachedCitekeys.value = []
      return
    }
    loadTimer = setTimeout(() => loadAttached(id), 300)
  },
  { immediate: true }
)

function _searchIn(q: string) {
  return libraryItems.value.filter(f =>
    !attachedCitekeys.value.includes(f.source) &&
    (f.sourceTitle.toLowerCase().includes(q) ||
      f.source.toLowerCase().includes(q) ||
      f.text.toLowerCase().includes(q))
  )
}

function onSearchInput() {
  if (searchTimeout) clearTimeout(searchTimeout)
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) { searchResults.value = []; return }
  searching.value = true

  if (libraryItems.value.length === 0) {
    ensureLibraryLoaded().then(() => {
      searchResults.value = _searchIn(q)
      searching.value = false
    })
    return
  }

  searchTimeout = setTimeout(() => {
    searchResults.value = _searchIn(q)
    searching.value = false
  }, 300)
}

async function attachItem(item: LibraryItem) {
  if (attachedCitekeys.value.includes(item.source)) return
  attachedCitekeys.value.push(item.source)
  searchResults.value = searchResults.value.filter(r => r.source !== item.source)
  emit('attach', item.source)
  if (!props.isDemoMode && props.sectionId) {
    try { await apiProjects.assignSections(item.source, [props.sectionId]) } catch { /* non-fatal */ }
  }
}

async function detachItem(citekey: string) {
  attachedCitekeys.value = attachedCitekeys.value.filter(k => k !== citekey)
  emit('detach', citekey)
  if (!props.isDemoMode && props.sectionId) {
    try { await apiProjects.detachSection(citekey, props.sectionId) } catch { /* non-fatal */ }
  }
}
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- Section header -->
    <div class="flex-shrink-0 px-3 pt-3 pb-2 border-b border-[var(--color-rule-light)]">
      <p v-if="sectionId" class="text-sm font-semibold text-[var(--color-accent-deep)] truncate">
        {{ sectionName ?? sectionId }}
      </p>
      <p v-else class="text-sm text-[var(--color-ink-muted)] italic">
        Поставьте курсор в раздел
      </p>
    </div>

    <!-- Search box -->
    <div class="flex-shrink-0 px-3 pt-2 pb-1.5">
      <div class="relative">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"
          class="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-ink-muted)]">
          <path fill-rule="evenodd" d="M9.965 11.026a5 5 0 1 1 1.06-1.06l2.755 2.754a.75.75 0 1 1-1.06 1.06l-2.755-2.754ZM10.5 7a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0Z" clip-rule="evenodd" />
        </svg>
        <input
          v-model="searchQuery"
          @input="onSearchInput"
          type="text"
          class="w-full rounded-md border border-[var(--color-rule)] bg-[var(--color-paper)] pl-8 pr-3 py-1.5 text-sm placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none"
          placeholder="Найти в библиотеке..."
        />
      </div>
    </div>

    <!-- Search results -->
    <div
      v-if="searchQuery.trim() && (searchResults.length > 0 || searching)"
      class="flex-shrink-0 mx-3 mb-2 rounded-md border border-[var(--color-accent)]/30 bg-[var(--color-accent-pale)]/20 overflow-hidden"
    >
      <div class="px-3 py-1 text-[13px] font-semibold text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]/40">
        Результаты
      </div>
      <div v-if="searching" class="px-3 py-3 text-center text-[13px] text-[var(--color-ink-muted)]">Ищу...</div>
      <div v-else-if="searchResults.length === 0" class="px-3 py-2 text-center text-[13px] text-[var(--color-ink-muted)]">
        Не найдено
      </div>
      <div v-else class="divide-y divide-[var(--color-rule-light)]">
        <div v-for="r in searchResults" :key="r.source" class="px-3 py-2 hover:bg-[var(--color-accent-pale)]/40">
          <div class="flex items-center gap-1 mb-0.5">
            <span class="font-[var(--font-mono)] text-[13px] text-[var(--color-accent-deep)] truncate">
              @{{ r.source.length > 14 ? r.source.slice(0, 14) + '..' : r.source }}
            </span>
            <span class="text-[13px] text-[var(--color-ink-muted)]">{{ r.year }}</span>
            <button
              @click="attachItem(r)"
              :disabled="!sectionId"
              class="ml-auto rounded bg-[var(--color-accent)] px-1.5 py-0.5 text-[13px] font-medium text-white hover:bg-[var(--color-accent-deep)] disabled:opacity-40 disabled:cursor-not-allowed"
            >+</button>
          </div>
          <p class="text-[13px] text-[var(--color-ink-light)] leading-relaxed line-clamp-2">{{ r.text }}</p>
        </div>
      </div>
    </div>

    <!-- Attached list -->
    <div class="flex-1 overflow-y-auto px-3 pb-3 space-y-2">
      <div class="text-[13px] font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider pt-1">
        <span v-if="loading" class="opacity-50">Загрузка…</span>
        <span v-else>Привязано ({{ attachedCitekeys.length }})</span>
      </div>
      <div v-if="!sectionId" class="text-[13px] text-[var(--color-ink-muted)] italic py-2 text-center">—</div>
      <div
        v-else-if="attachedItems.length === 0 && !loading"
        class="text-[13px] text-[var(--color-ink-muted)] italic py-2 text-center"
      >
        Нет источников
      </div>
      <div
        v-for="item in attachedItems"
        :key="item.source"
        class="group rounded-md border border-[var(--color-rule-light)] bg-[var(--color-paper-white)] px-3 py-2 relative"
      >
        <div class="flex items-center gap-1">
          <span class="font-[var(--font-mono)] text-[13px] text-[var(--color-accent-deep)] truncate flex-1">
            @{{ item.source.length > 16 ? item.source.slice(0, 16) + '..' : item.source }}
          </span>
          <span class="text-[13px] text-[var(--color-ink-muted)] flex-shrink-0">{{ item.year }}</span>
        </div>
        <p
          v-if="item.sourceTitle !== item.source"
          class="text-[13px] text-[var(--color-ink-light)] leading-snug truncate mt-0.5"
        >
          {{ item.sourceTitle }}
        </p>
        <button
          @click="detachItem(item.source)"
          class="absolute top-1.5 right-1.5 h-4 w-4 rounded flex items-center justify-center text-[var(--color-ink-muted)] hover:text-[var(--color-err)] hover:bg-[var(--color-err-bg)] opacity-0 group-hover:opacity-100 transition-all"
          title="Отвязать"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-2.5 w-2.5">
            <path d="M5.28 4.22a.75.75 0 0 0-1.06 1.06L6.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L8 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L9.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L8 6.94 5.28 4.22Z" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>
