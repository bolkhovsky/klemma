<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import FileBrowser from '@/components/FileBrowser.vue'
import SourcePanel from '@/components/SourcePanel.vue'
import { formatMarkdown, renderDraft } from '@/utils/markdown'
import { drafts, type DraftHeading } from '@/api/client'

const route = useRoute()
const router = useRouter()

const projectId = computed(() => route.params.projectId as string)

// ── File state ────────────────────────────────────────────────────────────
const draftFilename = ref('')
const draftContent = ref('')
const draftHeadings = ref<DraftHeading[]>([])
const editingBody = ref('')
const loading = ref(false)
const loadError = ref('')

// ── Save state ────────────────────────────────────────────────────────────
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'
const saveStatus = ref<SaveStatus>('idle')
let saveTimer: ReturnType<typeof setTimeout> | null = null

// ── Word count ────────────────────────────────────────────────────────────
const editWordCount = computed(() =>
  editingBody.value ? editingBody.value.split(/\s+/).filter(Boolean).length : 0
)

// ── Display name (same logic as FileBrowser) ──────────────────────────────
function displayName(name: string): string {
  const map: Record<string, string> = {
    'intro.md': 'Введение',
    'conclusion.md': 'Заключение',
  }
  if (map[name]) return map[name]
  const m = name.match(/chapter_(\d+)\.md/)
  if (m?.[1]) return `Глава ${m[1]}`
  return name.replace('.md', '')
}

// ── View / Edit mode ──────────────────────────────────────────────────────
const isViewMode = ref(true)

function moveCursorToEnd(el: HTMLElement) {
  const range = document.createRange()
  const sel = window.getSelection()
  range.selectNodeContents(el)
  range.collapse(false)
  sel?.removeAllRanges()
  sel?.addRange(range)
}

watch(isViewMode, async (viewMode) => {
  if (!viewMode) {
    await nextTick()
    if (editorEl.value) {
      editorEl.value.innerText = editingBody.value
      editorEl.value.focus()
      moveCursorToEnd(editorEl.value)
    }
  }
})

// ── Cursor section detection ──────────────────────────────────────────────
const cursorSectionId = ref<string | null>(null)
const editorEl = ref<HTMLDivElement>()
let detectTimer: ReturnType<typeof setTimeout> | null = null

function onSelectionChange() {
  if (detectTimer) clearTimeout(detectTimer)
  detectTimer = setTimeout(() => {
    const sel = window.getSelection()
    if (!sel?.rangeCount || !editorEl.value?.contains(sel.anchorNode)) return

    const range = sel.getRangeAt(0)
    const pre = document.createRange()
    pre.selectNodeContents(editorEl.value)
    pre.setEnd(range.startContainer, range.startOffset)
    const charOffset = pre.toString().length

    const textBefore = editingBody.value.slice(0, charOffset)
    const cursorLine = textBefore.split('\n').length - 1

    const heading = [...draftHeadings.value]
      .reverse()
      .find(h => h.line <= cursorLine)
    cursorSectionId.value = heading?.section_id ?? null
  }, 300)
}

// ── Load file ─────────────────────────────────────────────────────────────
async function loadFile(filename: string) {
  if (!projectId.value) return
  loading.value = true
  loadError.value = ''
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }

  try {
    const data = await drafts.get(projectId.value, filename)
    draftFilename.value = data.name
    draftContent.value = data.content
    draftHeadings.value = data.headings
    editingBody.value = data.content
    cursorSectionId.value = draftHeadings.value[0]?.section_id ?? null

    isViewMode.value = true
    loading.value = false
    await nextTick()
    if (editorEl.value) {
      editorEl.value.innerText = data.content
    }

    const newPath = `/${projectId.value}/edit/${encodeURIComponent(filename)}`
    if (route.path !== newPath) {
      router.replace({ name: 'edit-file', params: { projectId: projectId.value, filename } })
    }
  } catch (e: any) {
    loading.value = false
    loadError.value = e?.message ?? 'Не удалось загрузить файл'
  }
}

