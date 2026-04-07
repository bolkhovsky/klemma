<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { library } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'

interface Source {
  citekey: string
  title: string
  authors: string
  year: number | null
  status: string
  doi: string | null
}

const sources = ref<Source[]>([])
const loading = ref(true)

async function loadSources() {
  loading.value = true
  try {
    const data = await library.list()
    sources.value = data.sources
  } catch {
    sources.value = []
  } finally {
    loading.value = false
  }
}

onMounted(loadSources)

const statusLabel: Record<string, string> = {
  completed: 'готово',
  pending: 'ожидает',
  processing: 'обработка',
  failed: 'ошибка',
  queued: 'в очереди',
}
</script>

<template>
  <AppLayout>
    <div class="animate-in">
      <div class="mb-6">
        <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
          Моя библиотека
        </h1>
        <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
          Все источники из всех проектов
        </p>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex items-center justify-center py-16">
        <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
      </div>

      <!-- Empty -->
      <div v-else-if="sources.length === 0" class="rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center">
        <div class="mx-auto w-14 h-14 rounded-2xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-4">
          <svg class="w-7 h-7 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.331 0 4.512.89 6.148 2.354M12 6.042c1.985-1.392 4.37-2.292 7.025-2.292.944 0 1.857.14 2.725.4v14.25A9.001 9.001 0 0018 18c-2.331 0-4.512.89-6.148 2.354M12 6.042V20.354" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)]">Библиотека пуста</h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)]">
          Создайте проект и загрузите источники в его библиотеку.
        </p>
      </div>

      <!-- Table -->
      <div v-else class="overflow-hidden rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)]">
        <div class="px-5 py-3 bg-[var(--color-paper-warm)] border-b border-[var(--color-rule-light)]">
          <span class="text-sm font-semibold text-[var(--color-ink-muted)]">{{ sources.length }} источник(ов)</span>
        </div>
        <table class="min-w-full divide-y divide-[var(--color-rule-light)]">
          <thead class="bg-[var(--color-paper-warm)]">
            <tr>
              <th class="px-5 py-3 text-left text-sm font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Citekey</th>
              <th class="px-5 py-3 text-left text-sm font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Название</th>
              <th class="px-5 py-3 text-left text-sm font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Авторы</th>
              <th class="px-5 py-3 text-left text-sm font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Год</th>
              <th class="px-5 py-3 text-left text-sm font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Статус</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--color-rule-light)]">
            <tr v-for="src in sources" :key="src.citekey" class="hover:bg-[var(--color-paper-warm)] transition-colors">
              <td class="px-5 py-3.5 font-[var(--font-mono)] text-sm text-[var(--color-accent)]">
                {{ src.citekey }}
              </td>
              <td class="max-w-xs truncate px-5 py-3.5 text-sm text-[var(--color-ink)]">{{ src.title || '—' }}</td>
              <td class="max-w-[150px] truncate px-5 py-3.5 text-sm text-[var(--color-ink-muted)]">{{ src.authors || '—' }}</td>
              <td class="px-5 py-3.5 text-sm font-[var(--font-mono)] text-[var(--color-ink-muted)]">{{ src.year || '—' }}</td>
              <td class="px-5 py-3.5">
                <span
                  class="inline-block rounded-full px-2.5 py-1 text-sm font-medium"
                  :class="{
                    'bg-[var(--color-ok-bg)] text-[var(--color-ok)]': src.status === 'completed',
                    'bg-[var(--color-warn-bg)] text-[var(--color-warn)]': src.status === 'pending' || src.status === 'processing' || src.status === 'queued',
                    'bg-[var(--color-err-bg)] text-[var(--color-err)]': src.status === 'failed',
                  }"
                >
                  {{ statusLabel[src.status] ?? src.status }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </AppLayout>
</template>
