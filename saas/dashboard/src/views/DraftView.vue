<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { drafts, type DraftHeading } from '@/api/client'
import { renderDraft } from '@/utils/markdown'
import AppLayout from '@/components/AppLayout.vue'

const route = useRoute()
const projectId = computed(() => route.params.projectId as string)

// File state
const filename = ref('')
const content = ref('')
const headings = ref<DraftHeading[]>([])
const wordCount = ref(0)
const loading = ref(true)
const loadError = ref('')

// Active section editing
const activeSectionId = ref<string | null>(null)
const editingBody = ref('')
const dirty = ref(false)
const saving = ref(false)
const savedAt = ref<Date | null>(null)
const autoSaveTimer = ref<ReturnType<typeof setTimeout> | null>(null)

// kAI (ghost text) toggle — persisted
const ghostEnabled = ref(localStorage.getItem('klemma_ghost_enabled') !== 'false')
watch(ghostEnabled, (v) => localStorage.setItem('klemma_ghost_enabled', String(v)))

// ---------------------------------------------------------------
// Section split: divide content into heading + body chunks
// ---------------------------------------------------------------
interface Section {
  section_id: string
  full_title: string
  level: number
  body: string // text between this heading and the next
}

const titleBlock = computed<string>(() => {
  if (!content.value) return ''
  const lines = content.value.split('\n')
  const firstHeadingLine = headings.value[0]?.line ?? lines.length
  return lines.slice(0, firstHeadingLine).join('\n').trim()
})

const sections = computed<Section[]>(() => {
  if (!content.value || !headings.value.length) return []
  const lines = content.value.split('\n')
  return headings.value.map((h, i) => {
    const nextLine = headings.value[i + 1]?.line ?? lines.length
    const body = lines.slice(h.line + 1, nextLine).join('\n').trim()
    return {
      section_id: h.section_id,
      full_title: h.full_title,
      level: h.level,
      body,
    }
  })
})

// ---------------------------------------------------------------
// Load
// ---------------------------------------------------------------
onMounted(async () => {
  try {
    const data = await drafts.init(projectId.value)
    filename.value = data.name
    content.value = data.content
    headings.value = data.headings
    wordCount.value = data.word_count
  } catch (e: any) {
    loadError.value = e.message ?? 'Ошибка загрузки'
  } finally {
    loading.value = false
  }
})

// Save pending changes before leaving
onBeforeUnmount(() => {
  if (autoSaveTimer.value) clearTimeout(autoSaveTimer.value)
  if (dirty.value && activeSectionId.value) saveSection()
})

// ---------------------------------------------------------------
// Editing
// ---------------------------------------------------------------
const textareaRef = ref<HTMLTextAreaElement | null>(null)

async function activateSection(sectionId: string) {
  if (activeSectionId.value === sectionId) return
  // Flush current edits before switching
  if (dirty.value && activeSectionId.value) await saveSection()
  const sec = sections.value.find(s => s.section_id === sectionId)
  if (!sec) return
  activeSectionId.value = sectionId
  editingBody.value = sec.body
  dirty.value = false
  await nextTick()
  textareaRef.value?.focus()
  autoResizeTextarea()
}

function deactivate() {
  if (dirty.value) saveSection()
  activeSectionId.value = null
}

function onInput() {
  dirty.value = true
  autoResizeTextarea()
  scheduleAutoSave()
}

function scheduleAutoSave() {
  if (autoSaveTimer.value) clearTimeout(autoSaveTimer.value)
  autoSaveTimer.value = setTimeout(saveSection, 3000)
}

async function saveSection() {
  if (!activeSectionId.value || !filename.value) return
  if (autoSaveTimer.value) clearTimeout(autoSaveTimer.value)
  saving.value = true
  try {
    await drafts.upsertSection(
      projectId.value,
      filename.value,
      activeSectionId.value,
      editingBody.value,
    )
    dirty.value = false
    savedAt.value = new Date()
    // Refresh content silently
    const data = await drafts.get(projectId.value, filename.value)
    content.value = data.content
    headings.value = data.headings
    wordCount.value = data.word_count
  } catch {
    /* non-fatal */
  } finally {
    saving.value = false
  }
}

function autoResizeTextarea() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = el.scrollHeight + 'px'
}

