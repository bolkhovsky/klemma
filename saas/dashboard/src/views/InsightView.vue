<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { formatMarkdown } from '@/utils/markdown'

const route = useRoute()
const router = useRouter()

// ── Mock insight ─────────────────────────────────────────────────────────

const insight = ref({
  id: 61,
  type: 'blind_spot' as const,
  title: 'Стратификация по условиям инициализации',
  explanation: 'Раздел 3.2.1 не учитывает зависимость качества прогноза от месяца инициализации. Collow (2015) и Chevallier (2013) показывают значимое влияние начальных условий на RMSE.',
  trajectory: 'Добавление анализа чувствительности метрик к начальным условиям усилит методологическую обоснованность и выделит диссертацию среди работ, использующих единую агрегированную оценку.',
  diversity_tag: 'methodology',
  sections: ['1.4', '3.2', '3.2.1'],
  related_sources: [
    { citekey: 'collow2015', title: 'The Seasonal Cycle of Sea Ice Extent in the GFDL Coupled Model', year: 2015, section: '1.4' },
    { citekey: 'chevallier2013', title: 'The Role of Sea Ice Initialization in Seasonal Forecasts', year: 2013, section: '3.2' },
    { citekey: 'tietsche2014', title: 'Seasonal to Interannual Arctic Sea Ice Predictability', year: 2014, section: '1.2' },
  ],
})

// ── Chat with hypothesis proposals ───────────────────────────────────────

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  hypotheses?: { id: string; text: string }[]
}

const chatInput = ref('')
const chatLoading = ref(false)
const chatEl = ref<HTMLElement>()
const pinnedHypothesis = ref<string | null>(null)

const messages = ref<ChatMessage[]>([])

// Initial message with hypothesis proposals
onMounted(() => {
  messages.value.push({
    role: 'assistant',
    content: `В вашей библиотеке **[@collow2015]**, **[@chevallier2013]** и **[@tietsche2014]** описывают зависимость качества прогноза от сезона инициализации, но в разных разделах. Связь не эксплицирована.\n\nНа основе этих источников я вижу три возможные гипотезы:`,
    hypotheses: [
      {
        id: 'h1',
        text: 'Качество прогноза ледовой кромки зависит не только от горизонта, но и от сезона инициализации — прогнозы, инициализированные в переходные сезоны (весна, осень), теряют навык быстрее из-за нелинейности отклика льда на радиационный форсинг.',
      },
      {
        id: 'h2',
        text: 'Initialization shock ([@chevallier2013]) искажает метрики IIEE и RMSE в первые 5-10 дней прогноза, и стандартная агрегированная оценка маскирует этот эффект — необходима стратификация по lead time windows.',
      },
      {
        id: 'h3',
        text: 'Потеря предсказуемости ([@tietsche2014], корреляция 0.8→0.3 за 3-6 мес) неравномерна по секторам Арктики — консолидация результатов Collow и Tietsche позволит показать региональную зависимость.',
      },
    ],
  })
})

async function sendMessage() {
  const text = chatInput.value.trim()
  if (!text || chatLoading.value) return

  messages.value.push({ role: 'user', content: text })
  chatInput.value = ''
  chatLoading.value = true

  await nextTick()
  scrollToBottom()

  // Simulate AI response
  await new Promise(r => setTimeout(r, 1200))

  messages.value.push({
    role: 'assistant',
    content: `Интересное уточнение. Если вы хотите сфокусироваться на региональной неравномерности, то стоит добавить **[@massonnet2012]** — он показывает, что Баренцево море демонстрирует аномально высокую предсказуемость по сравнению с Чукотским.\n\nОбновлённая гипотеза:`,
    hypotheses: [
      {
        id: 'h4',
        text: 'Потеря предсказуемости морского льда после инициализации неравномерна по арктическим секторам: Баренцево море сохраняет skill дольше (3-4 мес) из-за доминирования термодинамических процессов, тогда как Чукотское и Берингово теряют навык за 1-2 мес из-за влияния динамики.',
      },
    ],
  })
  chatLoading.value = false

  await nextTick()
  scrollToBottom()
}

function selectHypothesis(text: string) {
  pinnedHypothesis.value = text
  scrollToBottom()
}

function unpinHypothesis() {
  pinnedHypothesis.value = null
}

function refineHypothesis(text: string) {
  chatInput.value = `Уточнение гипотезы: ${text.slice(0, 100)}...`
  pinnedHypothesis.value = null
}