// ── Save ──────────────────────────────────────────────────────────────────
async function doSave() {
  if (!projectId.value || !draftFilename.value) return
  saveStatus.value = 'saving'
  try {
    const data = await drafts.save(projectId.value, draftFilename.value, editingBody.value)
    draftContent.value = data.content
    draftHeadings.value = data.headings
    saveStatus.value = 'saved'
    setTimeout(() => { if (saveStatus.value === 'saved') saveStatus.value = 'idle' }, 2500)
  } catch {
    saveStatus.value = 'error'
  }
}

function scheduleAutoSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(doSave, 800)
}

// ── Editor event handlers ─────────────────────────────────────────────────
function onEditorInput() {
  editingBody.value = editorEl.value?.innerText ?? ''
  scheduleAutoSave()
}

function onEditorKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    doSave()
  }
}

function onEditorPaste(e: ClipboardEvent) {
  e.preventDefault()
  const text = e.clipboardData?.getData('text/plain') ?? ''
  document.execCommand('insertText', false, text)
}

// ── AI Assistant strip ────────────────────────────────────────────────────
interface AssistantNote {
  id: number
  text: string
  timestamp: Date
}
let noteId = 0
const assistantNotes = ref<AssistantNote[]>([])
const assistantEl = ref<HTMLElement>()
const userInput = ref('')

function pushAssistantMessage(text: string) {
  assistantNotes.value.push({ id: ++noteId, text, timestamp: new Date() })
  nextTick(() => {
    assistantEl.value?.scrollTo({ top: assistantEl.value.scrollHeight, behavior: 'smooth' })
  })
}

async function sendUserMessage() {
  const text = userInput.value.trim()
  if (!text) return
  assistantNotes.value.push({ id: ++noteId, text: `**Вы:** ${text}`, timestamp: new Date() })
  userInput.value = ''
  await nextTick()
  assistantEl.value?.scrollTo({ top: assistantEl.value.scrollHeight, behavior: 'smooth' })
  await new Promise(r => setTimeout(r, 1000))
  pushAssistantMessage('Выберите раздел и воспользуйтесь поиском по библиотеке справа.')
}

function timeAgo(d: Date): string {
  const s = Math.round((Date.now() - d.getTime()) / 1000)
  if (s < 10) return 'сейчас'
  if (s < 60) return `${s}с`
  return `${Math.round(s / 60)}м`
}

// ── Lifecycle ─────────────────────────────────────────────────────────────
onMounted(async () => {
  document.addEventListener('selectionchange', onSelectionChange)

  const filenameParam = route.params.filename as string | undefined
  if (filenameParam) {
    await loadFile(decodeURIComponent(filenameParam))
  } else if (projectId.value) {
    try {
      const listData = await drafts.list(projectId.value)
      if (listData.files.length > 0) {
        const sorted = [...listData.files].sort((a, b) => {
          function order(n: string) {
            if (n.startsWith('intro')) return -1
            const m = n.match(/chapter_(\d+)/)
            if (m?.[1]) return parseInt(m[1])
            if (n.startsWith('conclusion')) return 99
            return 50
          }
          return order(a.name) - order(b.name)
        })
        const first = sorted[0]
        if (first) await loadFile(first.name)
      }
    } catch { /* no files yet */ }
  }
})

onUnmounted(() => {
  document.removeEventListener('selectionchange', onSelectionChange)
  if (saveTimer) clearTimeout(saveTimer)
  if (detectTimer) clearTimeout(detectTimer)
})

watch(() => route.params.filename, (filename) => {
  if (filename && filename !== draftFilename.value) {
    loadFile(decodeURIComponent(filename as string))
  }
})
</script>

