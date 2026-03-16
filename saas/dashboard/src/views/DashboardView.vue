<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { analyze, library } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'

const route = useRoute()
const router = useRouter()

const status = ref<{
  sources: { total: number; completed: number; pending: number; failed: number }
  coverage: { section: string; source_count: number }[]
  total_fragments: number
} | null>(null)

const recentSources = ref<{ citekey: string; title: string; status: string; year: number | null }[]>([])
const loading = ref(true)

// Pipeline progress
const pipelineSteps = computed(() => {
  if (!status.value) return []
  const s = status.value
  const hasSources = s.sources.total > 0
  const hasProcessed = s.sources.completed > 0
  const hasCoverage = s.coverage.length > 0
  return [
    {
      key: 'upload',
      label: 'Загрузить',
      desc: 'Добавьте PDF-статьи в библиотеку',
      done: hasSources,
      active: !hasSources,
      count: s.sources.total,
      unit: 'источников',
    },
    {
      key: 'process',
      label: 'Обработать',
      desc: 'Извлеките фрагменты из статей',
      done: hasProcessed && s.sources.pending === 0,
      active: hasSources && !hasProcessed,
      count: s.sources.completed,
      unit: 'обработано',
    },
    {
      key: 'map',
      label: 'Разметить',
      desc: 'Назначьте источники разделам диссертации',
      done: hasCoverage,
      active: hasProcessed && !hasCoverage,
      count: s.coverage.length,
      unit: 'разделов',
    },
    {
      key: 'write',
      label: 'Написать',
      desc: 'Сгенерируйте обзор и черновики',
      done: false,
      active: hasCoverage,
      count: 0,
      unit: 'черновиков',
    },
  ]
})

// Coverage bar max
const maxCoverage = computed(() => {
  if (!status.value || status.value.coverage.length === 0) return 1
  return Math.max(...status.value.coverage.map(c => c.source_count))
})