function scrollToBottom() {
  nextTick(() => {
    chatEl.value?.scrollTo({ top: chatEl.value.scrollHeight, behavior: 'smooth' })
  })
}

// formatMarkdown imported from @/utils/markdown

const tagConfig: Record<string, { label: string; color: string; bg: string }> = {
  methodology: { label: 'Методология', color: 'var(--color-accent)', bg: 'var(--color-accent-pale)' },
  bridge:      { label: 'Мост',        color: 'var(--color-violet)', bg: 'var(--color-violet-pale)' },
  gap:         { label: 'Пробел',      color: 'var(--color-amber)',  bg: 'var(--color-amber-pale)' },
  anomaly:     { label: 'Аномалия',    color: 'var(--color-cta)',    bg: 'var(--color-cta-pale)' },
}
</script>

<template>
  <AppLayout>
    <div class="max-w-5xl mx-auto">
      <!-- Back -->
      <button
        @click="router.back()"
        class="flex items-center gap-1.5 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors mb-5"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4">
          <path fill-rule="evenodd" d="M12 8a.75.75 0 0 1-.75.75H5.81l2.72 2.72a.75.75 0 1 1-1.06 1.06l-4-4a.75.75 0 0 1 0-1.06l4-4a.75.75 0 0 1 1.06 1.06L5.81 7.25h5.44A.75.75 0 0 1 12 8Z" clip-rule="evenodd" />
        </svg>
        Открытия
      </button>

      <div class="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <!-- Left sidebar: context (2/5) -->
        <div class="lg:col-span-2 space-y-4">
          <!-- Insight summary -->
          <div class="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-5">
            <div class="flex items-center gap-2 mb-3">
              <span
                class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
                :style="{
                  color: tagConfig[insight.diversity_tag]?.color,
                  backgroundColor: tagConfig[insight.diversity_tag]?.bg,
                }"
              >
                {{ tagConfig[insight.diversity_tag]?.label }}
              </span>
              <span class="text-xs text-[var(--color-ink-muted)]">{{ insight.sections.join(', ') }}</span>
            </div>

            <h1 class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)] leading-snug mb-3">
              {{ insight.title }}
            </h1>

            <div class="space-y-3">
              <div>
                <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)] mb-1">Почему</h3>
                <p class="text-sm text-[var(--color-ink-light)] leading-relaxed">{{ insight.explanation }}</p>
              </div>
              <div>
                <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)] mb-1">Куда ведёт</h3>
                <p class="text-sm text-[var(--color-ink-light)] leading-relaxed">{{ insight.trajectory }}</p>
              </div>
            </div>
          </div>

          <!-- Related sources -->
          <div class="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-5">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)] mb-3">Источники</h3>
            <div class="space-y-2.5">
              <div
                v-for="s in insight.related_sources"
                :key="s.citekey"
                class="flex items-start gap-2"
              >
                <span class="citekey-link mt-0.5 flex-shrink-0">@{{ s.citekey }}</span>
                <div class="min-w-0">
                  <p class="text-sm text-[var(--color-ink-light)] leading-snug">{{ s.title }}</p>
                  <p class="text-xs text-[var(--color-ink-muted)]">{{ s.year }}</p>
                </div>
              </div>
            </div>
          </div>

          <!-- Pinned hypothesis -->
          <div
            v-if="pinnedHypothesis"
            class="rounded-lg border-2 border-[var(--color-accent)] bg-[var(--color-accent-pale)]/30 p-5"
          >
            <div class="flex items-center gap-2 mb-2">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4 text-[var(--color-accent)]">
                <path fill-rule="evenodd" d="M10.986 3.014a.75.75 0 0 1 0 1.06L8.3 6.763l3.25 3.25L13.3 8.3a.75.75 0 1 1 1.06 1.06l-1.75 1.714a.75.75 0 0 1-1.06 0L8.3 7.823l-3.25 3.25 1.714 1.75a.75.75 0 1 1-1.06 1.06L3.95 12.13a.75.75 0 0 1 0-1.06l3.25-3.25L3.95 4.57a.75.75 0 0 1 0-1.06L5.7 1.76a.75.75 0 0 1 1.06 0L8.3 3.51l3.25-3.25L13.3 2a.75.75 0 0 1-1.06 1.06l-1.254-1.046Z" clip-rule="evenodd" />
              </svg>
              <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--color-accent-deep)]">Зафиксированная гипотеза</h3>
            </div>
            <p class="text-sm text-[var(--color-ink)] leading-relaxed">{{ pinnedHypothesis }}</p>
            <div class="mt-3 flex items-center gap-2">
              <button
                @click="refineHypothesis(pinnedHypothesis!)"
                class="text-xs font-medium text-[var(--color-accent)] hover:underline"
              >
                Уточнить в чате
              </button>
              <span class="text-[var(--color-rule)]">&middot;</span>
              <button
                @click="unpinHypothesis"
                class="text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
              >
                Снять
              </button>
            </div>
          </div>
        </div>

        <!-- Right: Chat (3/5) -->
        <div class="lg:col-span-3 flex flex-col rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden" style="height: calc(100vh - 9rem);">
          <!-- Chat header -->
          <div class="flex items-center gap-2 border-b border-[var(--color-rule)] px-5 py-3 flex-shrink-0">
            <div class="h-2 w-2 rounded-full bg-[var(--color-ok)]"></div>
            <span class="text-sm font-medium text-[var(--color-ink)]">Обсуждение</span>
          </div>

          <!-- Messages -->
          <div ref="chatEl" class="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            <div
              v-for="(msg, i) in messages"
              :key="i"
              class="flex"
              :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
            >
              <div class="max-w-[90%]">
                <div
                  class="rounded-lg px-4 py-3 text-sm leading-relaxed"
                  :class="msg.role === 'user'
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'bg-[var(--color-paper-warm)] text-[var(--color-ink-light)]'"
                >
                  <div class="whitespace-pre-wrap" v-html="formatMarkdown(msg.content)" />
                </div>

                <!-- Hypothesis proposals (inline in assistant messages) -->
                <div v-if="msg.hypotheses && msg.hypotheses.length > 0" class="mt-2 space-y-2">
                  <div
                    v-for="h in msg.hypotheses"
                    :key="h.id"
                    class="group rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-4 py-3 hover:border-[var(--color-accent)]/40 transition-colors cursor-pointer"
                    @click="selectHypothesis(h.text)"
                  >
                    <p class="text-sm text-[var(--color-ink-light)] leading-relaxed" v-html="formatMarkdown(h.text)" />
                    <div class="mt-2 flex items-center gap-3 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        class="text-xs font-medium text-[var(--color-accent)] hover:underline"
                        @click.stop="selectHypothesis(h.text)"
                      >
                        Зафиксировать
                      </button>
                      <button
                        class="text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
                        @click.stop="chatInput = `Уточни гипотезу: ${h.text.slice(0, 80)}...`"
                      >
                        Уточнить
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Typing indicator -->
            <div v-if="chatLoading" class="flex justify-start">
              <div class="rounded-lg bg-[var(--color-paper-warm)] px-4 py-3">
                <div class="flex items-center gap-1.5">
                  <span class="h-1.5 w-1.5 rounded-full bg-[var(--color-ink-muted)] animate-pulse" />
                  <span class="h-1.5 w-1.5 rounded-full bg-[var(--color-ink-muted)] animate-pulse" style="animation-delay: 200ms" />
                  <span class="h-1.5 w-1.5 rounded-full bg-[var(--color-ink-muted)] animate-pulse" style="animation-delay: 400ms" />
                </div>
              </div>
            </div>
          </div>

          <!-- Input -->
          <div class="border-t border-[var(--color-rule)] px-4 py-3 flex-shrink-0">
            <div class="flex items-end gap-2">
              <textarea
                v-model="chatInput"
                @keydown.enter.exact.prevent="sendMessage"
                class="flex-1 rounded-md border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 text-sm text-[var(--color-ink)] placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]/30 resize-none"
                rows="1"
                placeholder="Уточните, предложите свою гипотезу..."
                :disabled="chatLoading"
              />
              <button
                @click="sendMessage"
                :disabled="!chatInput.trim() || chatLoading"
                class="flex h-9 w-9 items-center justify-center rounded-md bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-deep)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0"
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4">
                  <path d="M2.87 2.298a.75.75 0 0 0-.812 1.021L3.39 6.624a1 1 0 0 0 .928.626H8.25a.75.75 0 0 1 0 1.5H4.318a1 1 0 0 0-.927.626l-1.333 3.305a.75.75 0 0 0 .811 1.022l11.502-3.593a.75.75 0 0 0 0-1.42L2.87 2.298Z" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