<template>
  <AppLayout>
    <div class="max-w-6xl mx-auto">

      <!-- Error banner -->
      <div v-if="loadError" class="mb-4 rounded-md bg-[var(--color-err-bg)] border border-[var(--color-err)]/30 px-4 py-2 text-sm text-[var(--color-err)]">
        {{ loadError }}
      </div>

      <!-- 3-column layout -->
      <div class="flex gap-4 items-start">

        <!-- LEFT: File browser -->
        <nav class="w-48 flex-shrink-0 sticky top-8" style="max-height: calc(100vh - 6rem); overflow-y: auto;">
          <FileBrowser
            :projectId="projectId"
            :activeFile="draftFilename || null"
            @select="loadFile"
          />
        </nav>

        <!-- CENTER: Editor -->
        <div class="flex-1 min-w-0 flex flex-col gap-4">

          <!-- Title row -->
          <div class="flex items-center gap-3">
            <h1 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)] leading-snug flex-1">
              {{ draftFilename ? displayName(draftFilename) : '—' }}
            </h1>
            <span v-if="editWordCount > 0" class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">
              {{ editWordCount }}w
            </span>
            <button
              v-if="draftFilename"
              @click="isViewMode = !isViewMode"
              class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
              :class="isViewMode
                ? 'border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
                : 'border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent-pale)]'"
            >{{ isViewMode ? 'Редактировать' : 'Просмотр' }}</button>
          </div>

          <!-- Editor block -->
          <div class="rounded-lg border bg-[var(--color-paper-white)] transition-colors"
            :class="!isViewMode && draftFilename ? 'border-[var(--color-accent)]/40 shadow-sm' : 'border-[var(--color-rule)]'"
          >
            <!-- Loading spinner -->
            <div v-if="loading" class="flex items-center justify-center py-24">
              <div class="text-center">
                <div class="h-8 w-8 border-2 border-[var(--color-accent)] border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                <p class="text-sm text-[var(--color-ink-light)]">Загружаю файл…</p>
              </div>
            </div>

            <!-- VIEW mode: rendered markdown -->
            <div v-else-if="draftFilename && isViewMode"
              class="draft-prose px-8 py-6 text-[15px] text-[var(--color-ink)] leading-[1.8] font-[var(--font-body)] cursor-text"
              @click="isViewMode = false"
              v-html="editingBody ? renderDraft(editingBody) : '<p class=\'text-[var(--color-ink-muted)] italic\'>Файл пуст — нажмите для редактирования</p>'"
            />

            <!-- EDIT mode: contenteditable -->
            <div v-else-if="draftFilename && !isViewMode">
              <div class="relative">
                <div
                  ref="editorEl"
                  contenteditable="true"
                  spellcheck="false"
                  class="draft-editor w-full px-8 py-6 text-[15px] text-[var(--color-ink)] leading-[1.8] focus:outline-none font-[var(--font-body)]"
                  style="min-height: 40vh; white-space: pre-wrap; word-break: break-word;"
                  @input="onEditorInput"
                  @keydown="onEditorKeydown"
                  @paste.prevent="onEditorPaste"
                />
              </div>

              <!-- Footer bar -->
              <div class="flex items-center gap-3 border-t border-[var(--color-rule-light)] px-6 py-2.5">
                <transition name="ghost-hint">
                  <span v-if="saveStatus === 'saved'" class="text-xs text-[var(--color-ok)]">Сохранено ✓</span>
                </transition>
                <span v-if="saveStatus === 'saving'" class="text-xs text-[var(--color-ink-muted)] flex items-center gap-1.5">
                  <span class="h-3 w-3 border border-[var(--color-ink-muted)] border-t-transparent rounded-full animate-spin" />
                  Сохранение…
                </span>
                <span v-if="saveStatus === 'error'" class="text-xs text-[var(--color-err)]">Ошибка сохранения</span>
                <div class="flex-1" />
                <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">{{ editWordCount }} слов</span>
                <button
                  disabled
                  title="kAI автодополнение недоступно в режиме полного файла"
                  class="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold tracking-tight border-[var(--color-rule)] bg-transparent text-[var(--color-ink-muted)] opacity-40 cursor-not-allowed"
                >
                  <span class="h-[7px] w-[7px] rounded-full border bg-transparent border-[var(--color-ink-muted)] flex-shrink-0" />
                  kAI
                </button>
              </div>
            </div>

            <!-- No file selected -->
            <div v-else class="flex items-center justify-center py-20">
              <p class="text-sm text-[var(--color-ink-muted)]">Выберите файл в панели слева</p>
            </div>
          </div>

          <!-- AI ASSISTANT STRIP -->
          <div class="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden">
            <div ref="assistantEl" class="max-h-40 overflow-y-auto px-4 py-2.5 space-y-1.5">
              <div v-for="note in assistantNotes" :key="note.id" class="flex items-start gap-2">
                <div class="h-1.5 w-1.5 rounded-full bg-[var(--color-accent)] mt-1.5 flex-shrink-0" />
                <p class="text-sm text-[var(--color-ink-light)] leading-snug flex-1" v-html="formatMarkdown(note.text)" />
                <span class="text-xs text-[var(--color-ink-muted)] flex-shrink-0">{{ timeAgo(note.timestamp) }}</span>
              </div>
              <div v-if="assistantNotes.length === 0" class="text-xs text-[var(--color-ink-muted)] text-center py-2">
                Klemma наблюдает за вашей работой
              </div>
            </div>
            <div class="border-t border-[var(--color-rule-light)] px-4 py-2 flex items-center gap-2">
              <input
                v-model="userInput"
                @keydown.enter.prevent="sendUserMessage"
                class="flex-1 bg-transparent text-sm text-[var(--color-ink)] placeholder-[var(--color-ink-muted)] focus:outline-none"
                placeholder="Спросить Klemma..."
              />
              <button
                @click="sendUserMessage"
                :disabled="!userInput.trim()"
                class="text-[var(--color-accent)] disabled:text-[var(--color-rule)] transition-colors"
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4">
                  <path d="M2.87 2.298a.75.75 0 0 0-.812 1.021L3.39 6.624a1 1 0 0 0 .928.626H8.25a.75.75 0 0 1 0 1.5H4.318a1 1 0 0 0-.927.626l-1.333 3.305a.75.75 0 0 0 .811 1.022l11.502-3.593a.75.75 0 0 0 0-1.42L2.87 2.298Z" />
                </svg>
              </button>
            </div>
          </div>

        </div>

        <!-- RIGHT: Source panel -->
        <div class="w-52 flex-shrink-0" style="max-height: calc(100vh - 9rem);">
          <SourcePanel
            :sectionId="cursorSectionId"
            :projectId="projectId"
            :isDemoMode="false"
            @attach="() => {/* SourcePanel persists server-side internally */}"
            @detach="() => {/* detach is local-only in SourcePanel */}"
          />
        </div>

      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
