<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'

const router = useRouter()
const route = useRoute()

// ── Types ────────────────────────────────────────────────────────────────

interface FeedInsight {
  id: number
  type: 'blind_spot' | 'hidden_cluster'
  title: string
  explanation: string
  trajectory: string
  diversity_tag: 'methodology' | 'bridge' | 'gap' | 'anomaly'
  sections: string[]
  status: 'pending' | 'interested' | 'dismissed'
  created_at: string
  // blind_spot specific
  source_count?: number
  average_count?: number
  // hidden_cluster specific
  citekey_a?: string
  citekey_b?: string
  similarity?: number
  section_a?: string
  section_b?: string
}

// ── Mock data ────────────────────────────────────────────────────────────

const mockInsights: FeedInsight[] = [
  {
    id: 61,
    type: 'blind_spot',
    title: 'Стратификация по условиям инициализации',
    explanation: 'Раздел 3.2.1 не учитывает зависимость качества прогноза от месяца инициализации. Collow (2015) и Chevallier (2013) показывают значимое влияние начальных условий на RMSE.',
    trajectory: 'Добавление анализа чувствительности метрик к начальным условиям усилит методологическую обоснованность и выделит диссертацию среди работ, использующих единую агрегированную оценку.',
    diversity_tag: 'methodology',
    sections: ['1.4', '3.2', '3.2.1'],
    status: 'pending',
    created_at: '2026-03-23T17:13:00',
    source_count: 2,
    average_count: 8.5,
  },
  {
    id: 62,
    type: 'hidden_cluster',
    title: 'Неосознанный мост: initialization month dependence',
    explanation: 'Tietsche (2014) и Day (2014) из разных разделов описывают одно явление — зависимость предсказуемости от сезона инициализации. Связь не отражена в структуре работы.',
    trajectory: 'Консолидация в разделе 1.2.3 создаст единый теоретический фундамент для экспериментов в главе 3, вместо рассеянных упоминаний.',
    diversity_tag: 'bridge',
    sections: ['1.2', '1.2.3', '3.2.1'],
    status: 'pending',
    created_at: '2026-03-23T17:13:00',
    citekey_a: 'tietsche2014',
    citekey_b: 'day2014',
    similarity: 0.87,
    section_a: '1.2',
    section_b: '3.2.1',
  },
  {
    id: 63,
    type: 'hidden_cluster',
    title: 'Аффилиация РГГМУ и раздел 4.4',
    explanation: 'Публикация с аффилиацией РГГМУ размещена в обзорном разделе 1.3, но методологически относится к практическому применению (раздел 4.4).',
    trajectory: 'Перемещение усилит раздел 4.4 и продемонстрирует связь диссертации с работами кафедры.',
    diversity_tag: 'anomaly',
    sections: ['1.3', '1.3.1', '4.4'],
    status: 'interested',
    created_at: '2026-03-23T17:15:00',
  },
  {
    id: 64,
    type: 'blind_spot',
    title: 'Недостаток источников по валидации SPS',
    explanation: 'Раздел 3.3 содержит только 3 источника по Spatial Probability Score, тогда как среднее покрытие — 9.2 источника на раздел.',
    trajectory: 'Поиск работ Dukhovskoy и Goessling по SPS закроет пробел и обеспечит сравнимость с IIEE-ориентированными разделами.',
    diversity_tag: 'gap',
    sections: ['3.3'],
    status: 'pending',
    created_at: '2026-03-22T10:00:00',
    source_count: 3,
    average_count: 9.2,
  },
  {
    id: 65,
    type: 'blind_spot',
    title: 'Раздел 5.1 без свежих источников',
    explanation: 'Все 4 источника раздела 5.1 старше 2018 года. Появились новые работы по верификации ансамблевых прогнозов (2022-2025).',
    trajectory: 'Обновление базы позволит показать, что результаты диссертации сопоставимы с актуальными мировыми данными.',
    diversity_tag: 'gap',
    sections: ['5.1'],
    status: 'dismissed',
    created_at: '2026-03-21T14:30:00',
    source_count: 4,
    average_count: 9.2,
  },
]

// ── State ────────────────────────────────────────────────────────────────

const insights = ref<FeedInsight[]>(mockInsights)
const activeFilter = ref<'all' | 'pending' | 'interested' | 'dismissed'>('all')

const filteredInsights = computed(() => {
  if (activeFilter.value === 'all') return insights.value
  return insights.value.filter(i => i.status === activeFilter.value)
})

const counts = computed(() => ({
  all: insights.value.length,
  pending: insights.value.filter(i => i.status === 'pending').length,
  interested: insights.value.filter(i => i.status === 'interested').length,
  dismissed: insights.value.filter(i => i.status === 'dismissed').length,
}))

// ── Actions ──────────────────────────────────────────────────────────────

function markInterested(id: number) {
  const insight = insights.value.find(i => i.id === id)
  if (insight) insight.status = 'interested'
  // Navigate to detail page
  const projectId = route.params.projectId || 'demo'
  router.push(`/${projectId}/feed/${id}`)
}

function dismiss(id: number) {
  const insight = insights.value.find(i => i.id === id)
  if (insight) insight.status = 'dismissed'
}

// ── Helpers ──────────────────────────────────────────────────────────────

const tagConfig: Record<string, { label: string; color: string; bg: string }> = {
  methodology: { label: 'Методология', color: 'var(--color-accent)', bg: 'var(--color-accent-pale)' },
  bridge:      { label: 'Мост',        color: 'var(--color-violet)', bg: 'var(--color-violet-pale)' },
  gap:         { label: 'Пробел',      color: 'var(--color-amber)',  bg: 'var(--color-amber-pale)' },
  anomaly:     { label: 'Аномалия',    color: 'var(--color-cta)',    bg: 'var(--color-cta-pale)' },
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
}
</script>

