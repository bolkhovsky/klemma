<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { research as researchApi, process as processApi, userProjects } from '@/api/client'
import type { OutlineSection } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.projectId as string)
const section = computed(() => route.params.section as string)

interface ArgumentBlock {
  order: number
  title: string
  description: string
  citations: string[]
  estimated_words: number
}

interface CitationEntry {
  citekey: string
  fragment_text: string
  usage: string
  position: string
  relevance: number
}

interface ReportData {
  section_title: string
  section_status: string
  readiness_pct: number
  current_word_count: number
  target_word_count: number
  available_sources: number
  available_fragments: number
  fragment_distribution: Record<string, number>
  argument_blocks: ArgumentBlock[]
  citation_plan: CitationEntry[]
  missing_coverage: string[]
  writing_suggestions: string[]
}

const reportData = ref<ReportData | null>(null)
const reportText = ref('')
const reportMeta = ref<{ created_at: string; model: string }>({ created_at: '', model: '' })
const loading = ref(true)
const error = ref('')
const copied = ref(false)
const showRawText = ref(false)

// Outline
const outline = ref<OutlineSection[]>([])
const sectionName = computed(() => {
  const entry = outline.value.find(s => s.id === section.value)
  if (entry) return entry.name
  return reportData.value?.section_title || ''
})

// Regeneration
const regenerating = ref(false)
const regenJobId = ref<string | null>(null)
const regenStatus = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// Group citation plan entries by argument block (match via citekey in block.citations)
function citationsForBlock(block: ArgumentBlock): CitationEntry[] {
  if (!reportData.value || !block.citations.length) return []
  const ckSet = new Set(block.citations)
  return reportData.value.citation_plan.filter(c => ckSet.has(c.citekey))
}

// Citations not assigned to any block (orphans)
const unassignedCitations = computed(() => {
  if (!reportData.value) return []
  const allBlockCks = new Set(reportData.value.argument_blocks.flatMap(b => b.citations))
  return reportData.value.citation_plan.filter(c => !allBlockCks.has(c.citekey))
})

const usageLabel: Record<string, string> = {
  evidence: 'доказательство',
  method: 'метод',
  comparison: 'сравнение',
  definition: 'определение',
  quote: 'цитата',
  background: 'фон',
  result_comparison: 'результат',
  extends: 'расширяет',
  contrasts: 'контраст',
  uses_data: 'данные',
}

const usageColor: Record<string, string> = {
  evidence: 'bg-green-100 text-green-800',
  method: 'bg-purple-100 text-purple-800',
  comparison: 'bg-orange-100 text-orange-800',
  definition: 'bg-blue-100 text-blue-800',
  quote: 'bg-yellow-100 text-yellow-800',
  background: 'bg-gray-100 text-gray-700',
  result_comparison: 'bg-green-100 text-green-800',
  extends: 'bg-teal-100 text-teal-800',
  contrasts: 'bg-red-100 text-red-800',
  uses_data: 'bg-cyan-100 text-cyan-800',
}

// Actionable recommendations: max 2, with links
const actionableRecs = computed(() => {
  if (!reportData.value) return []
  const recs: { text: string; link: string; linkText: string }[] = []

  // Missing coverage → library
  if (reportData.value.missing_coverage.length > 0) {
    const topics = reportData.value.missing_coverage.slice(0, 2).join('; ')
    recs.push({
      text: `Не хватает источников: ${topics}`,
      link: `/${projectId.value}/library`,
      linkText: 'Перейти в библиотеку',
    })
  }

  // Writing suggestions → pick first actionable one
  for (const s of reportData.value.writing_suggestions) {
    if (recs.length >= 2) break
    recs.push({ text: s, link: '', linkText: '' })
  }

  return recs.slice(0, 2)
})