[contenteditable]:focus {
  outline: none;
}

.ghost-hint-enter-active,
.ghost-hint-leave-active {
  transition: opacity 0.15s ease;
}
.ghost-hint-enter-from,
.ghost-hint-leave-to {
  opacity: 0;
}

.draft-prose :deep(h1),
.draft-prose :deep(h2),
.draft-prose :deep(h3) {
  font-weight: 600;
  color: var(--color-ink);
  margin-top: 1.5em;
  margin-bottom: 0.5em;
  line-height: 1.3;
}
.draft-prose :deep(h1) { font-size: 1.25em; }
.draft-prose :deep(h2) { font-size: 1.1em; }
.draft-prose :deep(h3) { font-size: 1em; }

.draft-prose :deep(p) {
  margin-bottom: 1em;
}
.draft-prose :deep(p:last-child) {
  margin-bottom: 0;
}

.draft-prose :deep(ul),
.draft-prose :deep(ol) {
  margin-bottom: 1em;
  padding-left: 1.5em;
}
.draft-prose :deep(li) {
  margin-bottom: 0.25em;
}

.draft-prose :deep(strong) {
  font-weight: 600;
}
.draft-prose :deep(em) {
  font-style: italic;
}

.draft-prose :deep(.citekey-ref) {
  font-family: var(--font-mono);
  font-size: 0.85em;
  color: var(--color-accent);
  background: var(--color-accent-pale);
  border-radius: 3px;
  padding: 0 3px;
}
</style>
