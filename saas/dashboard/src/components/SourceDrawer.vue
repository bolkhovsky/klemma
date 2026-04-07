<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { library, projects as apiProjects } from '@/api/client'

const props = defineProps<{
  citekey: string
  projectId: string
  activeSectionId?: string | null
}>()
const emit = defineEmits<{ close: [] }>()

interface Fragment { fragment_id: string; text: string; fragment_type: string; section?: string }
interface SourceDetail {
  citekey: string; title: string; authors?: string; year?: number
  abstract?: string; journal?: string; fragments?: Fragment[]
}

const loading = ref(true)
const error = ref('')
const source = ref<SourceDetail | null>(null)
const assignedSections = ref<string[]>([])
const attaching = ref(false)

onMounted(async () => {
  try {
    const [src, sect] = await Promise.all([
      library.get(props.citekey),
      apiProjects.sourceSections(props.citekey).catch(() => ({ sections: [] as string[] })),
    ])
    source.value = src
    assignedSections.value = sect.sections
  } catch (e: any) {
    error.value = e.message ?? 'Ошибка загрузки'
  } finally {
    loading.value = false
  }
})

async function toggleSection(sectionId: string) {
  if (!source.value) return
  const isAssigned = assignedSections.value.includes(sectionId)
  attaching.value = true
  try {
    const newSections = isAssigned
      ? assignedSections.value.filter(s => s !== sectionId)
      : [...assignedSections.value, sectionId]
    await apiProjects.assignSections(props.citekey, newSections)
    assignedSections.value = newSections
  } catch { /* ignore */ } finally {
    attaching.value = false
  }
}

const isCurrentSectionAssigned = () =>
  props.activeSectionId ? assignedSections.value.includes(props.activeSectionId) : false
</script>

<template>
  <!-- Overlay backdrop -->
  <Teleport to="body">
    <div class="fixed inset-0 z-40 bg-black/10" @click="emit('close')" />
    <div class="fixed right-0 top-0 h-full w-80 bg-white border-l border-[var(--color-rule)] shadow-2xl z-50 flex flex-col">

      <!-- Header -->
      <div class="flex items-center gap-2.5 px-4 py-3 border-b border-[var(--color-rule)] flex-shrink-0">
        <span class="text-[13px] font-mono font-semibold text-[var(--color-accent)] bg-[var(--color-accent-pale)] rounded px-1.5 py-0.5 flex-1 truncate">
          @{{ citekey }}
        </span>
        <button
          @click="emit('close')"
          class="w-7 h-7 flex items-center justify-center rounded text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)] transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex items-center justify-center flex-1 text-[var(--color-ink-muted)]">
        <svg class="animate-spin w-5 h-5" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-dasharray="31.4" stroke-dashoffset="10"/>
        </svg>
      </div>

      <!-- Error -->
      <div v-else-if="error" class="p-4 text-[var(--color-err)] text-sm">
        {{ error }}
      </div>

      <!-- Content -->
      <div v-else-if="source" class="flex-1 overflow-y-auto">

        <!-- Bibliographic info -->
        <div class="px-4 pt-4 pb-3 border-b border-[var(--color-rule-light)]">
          <h3 class="text-[15px] font-semibold text-[var(--color-ink)] leading-snug mb-1.5">{{ source.title }}</h3>
          <p v-if="source.authors" class="text-sm text-[var(--color-ink-muted)] mb-0.5">{{ source.authors }}</p>
          <p v-if="source.year || source.journal" class="text-[13px] font-mono text-[var(--color-ink-muted)]">
            <span v-if="source.year">{{ source.year }}</span>
            <span v-if="source.journal && source.year"> · </span>
            <span v-if="source.journal">{{ source.journal }}</span>
          </p>
          <p v-if="source.abstract" class="text-sm text-[var(--color-ink-muted)] mt-2 leading-relaxed line-clamp-4">
            {{ source.abstract }}
          </p>
        </div>

        <!-- Assign to active section -->
        <div v-if="activeSectionId" class="px-4 py-3 border-b border-[var(--color-rule-light)]">
          <button
            @click="toggleSection(activeSectionId)"
            :disabled="attaching"
            :class="[
              'w-full inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
              isCurrentSectionAssigned()
                ? 'border border-[var(--color-ok)] text-[var(--color-ok)] bg-[var(--color-ok-bg)] hover:bg-green-100'
                : 'bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-deep)]',
              attaching ? 'opacity-60 cursor-wait' : ''
            ]"
          >
            <svg v-if="isCurrentSectionAssigned()" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            {{ isCurrentSectionAssigned() ? `Прикреплён к ${activeSectionId}` : `Прикрепить к ${activeSectionId}` }}
          </button>
        </div>

        <!-- Fragments -->
        <div class="px-4 pt-3">
          <div class="text-[12px] font-semibold uppercase tracking-[0.5px] text-[var(--color-ink-muted)] mb-2">
            Фрагменты {{ source.fragments?.length ? `(${source.fragments.length})` : '' }}
          </div>
          <div v-if="!source.fragments?.length" class="text-[13px] text-[var(--color-ink-muted)] italic py-2">
            Фрагменты не загружены
          </div>
          <div
            v-for="frag in source.fragments"
            :key="frag.fragment_id"
            class="mb-2 p-2.5 rounded-md bg-[var(--color-rule-light)] border border-[var(--color-rule)]"
          >
            <div class="flex items-center gap-1.5 mb-1.5">
              <span class="text-[12px] font-mono text-[var(--color-ink-muted)] bg-white rounded px-1 py-0.5 border border-[var(--color-rule)]">
                {{ frag.fragment_type }}
              </span>
              <span v-if="frag.section" class="text-[12px] font-mono text-[var(--color-accent)]">
                {{ frag.section }}
              </span>
            </div>
            <p class="text-sm text-[var(--color-ink)] leading-relaxed">{{ frag.text }}</p>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="border-t border-[var(--color-rule)] px-4 py-2.5 flex-shrink-0">
        <RouterLink
          :to="`/${projectId}/library/${citekey}`"
          class="flex items-center justify-center gap-1.5 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] transition-colors"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          Открыть полную карточку
        </RouterLink>
      </div>
    </div>
  </Teleport>
</template>
