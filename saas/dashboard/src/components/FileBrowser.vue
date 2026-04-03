<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { drafts } from '@/api/client'
import type { DraftFile } from '@/api/client'

const props = defineProps<{
  projectId: string
  activeFile: string | null
}>()

const emit = defineEmits<{
  select: [filename: string]
}>()

const files = ref<DraftFile[]>([])
const loading = ref(false)

function fileOrder(name: string): number {
  if (name.startsWith('intro')) return -1
  const m = name.match(/chapter_(\d+)/)
  if (m?.[1]) return parseInt(m[1])
  if (name.startsWith('conclusion')) return 99
  return 50
}

function displayName(name: string): string {
  const map: Record<string, string> = {
    'intro.md': 'Введение',
    'conclusion.md': 'Заключение',
  }
  if (map[name]) return map[name]
  const m = name.match(/chapter_(\d+)\.md/)
  if (m) return `Глава ${m[1]}`
  return name.replace('.md', '')
}

const sortedFiles = computed(() =>
  [...files.value].sort((a, b) => fileOrder(a.name) - fileOrder(b.name))
)

async function loadFiles(pid: string) {
  if (!pid) return
  loading.value = true
  try {
    const data = await drafts.list(pid)
    files.value = data.files
  } catch {
    files.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.projectId, loadFiles, { immediate: true })
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- Header -->
    <div class="px-3 pt-3 pb-2 border-b border-[var(--color-rule-light)] flex-shrink-0">
      <p class="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Файлы</p>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="px-3 py-4 text-xs text-[var(--color-ink-muted)]">Загрузка…</div>

    <!-- Empty state -->
    <div
      v-else-if="files.length === 0"
      class="px-3 py-4 text-xs text-[var(--color-ink-muted)] italic leading-relaxed"
    >
      Нет файлов — запустите<br>
      <code class="font-[var(--font-mono)] text-[var(--color-accent)] not-italic">klemma-cli push</code>
    </div>

    <!-- File list -->
    <div v-else class="flex-1 overflow-y-auto py-1">
      <button
        v-for="file in sortedFiles"
        :key="file.name"
        @click="emit('select', file.name)"
        class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors"
        :class="activeFile === file.name
          ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)] font-medium'
          : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
      >
        <!-- File icon -->
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"
          class="h-3.5 w-3.5 flex-shrink-0 opacity-60">
          <path d="M4 2a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6.414A2 2 0 0 0 13.414 5L11 2.586A2 2 0 0 0 9.586 2H4Zm7 7a.75.75 0 0 1-.75.75h-4.5a.75.75 0 0 1 0-1.5h4.5A.75.75 0 0 1 11 9Zm0 2.5a.75.75 0 0 1-.75.75h-4.5a.75.75 0 0 1 0-1.5h4.5a.75.75 0 0 1 .75.75ZM6.75 5a.75.75 0 0 1 0-1.5H9a.75.75 0 0 1 0 1.5H6.75Z" />
        </svg>
        <span class="flex-1 truncate">{{ displayName(file.name) }}</span>
        <span
          v-if="file.word_count > 0"
          class="font-[var(--font-mono)] text-xs flex-shrink-0 opacity-50"
        >{{ file.word_count }}w</span>
      </button>
    </div>
  </div>
</template>