async function loadOutline() {
  if (projectStore.activeOutline?.length) {
    outline.value = projectStore.activeOutline
  } else {
    try {
      const data = await userProjects.list()
      const p = data.projects.find(pr => pr.project_id === projectId.value)
      outline.value = p?.outline ?? []
    } catch { /* ignore */ }
  }
}

watch(() => projectStore.activeOutline, (v) => {
  if (v?.length) outline.value = v
})

async function loadReport() {
  loading.value = true
  error.value = ''
  try {
    const data = await researchApi.getReport(projectId.value, section.value)
    reportText.value = data.report_text
    reportMeta.value = { created_at: data.created_at, model: data.model }
    reportData.value = (data as any).report_data ?? null
  } catch {
    error.value = 'Обзор не найден'
  } finally {
    loading.value = false
  }
}

async function regenerate() {
  if (!confirm('Обновить обзор? Будут списаны токены за генерацию.')) return
  regenerating.value = true
  regenStatus.value = 'queued'
  error.value = ''
  try {
    const resp = await researchApi.generate(section.value, projectId.value)
    regenJobId.value = resp.job_id
    startPolling()
  } catch (e: any) {
    error.value = e.message || 'Ошибка запуска'
    regenerating.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!regenJobId.value) return
    try {
      const resp = await processApi.jobStatus(regenJobId.value)
      regenStatus.value = resp.status
      if (resp.status === 'finished') {
        stopPolling()
        regenerating.value = false
        regenJobId.value = null
        if (resp.result?.status === 'error') {
          error.value = resp.result.detail || 'Генерация завершилась с ошибкой'
        } else {
          await loadReport()
        }
      } else if (resp.status === 'failed') {
        stopPolling()
        regenerating.value = false
        regenJobId.value = null
        error.value = resp.result?.detail || 'Генерация завершилась с ошибкой'
      }
    } catch { /* keep polling */ }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function copyToClipboard() {
  if (!reportText.value) return
  try { navigator.clipboard.writeText(reportText.value) } catch {
    const ta = document.createElement('textarea')
    ta.value = reportText.value
    ta.style.cssText = 'position:fixed;opacity:0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

function formatDate(iso: string) {
  try { return new Date(iso + 'Z').toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }) }
  catch { return iso }
}

// Reload on route param change (component reuse)
watch(() => route.params.section, () => { stopPolling(); loadReport() })

onMounted(() => { loadOutline(); loadReport() })
onUnmounted(stopPolling)
</script>

<template>
  <AppLayout>
    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-24">
      <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
    </div>

    <!-- Error -->
    <div v-else-if="error && !reportData && !reportText" class="py-12 text-center">
      <p class="text-sm text-[var(--color-err)]">{{ error }}</p>
      <RouterLink :to="`/${projectId}/research`" class="mt-4 inline-block text-sm text-[var(--color-accent)]">
        &larr; Назад к исследованиям
      </RouterLink>
    </div>

    <!-- Report -->
    <div v-else class="space-y-8">
      <!-- Breadcrumb -->
      <div class="animate-in">
        <RouterLink :to="`/${projectId}/research`" class="text-sm text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] transition-colors">
          &larr; Исследование
        </RouterLink>
      </div>

      <!-- Header -->
      <div class="animate-in animate-in-delay-1">
        <div class="flex items-start justify-between gap-6">
          <div>
            <h1 class="font-[var(--font-display)] text-2xl font-bold text-[var(--color-ink)] tracking-tight">
              {{ section }}
              <template v-if="sectionName">
                <span class="mx-2 text-[var(--color-rule)]">&middot;</span>
                {{ sectionName }}
              </template>
            </h1>
            <!-- Facts, not percentages -->
            <div v-if="reportData" class="mt-2 flex items-center gap-4 text-sm text-[var(--color-ink-muted)]">
              <span>{{ reportData.available_sources }} источников</span>
              <span class="text-[var(--color-rule)]">&middot;</span>
              <span>{{ reportData.available_fragments }} фрагментов</span>
              <span class="text-[var(--color-rule)]">&middot;</span>
              <span>{{ reportData.argument_blocks.length }} блоков аргументации</span>
            </div>
            <p class="mt-1 text-sm text-[var(--color-ink-muted)]">
              <span v-if="reportMeta.created_at">{{ formatDate(reportMeta.created_at) }}</span>
              <span v-if="reportMeta.model"> &middot; {{ reportMeta.model }}</span>
            </p>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <button
              @click="showRawText = !showRawText"
              class="rounded-lg border border-[var(--color-rule)] px-3 py-2 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)] transition-colors"
            >
              {{ showRawText ? 'Карточки' : 'Текст' }}
            </button>
            <button
              @click="copyToClipboard"
              class="rounded-lg border border-[var(--color-rule)] px-3 py-2 text-sm transition-colors"
              :class="copied ? 'text-[var(--color-ok)] border-[var(--color-ok)]' : 'text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)]'"
            >
              {{ copied ? 'Скопировано' : 'Копировать' }}
            </button>
            <button
              @click="regenerate"
              :disabled="regenerating"
              class="rounded-lg border border-[var(--color-rule)] px-4 py-2 text-sm font-medium text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)] disabled:opacity-50 transition-colors"
            >
              {{ regenerating ? 'Генерация...' : 'Обновить' }}
            </button>
          </div>
        </div>
      </div>

      <!-- Regeneration progress -->
      <div v-if="regenerating" class="animate-in rounded-xl border border-[var(--color-accent)] bg-[var(--color-accent-pale)] p-4">
        <div class="flex items-center gap-3">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent"></div>
          <span class="text-sm font-medium text-[var(--color-accent-deep)]">Обновляем обзор...</span>
          <span class="text-sm text-[var(--color-ink-muted)]">{{ regenStatus }}</span>
        </div>
      </div>

      <!-- Error banner -->
      <div v-if="error" class="rounded-xl border border-[var(--color-err)] bg-[var(--color-err-bg)] p-4">
        <p class="text-sm text-[var(--color-err)]">{{ error }}</p>
      </div>

      <!-- RAW TEXT VIEW -->
      <div v-if="showRawText" class="animate-in rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-8">
        <pre class="text-sm text-[var(--color-ink-light)] leading-relaxed whitespace-pre-wrap font-[var(--font-body)]">{{ reportText }}</pre>
      </div>

      <!-- STRUCTURED VIEW -->
      <template v-else-if="reportData">
        <!-- Fragment type tags -->
        <div v-if="Object.keys(reportData.fragment_distribution).length > 0" class="animate-in animate-in-delay-1 flex flex-wrap gap-2">
          <span
            v-for="(count, type) in reportData.fragment_distribution"
            :key="type"
            class="rounded-full px-3 py-1 text-sm font-medium bg-[var(--color-rule-light)] text-[var(--color-ink)]"
          >
            {{ type }}: {{ count }}
          </span>
        </div>

        <!-- Argument blocks with inline citations -->
        <div v-if="reportData.argument_blocks.length > 0" class="animate-in animate-in-delay-1 space-y-4">
          <h2 class="font-[var(--font-display)] text-base font-semibold text-[var(--color-ink)] uppercase tracking-wider">
            Структура аргументации
          </h2>
          <div class="space-y-4">
            <div
              v-for="block in reportData.argument_blocks"
              :key="block.order"
              class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] overflow-hidden"
            >
              <!-- Block header -->
              <div class="p-6">
                <div class="flex items-start gap-4">
                  <span class="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--color-accent)] text-white text-sm font-bold shrink-0">
                    {{ block.order }}
                  </span>
                  <div class="flex-1 min-w-0">
                    <h3 class="text-base font-semibold text-[var(--color-ink)]">{{ block.title }}</h3>
                    <p class="mt-2 text-sm text-[var(--color-ink-light)] leading-relaxed">{{ block.description }}</p>
                  </div>
                </div>
              </div>

              <!-- Inline citations for this block -->
              <div
                v-if="citationsForBlock(block).length > 0"
                class="border-t border-[var(--color-rule-light)] bg-[var(--color-paper-warm)] divide-y divide-[var(--color-rule-light)]"
              >
                <div
                  v-for="(cite, ci) in citationsForBlock(block)"
                  :key="ci"
                  class="px-6 py-3.5"
                >
                  <p class="text-sm text-[var(--color-ink)] leading-relaxed italic">
                    "{{ cite.fragment_text.slice(0, 200) }}{{ cite.fragment_text.length > 200 ? '...' : '' }}"
                  </p>
                  <div class="mt-2 flex items-center gap-3">
                    <RouterLink
                      :to="`/${projectId}/library/${cite.citekey}`"
                      class="font-[var(--font-mono)] text-sm font-medium text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] hover:underline transition-colors"
                    >@{{ cite.citekey }}</RouterLink>
                    <span
                      v-if="cite.usage"
                      class="rounded-full px-2.5 py-0.5 text-sm font-medium"
                      :class="usageColor[cite.usage] || 'bg-gray-100 text-gray-700'"
                    >
                      {{ usageLabel[cite.usage] || cite.usage }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Unassigned citations (not linked to any argument block) -->
        <div v-if="unassignedCitations.length > 0" class="animate-in animate-in-delay-2 space-y-4">
          <h2 class="font-[var(--font-display)] text-base font-semibold text-[var(--color-ink)] uppercase tracking-wider">
            Дополнительные цитаты
          </h2>
          <div class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] divide-y divide-[var(--color-rule-light)]">
            <div
              v-for="(cite, i) in unassignedCitations"
              :key="i"
              class="px-6 py-4"
            >
              <p class="text-sm text-[var(--color-ink)] leading-relaxed italic">
                "{{ cite.fragment_text.slice(0, 200) }}{{ cite.fragment_text.length > 200 ? '...' : '' }}"
              </p>
              <div class="mt-2 flex items-center gap-3">
                <RouterLink
                  :to="`/${projectId}/library/${cite.citekey}`"
                  class="font-[var(--font-mono)] text-sm font-medium text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] hover:underline transition-colors"
                >@{{ cite.citekey }}</RouterLink>
                <span
                  v-if="cite.usage"
                  class="rounded-full px-2.5 py-0.5 text-sm font-medium"
                  :class="usageColor[cite.usage] || 'bg-gray-100 text-gray-700'"
                >
                  {{ usageLabel[cite.usage] || cite.usage }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- Actionable recommendations (max 2) -->
        <div v-if="actionableRecs.length > 0" class="animate-in animate-in-delay-3 space-y-3">
          <h2 class="font-[var(--font-display)] text-base font-semibold text-[var(--color-ink)] uppercase tracking-wider">
            Рекомендации
          </h2>
          <div class="space-y-2">
            <div
              v-for="(rec, i) in actionableRecs"
              :key="i"
              class="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-6 py-4 flex items-center justify-between gap-4"
            >
              <p class="text-sm text-[var(--color-ink)]">{{ rec.text }}</p>
              <RouterLink
                v-if="rec.link"
                :to="rec.link"
                class="shrink-0 text-sm font-medium text-[var(--color-accent)] hover:text-[var(--color-accent-deep)] transition-colors"
              >
                {{ rec.linkText }} &rarr;
              </RouterLink>
            </div>
          </div>
        </div>
      </template>

      <!-- Fallback: raw text if no structured data -->
      <div v-else-if="reportText" class="animate-in rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-white)] p-8">
        <pre class="text-sm text-[var(--color-ink-light)] leading-relaxed whitespace-pre-wrap font-[var(--font-body)]">{{ reportText }}</pre>
      </div>
    </div>
  </AppLayout>
</template>
