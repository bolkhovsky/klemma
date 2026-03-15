<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, RouterLink } from 'vue-router'
import { projects, library } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'

const router = useRouter()

const coverage = ref<{ total_sources: number; sections: Record<string, number>; chapters: Record<string, number> } | null>(null)
const sectionSources = ref<Record<string, string[]>>({})
const expandedSection = ref<string | null>(null)
const loading = ref(true)
const allSources = ref<{ citekey: string; title: string; status: string }[]>([])

const sortedSections = computed(() => {
  if (!coverage.value) return []
  return Object.entries(coverage.value.sections)
    .sort(([a], [b]) => {
      const aParts = a.split('.').map(Number)
      const bParts = b.split('.').map(Number)
      for (let i = 0; i < Math.max(aParts.length, bParts.length); i++) {
        const diff = (aParts[i] || 0) - (bParts[i] || 0)
        if (diff !== 0) return diff
      }
      return 0
    })
})

const maxCount = computed(() => {
  if (!coverage.value) return 1
  const vals = Object.values(coverage.value.sections)
  return Math.max(...vals, 1)
})

const unassignedCount = computed(() => {
  if (!coverage.value) return 0
  const assignedKeys = new Set(Object.values(sectionSources.value).flat())
  return allSources.value.filter(s => !assignedKeys.has(s.citekey)).length
})

async function toggleSection(section: string) {
  if (expandedSection.value === section) {
    expandedSection.value = null
    return
  }
  expandedSection.value = section
  if (!sectionSources.value[section]) {
    try {
      const resp = await projects.sectionSources(section)
      sectionSources.value[section] = resp.citekeys
    } catch {
      sectionSources.value[section] = []
    }
  }
}

onMounted(async () => {
  try {
    const [cov, lib] = await Promise.all([projects.coverage(), library.list()])
    coverage.value = cov
    allSources.value = lib.sources
  } catch {
    router.push('/login')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <AppLayout>
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <div v-else-if="coverage" class="space-y-8">
      <!-- Header -->
      <div class="animate-in">
        <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
          Карта покрытия
        </h1>
        <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
          Распределение источников по разделам диссертации
        </p>
      </div>

      <!-- Summary cards -->
      <div class="grid grid-cols-3 gap-4 animate-in animate-in-delay-1">
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-5">
          <div class="font-[var(--font-mono)] text-2xl font-medium text-[var(--color-ink)]">{{ coverage.total_sources }}</div>
          <div class="text-sm text-[var(--color-ink-muted)]">Назначено источников</div>
        </div>
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-5">
          <div class="font-[var(--font-mono)] text-2xl font-medium text-[var(--color-accent)]">{{ sortedSections.length }}</div>
          <div class="text-sm text-[var(--color-ink-muted)]">Разделов с источниками</div>
        </div>
        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-5">
          <div class="font-[var(--font-mono)] text-2xl font-medium" :class="unassignedCount > 0 ? 'text-[var(--color-warn)]' : 'text-[var(--color-ok)]'">
            {{ unassignedCount }}
          </div>
          <div class="text-sm text-[var(--color-ink-muted)]">Без раздела</div>
        </div>
      </div>

      <!-- Coverage map -->
      <div class="animate-in animate-in-delay-2">
        <!-- Empty state -->
        <div v-if="sortedSections.length === 0" class="rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center">
          <div class="mx-auto w-12 h-12 rounded-xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-4">
            <svg class="w-6 h-6 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
            </svg>
          </div>
          <h3 class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)]">Нет назначенных разделов</h3>
          <p class="mt-2 text-sm text-[var(--color-ink-muted)] max-w-md mx-auto">
            Откройте источник в библиотеке и назначьте его разделам диссертации.
          </p>
          <RouterLink
            to="/library"
            class="mt-5 inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] transition-colors"
          >
            Перейти в библиотеку
          </RouterLink>
        </div>

        <!-- Sections list -->
        <div v-else class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden">
          <div
            v-for="([section, count], i) in sortedSections"
            :key="section"
            :class="{ 'border-t border-[var(--color-rule-light)]': i > 0 }"
          >
            <!-- Section row -->
            <button
              @click="toggleSection(section)"
              class="w-full flex items-center gap-4 px-5 py-4 hover:bg-[var(--color-paper-warm)] transition-colors text-left"
            >
              <!-- Section number -->
              <span class="font-[var(--font-mono)] text-sm font-medium text-[var(--color-accent)] w-16 shrink-0">
                {{ section }}
              </span>

              <!-- Bar -->
              <div class="flex-1">
                <div class="h-3 w-full rounded-full bg-[var(--color-rule-light)] overflow-hidden">
                  <div
                    class="h-full rounded-full transition-all duration-500"
                    :class="count >= 10 ? 'bg-[var(--color-ok)]' : count >= 5 ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-warn)]'"
                    :style="{ width: `${(count / maxCount) * 100}%` }"
                  ></div>
                </div>
              </div>

              <!-- Count -->
              <span class="font-[var(--font-mono)] text-sm text-[var(--color-ink-muted)] w-8 text-right shrink-0">
                {{ count }}
              </span>

              <!-- Expand indicator -->
              <svg
                class="w-4 h-4 text-[var(--color-ink-muted)] transition-transform shrink-0"
                :class="{ 'rotate-180': expandedSection === section }"
                fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
              >
                <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
              </svg>
            </button>

            <!-- Expanded: sources in this section -->
            <div v-if="expandedSection === section" class="bg-[var(--color-paper-warm)] px-5 py-3 border-t border-[var(--color-rule-light)]">
              <div v-if="!sectionSources[section]" class="text-sm text-[var(--color-ink-muted)]">Загрузка...</div>
              <div v-else-if="sectionSources[section].length === 0" class="text-sm text-[var(--color-ink-muted)]">Нет источников</div>
              <div v-else class="space-y-1">
                <RouterLink
                  v-for="ck in sectionSources[section]"
                  :key="ck"
                  :to="`/library/${ck}`"
                  class="block font-[var(--font-mono)] text-sm text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] hover:underline transition-colors py-0.5"
                >
                  {{ ck }}
                </RouterLink>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
