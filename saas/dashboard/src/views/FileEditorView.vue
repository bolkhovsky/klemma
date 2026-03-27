<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import FileBrowser from '@/components/FileBrowser.vue'
import SourcePanel from '@/components/SourcePanel.vue'
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

// ── Cursor section detection ──────────────────────────────────────────────
const cursorSectionId = ref<string | null>(null)
const editorEl = ref<HTMLDivElement>()
let detectTimer: ReturnType<typeof setTimeout> | null = null

function onSelectionChange() {
  if (detectTimer) clearTimeout(detectTimer)
  detectTimer = setTimeout(() => {
    const sel = window.getSelection()
    if (!sel?.rangeCount || !editorEl.value?.contains(sel.anchorNode)) return

    // Compute character offset within editor
    const range = sel.getRangeAt(0)
    const pre = document.createRange()
    pre.selectNodeContents(editorEl.value)
    pre.setEnd(range.startContainer, range.startOffset)
    const charOffset = pre.toString().length

    // Map char offset → line number
    const textBefore = editingBody.value.slice(0, charOffset)
    const cursorLine = textBefore.split('\n').length - 1

    // Find last heading at or before cursorLine
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
  // Pending save: flush before loading new file
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }

  try {
    const data = await drafts.get(projectId.value, filename)
    draftFilename.value = data.name
    draftContent.value = data.content
    draftHeadings.value = data.headings
    editingBody.value = data.content
    cursorSectionId.value = draftHeadings.value[0]?.section_id ?? null

    await nextTick()
    if (editorEl.value) {
      editorEl.value.innerText = data.content
    }

    // Update URL without full navigation
    const newPath = `/${projectId.value}/edit/${encodeURIComponent(filename)}`
    if (route.path !== newPath) {
      router.replace({ name: 'edit-file', params: { projectId: projectId.value, filename } })
    }
  } catch (e: any) {
    loadError.value = e?.message ?? 'Не удалось загрузить файл'
  } finally {
    loading.value = false
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
  // Ctrl/Cmd+S → immediate save
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

// ── Lifecycle ─────────────────────────────────────────────────────────────
onMounted(async () => {
  document.addEventListener('selectionchange', onSelectionChange)

  const filenameParam = route.params.filename as string | undefined
  if (filenameParam) {
    await loadFile(decodeURIComponent(filenameParam))
  } else if (projectId.value) {
    // Auto-load first available file
    try {
      const listData = await drafts.list(projectId.value)
      if (listData.files.length > 0) {
        // Sort: intro first, then chapters, then conclusion
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

// Watch for route filename changes (browser back/forward)
watch(() => route.params.filename, (filename) => {
  if (filename && filename !== draftFilename.value) {
    loadFile(decodeURIComponent(filename as string))
  }
})
</script>

<template>
  <AppLayout>
    <!-- Override AppLayout padding: full-bleed 3-column layout -->
    <div class="-mx-8 -my-8 flex" style="min-height: calc(100vh - 2rem)">

      <!-- LEFT: File browser (w-44) -->
      <div class="w-44 flex-shrink-0 border-r border-[var(--color-rule-light)] bg-[var(--color-paper-warm)] overflow-hidden">
        <FileBrowser
          :projectId="projectId"
          :activeFile="draftFilename || null"
          @select="loadFile"
        />
      </div>

      <!-- CENTER: Editor -->
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- Toolbar -->
        <div class="flex items-center gap-3 px-6 py-2 border-b border-[var(--color-rule-light)] flex-shrink-0 min-h-[2.5rem]">
          <span v-if="draftFilename" class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)] truncate">
            {{ draftFilename }}
          </span>
          <div class="flex-1" />
          <!-- Save status -->
          <span v-if="saveStatus === 'saving'" class="text-xs text-[var(--color-ink-muted)] flex items-center gap-1.5">
            <span class="h-3 w-3 border border-[var(--color-ink-muted)] border-t-transparent rounded-full animate-spin" />
            Сохранение…
          </span>
          <span v-else-if="saveStatus === 'saved'" class="text-xs text-[var(--color-ok)]">
            Сохранено ✓
          </span>
          <span v-else-if="saveStatus === 'error'" class="text-xs text-[var(--color-err)]">
            Ошибка сохранения
          </span>
        </div>

        <!-- Error banner -->
        <div v-if="loadError" class="mx-6 mt-3 rounded-md bg-[var(--color-err-bg)] border border-[var(--color-err)]/30 px-4 py-2 text-sm text-[var(--color-err)] flex-shrink-0">
          {{ loadError }}
        </div>

        <!-- Loading state -->
        <div v-if="loading" class="flex-1 flex items-center justify-center">
          <div class="flex items-center gap-2 text-sm text-[var(--color-ink-muted)]">
            <div class="h-4 w-4 border-2 border-[var(--color-accent)] border-t-transparent rounded-full animate-spin" />
            Загрузка…
          </div>
        </div>

        <!-- Empty placeholder -->
        <div v-else-if="!draftFilename" class="flex-1 flex items-center justify-center">
          <p class="text-sm text-[var(--color-ink-muted)] italic">Выберите файл в панели слева</p>
        </div>

        <!-- Editor -->
        <div v-else class="flex-1 overflow-y-auto px-8 py-6">
          <div
            ref="editorEl"
            contenteditable="true"
            spellcheck="false"
            style="white-space: pre-wrap; font-size: 15px; line-height: 1.7; min-height: 200px; outline: none"
            class="draft-editor text-[var(--color-ink)] max-w-3xl mx-auto"
            @input="onEditorInput"
            @keydown="onEditorKeydown"
            @paste.prevent="onEditorPaste"
          />
        </div>
      </div>

      <!-- RIGHT: Source panel (w-52) -->
      <div class="w-52 flex-shrink-0 border-l border-[var(--color-rule-light)] overflow-hidden">
        <SourcePanel
          :sectionId="cursorSectionId"
          :projectId="projectId"
          :isDemoMode="false"
          @attach="() => {/* SourcePanel persists server-side internally */}"
          @detach="() => {/* detach is local-only in SourcePanel */}"
        />
      </div>

    </div>
  </AppLayout>
</template>

<style scoped>
.draft-editor :deep(h2),
.draft-editor :deep(h3),
.draft-editor :deep(h4) {
  font-weight: 600;
  color: var(--color-ink);
}
</style>