onMounted(async () => {
  try {
    const [s, lib] = await Promise.all([analyze.status(), library.list()])
    status.value = s
    recentSources.value = lib.sources.slice(0, 5)
  } catch {
    router.push('/login')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <AppLayout>
    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <div v-else-if="status" class="space-y-10">
      <!-- Page title -->
      <div class="animate-in">
        <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
          Обзор исследования
        </h1>
        <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
          Текущее состояние вашей работы
        </p>
      </div>

      <!-- Pipeline steps -->
      <div class="animate-in animate-in-delay-1">
        <div class="grid grid-cols-4 gap-3">
          <div
            v-for="(step, i) in pipelineSteps"
            :key="step.key"
            class="relative rounded-xl border p-5 transition-all duration-200"
            :class="[
              step.active
                ? 'border-[var(--color-accent)] bg-[var(--color-accent-pale)] shadow-sm'
                : step.done
                  ? 'border-[var(--color-rule)] bg-[var(--color-paper-white)]'
                  : 'border-[var(--color-rule-light)] bg-[var(--color-paper-warm)] opacity-60'
            ]"
          >
            <!-- Step number -->
            <div class="flex items-center gap-3 mb-3">
              <div
                class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold"
                :class="[
                  step.done
                    ? 'bg-[var(--color-ok)] text-white'
                    : step.active
                      ? 'bg-[var(--color-accent)] text-white'
                      : 'bg-[var(--color-rule)] text-[var(--color-ink-muted)]'
                ]"
              >
                <span v-if="step.done">&#10003;</span>
                <span v-else>{{ i + 1 }}</span>
              </div>
              <span
                class="text-sm font-semibold"
                :class="step.active ? 'text-[var(--color-accent-deep)]' : 'text-[var(--color-ink)]'"
              >
                {{ step.label }}
              </span>
            </div>

            <p class="text-xs text-[var(--color-ink-muted)] leading-relaxed">{{ step.desc }}</p>

            <!-- Counter -->
            <div v-if="step.count > 0" class="mt-3 pt-3 border-t border-[var(--color-rule-light)]">
              <span class="font-[var(--font-mono)] text-lg font-medium text-[var(--color-ink)]">{{ step.count }}</span>
              <span class="ml-1 text-xs text-[var(--color-ink-muted)]">{{ step.unit }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Stats + Coverage row -->
      <div class="grid grid-cols-3 gap-6 animate-in animate-in-delay-2">
        <!-- Stats column -->
        <div class="col-span-1 space-y-4">
          <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
            Статистика
          </h2>

          <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] divide-y divide-[var(--color-rule-light)]">
            <div class="flex items-center justify-between px-5 py-4">
              <span class="text-sm text-[var(--color-ink-muted)]">Источников</span>
              <span class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-ink)]">{{ status.sources.total }}</span>
            </div>
            <div class="flex items-center justify-between px-5 py-4">
              <span class="text-sm text-[var(--color-ink-muted)]">Обработано</span>
              <span class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-ok)]">{{ status.sources.completed }}</span>
            </div>
            <div class="flex items-center justify-between px-5 py-4">
              <span class="text-sm text-[var(--color-ink-muted)]">В очереди</span>
              <span class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-warn)]">{{ status.sources.pending }}</span>
            </div>
            <div class="flex items-center justify-between px-5 py-4">
              <span class="text-sm text-[var(--color-ink-muted)]">Фрагментов</span>
              <span class="font-[var(--font-mono)] text-xl font-medium text-[var(--color-accent)]">{{ status.total_fragments }}</span>
            </div>
          </div>
        </div>

        <!-- Coverage column -->
        <div class="col-span-2 space-y-4">
          <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
            Покрытие по разделам
          </h2>

          <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-5">
            <div v-if="status.coverage.length === 0" class="py-8 text-center">
              <p class="text-sm text-[var(--color-ink-muted)]">
                Нет назначенных разделов.
              </p>
              <p class="mt-1 text-xs text-[var(--color-ink-muted)]">
                Обработайте источники и назначьте их разделам диссертации.
              </p>
            </div>

            <div v-else class="space-y-3">
              <div
                v-for="item in status.coverage"
                :key="item.section"
                class="group"
              >
                <div class="flex items-center justify-between mb-1.5">
                  <span class="text-sm font-medium text-[var(--color-ink)]">{{ item.section }}</span>
                  <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">{{ item.source_count }}</span>
                </div>
                <div class="h-2 w-full rounded-full bg-[var(--color-rule-light)] overflow-hidden">
                  <div
                    class="h-full rounded-full bg-[var(--color-accent)] transition-all duration-500"
                    :style="{ width: `${(item.source_count / maxCoverage) * 100}%` }"
                  ></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Recent sources -->
      <div class="animate-in animate-in-delay-3" v-if="recentSources.length > 0">
        <div class="flex items-center justify-between mb-4">
          <h2 class="font-[var(--font-display)] text-sm font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider">
            Недавние источники
          </h2>
          <RouterLink
            :to="`/${route.params.projectId}/library`"
            class="text-sm text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] transition-colors"
          >
            Все источники &rarr;
          </RouterLink>
        </div>

        <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden">
          <div
            v-for="(src, i) in recentSources"
            :key="src.citekey"
            class="flex items-center justify-between px-5 py-3.5 hover:bg-[var(--color-paper-warm)] transition-colors"
            :class="{ 'border-t border-[var(--color-rule-light)]': i > 0 }"
          >
            <div class="flex items-center gap-4 min-w-0">
              <span class="font-[var(--font-mono)] text-xs text-[var(--color-accent)] shrink-0">{{ src.citekey }}</span>
              <span class="text-sm text-[var(--color-ink)] truncate">{{ src.title || '—' }}</span>
              <span v-if="src.year" class="text-xs text-[var(--color-ink-muted)] shrink-0">{{ src.year }}</span>
            </div>
            <span
              class="shrink-0 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
              :class="{
                'bg-[var(--color-ok-bg)] text-[var(--color-ok)]': src.status === 'completed',
                'bg-[var(--color-warn-bg)] text-[var(--color-warn)]': src.status === 'pending',
                'bg-[var(--color-err-bg)] text-[var(--color-err)]': src.status === 'failed',
              }"
            >
              {{ src.status === 'completed' ? 'готово' : src.status === 'pending' ? 'ожидает' : 'ошибка' }}
            </span>
          </div>
        </div>
      </div>

      <!-- Empty state -->
      <div
        v-if="status.sources.total === 0"
        class="animate-in animate-in-delay-2 rounded-xl border-2 border-dashed border-[var(--color-rule)] p-16 text-center"
      >
        <div class="mx-auto w-16 h-16 rounded-2xl bg-[var(--color-accent-pale)] flex items-center justify-center mb-5">
          <svg class="w-8 h-8 text-[var(--color-accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.331 0 4.512.89 6.148 2.354M12 6.042c1.985-1.392 4.37-2.292 7.025-2.292.944 0 1.857.14 2.725.4v14.25A9.001 9.001 0 0018 18c-2.331 0-4.512.89-6.148 2.354M12 6.042V20.354" />
          </svg>
        </div>
        <h3 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)]">
          Начните исследование
        </h3>
        <p class="mt-2 text-sm text-[var(--color-ink-muted)] max-w-md mx-auto leading-relaxed">
          Загрузите PDF-статьи в библиотеку. LitResearch извлечёт ключевые фрагменты,
          оценит покрытие по разделам и поможет написать обзор литературы.
        </p>
        <RouterLink
          :to="`/${route.params.projectId}/library`"
          class="mt-6 inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[var(--color-accent-deep)] transition-colors shadow-sm"
        >
          Перейти в библиотеку
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
          </svg>
        </RouterLink>
      </div>
    </div>
  </AppLayout>
</template>