<template>
  <AppLayout>
    <div class="max-w-3xl mx-auto">
      <!-- Header -->
      <div class="mb-8">
        <h1 class="font-[var(--font-display)] text-2xl font-semibold text-[var(--color-ink)] tracking-tight">
          Открытия
        </h1>
        <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
          Что система нашла в вашей библиотеке
        </p>
      </div>

      <!-- Filter tabs -->
      <div class="flex items-center gap-1 mb-6 border-b border-[var(--color-rule)]">
        <button
          v-for="(label, key) in ({ all: 'Все', pending: 'Новые', interested: 'Интересные', dismissed: 'Отклонённые' } as Record<string, string>)"
          :key="key"
          @click="activeFilter = key as typeof activeFilter"
          class="relative px-3 py-2 text-sm font-medium transition-colors"
          :class="activeFilter === key
            ? 'text-[var(--color-accent-deep)]'
            : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'"
        >
          {{ label }}
          <span
            v-if="counts[key as keyof typeof counts] > 0"
            class="ml-1 font-[var(--font-mono)] text-xs"
            :class="activeFilter === key ? 'text-[var(--color-accent)]' : 'text-[var(--color-ink-muted)]'"
          >
            {{ counts[key as keyof typeof counts] }}
          </span>
          <span
            v-if="activeFilter === key"
            class="absolute bottom-0 left-0 right-0 h-0.5 bg-[var(--color-accent)] rounded-t"
          />
        </button>
      </div>

      <!-- Empty state -->
      <div v-if="filteredInsights.length === 0" class="py-16 text-center">
        <div class="inline-flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-accent-pale)] mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="h-6 w-6 text-[var(--color-accent)]">
            <path fill-rule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm3.857-9.809a.75.75 0 0 0-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 1 0-1.06 1.061l2.5 2.5a.75.75 0 0 0 1.137-.089l4-5.5Z" clip-rule="evenodd" />
          </svg>
        </div>
        <p class="text-sm text-[var(--color-ink-muted)]">Нет открытий в этой категории</p>
      </div>

      <!-- Insight cards -->
      <div class="space-y-3">
        <div
          v-for="insight in filteredInsights"
          :key="insight.id"
          class="group rounded-lg border bg-[var(--color-paper-white)] transition-all duration-200"
          :class="insight.status === 'pending'
            ? 'border-[var(--color-accent)]/30 shadow-sm'
            : insight.status === 'dismissed'
              ? 'border-[var(--color-rule)] opacity-50'
              : 'border-[var(--color-rule)]'"
        >
          <div class="px-5 pt-4 pb-4">
            <!-- Tag + meta row -->
            <div class="flex items-center gap-2 mb-2">
              <span
                class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
                :style="{
                  color: tagConfig[insight.diversity_tag]?.color,
                  backgroundColor: tagConfig[insight.diversity_tag]?.bg,
                }"
              >
                {{ tagConfig[insight.diversity_tag]?.label }}
              </span>
              <span class="text-xs text-[var(--color-ink-muted)]">{{ formatDate(insight.created_at) }}</span>
              <span class="text-xs text-[var(--color-ink-muted)]">{{ insight.sections.join(', ') }}</span>
              <span v-if="insight.type === 'hidden_cluster' && insight.similarity" class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">
                sim {{ insight.similarity.toFixed(2) }}
              </span>

              <!-- Status badge (non-pending) -->
              <span
                v-if="insight.status === 'interested'"
                class="ml-auto inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-[var(--color-accent-pale)] text-[var(--color-accent-deep)]"
              >
                Интересно
              </span>
              <span
                v-else-if="insight.status === 'dismissed'"
                class="ml-auto inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-[var(--color-rule-light)] text-[var(--color-ink-muted)]"
              >
                Отклонено
              </span>
            </div>

            <!-- Title -->
            <h3 class="font-[var(--font-display)] text-base font-semibold text-[var(--color-ink)] leading-snug mb-2">
              {{ insight.title }}
            </h3>

            <!-- Explanation (WHY) — always visible -->
            <p class="text-sm text-[var(--color-ink-light)] leading-relaxed">
              {{ insight.explanation }}
            </p>
          </div>

          <!-- Actions — clean bottom bar -->
          <div class="flex items-center border-t border-[var(--color-rule-light)] px-5 py-2.5">
            <template v-if="insight.status === 'pending'">
              <button
                @click="markInterested(insight.id)"
                class="rounded-md bg-[var(--color-accent)] px-4 py-1.5 text-sm font-medium text-white hover:bg-[var(--color-accent-deep)] transition-colors"
              >
                Это интересно
              </button>
              <button
                @click="dismiss(insight.id)"
                class="ml-2 rounded-md px-3 py-1.5 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)] transition-colors"
              >
                Отклонить
              </button>
            </template>
            <template v-else-if="insight.status === 'interested'">
              <button
                @click="markInterested(insight.id)"
                class="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent-pale)] transition-colors"
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4">
                  <path fill-rule="evenodd" d="M2 8a6 6 0 1 1 12 0A6 6 0 0 1 2 8Zm6.75-2.5a.75.75 0 0 0-1.5 0v2.69L5.97 9.47a.75.75 0 0 0 1.06 1.06l1.5-1.5a.75.75 0 0 0 .22-.53V5.5Z" clip-rule="evenodd" />
                </svg>
                Открыть обсуждение
              </button>
            </template>

            <span class="ml-auto font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">#{{ insight.id }}</span>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