function scrollToSection(sectionId: string) {
  const el = document.getElementById(`draft-sec-${sectionId}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// Heading level → indent for TOC
function tocIndent(level: number) {
  if (level <= 2) return ''
  return `pl-${(level - 2) * 3}`
}

// Format saved-at time
const savedLabel = computed(() => {
  if (saving.value) return 'Сохранение…'
  if (!savedAt.value) return ''
  const diff = Math.floor((Date.now() - savedAt.value.getTime()) / 1000)
  if (diff < 10) return 'Сохранено'
  if (diff < 60) return `${diff}с назад`
  return `${Math.floor(diff / 60)}м назад`
})

const activeWordCount = computed(() =>
  editingBody.value.trim() ? editingBody.value.trim().split(/\s+/).length : 0
)
</script>

<template>
  <AppLayout>
    <!-- Loading / error -->
    <div v-if="loading" class="flex items-center justify-center h-64 text-[var(--color-ink-muted)] text-sm">
      Загрузка черновика…
    </div>
    <div v-else-if="loadError" class="rounded-lg border border-[var(--color-err)] bg-[var(--color-err-bg)] px-4 py-3 text-sm text-[var(--color-err)]">
      {{ loadError }}
    </div>

    <template v-else>
      <!-- Header bar -->
      <div class="mb-6 flex items-center justify-between gap-4">
        <div class="min-w-0">
          <h1 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)] leading-tight truncate">
            Черновик
          </h1>
          <p class="mt-0.5 text-xs text-[var(--color-ink-muted)]">
            {{ filename }} · {{ wordCount.toLocaleString('ru-RU') }} слов
          </p>
        </div>

        <div class="flex items-center gap-3 flex-shrink-0">
          <!-- Save status -->
          <span
            v-if="savedLabel || dirty"
            class="text-xs transition-colors"
            :class="dirty ? 'text-[var(--color-warn)]' : 'text-[var(--color-ink-muted)]'"
          >
            {{ dirty ? '●' : '' }} {{ savedLabel }}
          </span>

          <!-- kAI toggle -->
          <button
            @click="ghostEnabled = !ghostEnabled"
            class="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold tracking-tight transition-all duration-150"
            :class="ghostEnabled
              ? 'border-[var(--color-accent)] bg-[var(--color-accent-pale)] text-[var(--color-accent)]'
              : 'border-[var(--color-rule)] bg-transparent text-[var(--color-ink-muted)]'"
            :title="ghostEnabled ? 'Отключить kAI автодополнение' : 'Включить kAI автодополнение'"
          >
            <span
              class="h-[7px] w-[7px] rounded-full border transition-all duration-150 flex-shrink-0"
              :class="ghostEnabled
                ? 'bg-[var(--color-accent)] border-[var(--color-accent)]'
                : 'bg-transparent border-[var(--color-ink-muted)]'"
            />
            kAI
          </button>
        </div>
      </div>

      <!-- Main: TOC + Document -->
      <div class="flex gap-8 items-start">
        <!-- TOC -->
        <nav class="w-44 flex-shrink-0 sticky top-8">
          <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">
            Содержание
          </p>
          <div class="flex flex-col gap-0.5">
            <button
              v-for="sec in sections"
              :key="sec.section_id"
              @click="scrollToSection(sec.section_id); activateSection(sec.section_id)"
              class="flex items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs transition-colors leading-tight w-full"
              :class="[
                tocIndent(sec.level),
                activeSectionId === sec.section_id
                  ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)] font-medium'
                  : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]',
              ]"
            >
              <span class="font-[var(--font-mono)] flex-shrink-0 text-[10px] opacity-60">{{ sec.section_id }}</span>
              <span class="truncate">{{ sec.full_title.replace(/^\d[\d.]*\s*/, '') }}</span>
            </button>
          </div>
        </nav>

        <!-- Document tape -->
        <div class="flex-1 min-w-0">
          <!-- Title block (# Диссертация, etc.) -->
          <div
            v-if="titleBlock"
            class="mb-8 draft-prose"
            v-html="renderDraft(titleBlock)"
          />

          <!-- Sections -->
          <div
            v-for="sec in sections"
            :key="sec.section_id"
            :id="`draft-sec-${sec.section_id}`"
            class="mb-1 scroll-mt-8"
          >
            <!-- Heading row -->
            <div
              class="group flex cursor-pointer items-baseline gap-2 py-1 rounded-md px-2 -mx-2 transition-colors hover:bg-[var(--color-rule-light)]"
              :class="activeSectionId === sec.section_id ? 'bg-[var(--color-accent-pale)] hover:bg-[var(--color-accent-pale)]' : ''"
              @click="activateSection(sec.section_id)"
            >
              <component
                :is="`h${Math.min(sec.level, 6)}`"
                class="font-[var(--font-display)] font-semibold text-[var(--color-ink)] leading-snug select-none"
                :class="{
                  'text-xl mt-6': sec.level === 2,
                  'text-lg mt-4': sec.level === 3,
                  'text-base mt-3': sec.level >= 4,
                }"
              >
                {{ sec.full_title }}
              </component>
              <!-- Edit hint -->
              <span
                class="text-[10px] text-[var(--color-ink-muted)] opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
                :class="activeSectionId === sec.section_id ? 'opacity-100' : ''"
              >
                {{ activeSectionId === sec.section_id ? 'редактирование' : 'нажмите' }}
              </span>
            </div>

            <!-- Body: edit mode -->
            <div
              v-if="activeSectionId === sec.section_id"
              class="mt-1 rounded-lg border border-[var(--color-accent)] bg-[var(--color-accent-pale)]/30 px-4 py-3"
            >
              <textarea
                ref="textareaRef"
                v-model="editingBody"
                @input="onInput"
                @blur="deactivate"
                @keydown.escape="deactivate"
                class="w-full resize-none bg-transparent font-[var(--font-body)] text-sm leading-relaxed text-[var(--color-ink)] placeholder:text-[var(--color-ink-muted)] focus:outline-none"
                placeholder="Напишите текст раздела…"
                rows="6"
                style="overflow: hidden;"
              />
              <div class="mt-2 flex items-center justify-between">
                <span class="text-[11px] text-[var(--color-ink-muted)]">
                  {{ activeWordCount }} слов
                  <span v-if="ghostEnabled" class="ml-2 opacity-60">· kAI вкл.</span>
                </span>
                <button
                  @mousedown.prevent="saveSection"
                  class="rounded px-3 py-1 text-xs font-semibold text-white bg-[var(--color-accent)] hover:bg-[var(--color-accent-deep)] transition-colors disabled:opacity-50"
                  :disabled="saving || !dirty"
                >
                  {{ saving ? 'Сохранение…' : 'Сохранить' }}
                </button>
              </div>
            </div>

            <!-- Body: read mode -->
            <div
              v-else
              class="mt-1 cursor-text rounded-md px-2 -mx-2 py-1 transition-colors hover:bg-[var(--color-rule-light)]"
              @click="activateSection(sec.section_id)"
            >
              <div
                v-if="sec.body"
                class="draft-prose"
                v-html="renderDraft(sec.body)"
              />
              <p
                v-else
                class="italic text-sm text-[var(--color-ink-muted)] py-2"
              >
                Раздел пуст — нажмите для редактирования
              </p>
            </div>
          </div>

          <!-- Empty state: no headings -->
          <div
            v-if="!sections.length"
            class="mt-12 text-center"
          >
            <p class="text-[var(--color-ink-muted)] text-sm">
              Структура не задана. Добавьте разделы в
              <RouterLink
                :to="`/${projectId}/outline`"
                class="text-[var(--color-accent)] hover:underline"
              >
                Структуре проекта
              </RouterLink>
              , затем вернитесь сюда.
            </p>
          </div>
        </div>
      </div>
    </template>
  </AppLayout>
</template>

<style scoped>
.draft-prose :deep(h1),
.draft-prose :deep(h2),
.draft-prose :deep(h3) {
  font-family: var(--font-display);
  font-weight: 600;
  color: var(--color-ink);
  margin-top: 1.25rem;
  margin-bottom: 0.5rem;
}
.draft-prose :deep(h1) { font-size: 1.25rem; }
.draft-prose :deep(h2) { font-size: 1.1rem; }
.draft-prose :deep(h3) { font-size: 1rem; }
.draft-prose :deep(p) {
  font-size: 0.9rem;
  line-height: 1.75;
  color: var(--color-ink-light);
  margin-bottom: 0.75rem;
}
.draft-prose :deep(ul),
.draft-prose :deep(ol) {
  padding-left: 1.5rem;
  margin-bottom: 0.75rem;
}
.draft-prose :deep(li) {
  font-size: 0.9rem;
  line-height: 1.7;
  color: var(--color-ink-light);
}
.draft-prose :deep(strong) { color: var(--color-ink); font-weight: 600; }
.draft-prose :deep(em) { color: var(--color-ink-muted); }
.draft-prose :deep(blockquote) {
  border-left: 3px solid var(--color-rule);
  padding-left: 1rem;
  color: var(--color-ink-muted);
  font-style: italic;
  margin: 0.75rem 0;
}
.draft-prose :deep(.citekey-ref) {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--color-accent);
  background: var(--color-accent-pale);
  border-radius: 3px;
  padding: 0 3px;
}
</style>
