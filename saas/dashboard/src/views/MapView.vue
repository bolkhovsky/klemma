<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { userProjects, projects as apiProjects, drafts, type DraftHeading } from '@/api/client'

const router = useRouter()
const route = useRoute()

const projectId = computed(() => route.params.projectId as string | undefined)
const isDemoMode = computed(() => !route.params.projectId)

// ── Types ────────────────────────────────────────────────────────────────

interface ArgumentBlock {
  id: string
  title: string
  sources: string[]
  wordTarget: number
  status: 'draft' | 'outlined' | 'empty'
}

interface Section {
  id: string
  name: string
  thesis?: string
  blocks: ArgumentBlock[]
  sourceCount: number
  fragmentCount: number
  insightCount: number
}

interface Chapter {
  number: number
  name: string
  thesis: string
  task: string
  sections: Section[]
}

interface WorkThesis {
  title: string
  goal: string
  nr1: string
  nr2: string
}

// ── Demo mock data ────────────────────────────────────────────────────────

const DEMO_THESIS: WorkThesis = {
  title: 'Геоинформационная методика валидации нейросетевых прогнозов ледовой обстановки',
  goal: 'Разработка методики оценки качества нейросетевых прогнозов для навигационно-гидрографического обеспечения арктического судоходства',
  nr1: 'Геоинформационная модель валидации прогнозов',
  nr2: 'Геоинформационная методика валидации прогнозов ледовой обстановки',
}

const DEMO_CHAPTERS: Chapter[] = [
  {
    number: 1,
    name: 'Анализ предметной области',
    thesis: 'Существующие системы НГО не обеспечивают достаточную точность прогнозов ледовой обстановки для безопасного круглогодичного судоходства по СМП',
    task: 'T1: Проанализировать системы НГО в Арктике и специфику прогнозирования',
    sections: [
      {
        id: '1.1', name: 'Потребности пользователей НГГМИ',
        thesis: 'Рост грузопотока по СМП создаёт потребность в оперативных ледовых прогнозах',
        blocks: [
          { id: 'b1.1.1', title: 'Стратегическое значение СМП и рост грузопотока', sources: ['zhuravelSevernyyMorckoyPut2020', 'kuvatovPotencialSevernogoMorskogo2014', 'Angudovich2025'], wordTarget: 350, status: 'draft' },
          { id: 'b1.1.2', title: 'Потребности навигации в ледовой информации', sources: ['alekseevaVliyanieIntensivnogoSudohodstva2024', 'butakovRezultatyPrognozaGidrometeorologicheskih2025'], wordTarget: 350, status: 'draft' },
          { id: 'b1.1.3', title: 'Ледовые условия АЗРФ и их изменчивость', sources: ['trofimovIzmenchivostTrendyLedovitosti2024', 'kirillovVozmozhnostiIssledovaniyaVozrastnyh2023'], wordTarget: 300, status: 'draft' },
          { id: 'b1.1.4', title: 'Ледовые карты как инструмент навигации', sources: ['arcticandantarcticresearchinstituteUSINGNEURALNETWORK2024', 'stokholmAutoICEChallenge2024'], wordTarget: 350, status: 'outlined' },
          { id: 'b1.1.5', title: 'Спутниковое ДЗЗ и информационные пробелы', sources: ['zabolotskihSputnikovoeMikrovolnovoeZondirovanie2023', 'bidenkoGeoinformacionnayaProceduraOcenki2022'], wordTarget: 300, status: 'outlined' },
        ],
        sourceCount: 23, fragmentCount: 40, insightCount: 0,
      },
      {
        id: '1.2', name: 'Источники НГГМИ в АЗРФ',
        thesis: 'Данные наблюдений фрагментированы между ведомствами и недостаточны для валидации',
        blocks: [
          { id: 'b1.2.1', title: 'Морские гидрометеорологические станции и их покрытие', sources: ['smirnovMonitoringFizikomehanicheskogoSostoyaniya2020', 'abuzVVEDENIE'], wordTarget: 300, status: 'outlined' },
          { id: 'b1.2.2', title: 'Автоматические буйковые станции и дрифтеры', sources: ['kirillovVozmozhnostiIssledovaniyaVozrastnyh2023'], wordTarget: 250, status: 'empty' },
          { id: 'b1.2.3', title: 'Зависимость предсказуемости от сезона инициализации', sources: ['tietsche2014', 'day2014', 'collow2015'], wordTarget: 350, status: 'empty' },
        ],
        sourceCount: 18, fragmentCount: 31, insightCount: 1,
      },
      {
        id: '1.3', name: 'Данные ДЗЗ и ИНС',
        thesis: 'Спутниковое ДЗЗ — единственный источник регулярного покрытия Арктики',
        blocks: [
          { id: 'b1.3.1', title: 'Пассивная микроволновая радиометрия (AMSR2, SSM/I)', sources: ['zabolotskihSputnikovoeMikrovolnovoeZondirovanie2023'], wordTarget: 350, status: 'draft' },
          { id: 'b1.3.2', title: 'Радарная съёмка (SAR) и проблема отечественных спутников', sources: ['Tsepelev2023', 'bidenkoGeoinformacionnayaProceduraOcenki2022'], wordTarget: 300, status: 'outlined' },
          { id: 'b1.3.3', title: 'ИНС для тематической интерпретации спутниковых данных', sources: ['arcticandantarcticresearchinstituteUSINGNEURALNETWORK2024'], wordTarget: 250, status: 'empty' },
        ],
        sourceCount: 15, fragmentCount: 22, insightCount: 1,
      },
      {
        id: '1.4', name: 'Методы и технологии НГГМИ',
        thesis: 'Существующие методы либо точны на коротких горизонтах, либо ресурсоёмки',
        blocks: [
          { id: 'b1.4.1', title: 'Физические численные модели (CICE, NEMO-LIM)', sources: ['chevallier2013', 'massonnet2012'], wordTarget: 350, status: 'outlined' },
          { id: 'b1.4.2', title: 'Статистические методы и ML-подходы', sources: ['stokholmAutoICEChallenge2024'], wordTarget: 300, status: 'outlined' },
          { id: 'b1.4.3', title: 'Нейросетевые архитектуры: LSTM, U-Net, ConvLSTM', sources: ['arcticandantarcticresearchinstituteUSINGNEURALNETWORK2024'], wordTarget: 350, status: 'empty' },
          { id: 'b1.4.4', title: 'Сравнительный анализ горизонтов и ресурсов', sources: ['collow2015', 'tietsche2014'], wordTarget: 300, status: 'empty' },
        ],
        sourceCount: 12, fragmentCount: 19, insightCount: 1,
      },
    ],
  },
  {
    number: 2,
    name: 'Геоинформационная модель',
    thesis: 'Модель валидации должна объединять компоненты временного анализа (LSTM), пространственной сегментации (U-Net) и пространственно-временной динамики (ConvLSTM)',
    task: 'T2-T3: Исследовать факторы геосреды и обосновать выбор нейросетевой модели',
    sections: [
      {
        id: '2.1', name: 'Концептуальная модель',
        thesis: 'Валидация нейросетевых прогнозов требует многокомпонентной модели',
        blocks: [
          { id: 'b2.1.1', title: 'Обоснование многокомпонентного подхода', sources: ['bidenkoGeoinformacionnayaProceduraOcenki2022'], wordTarget: 400, status: 'draft' },
          { id: 'b2.1.2', title: 'Архитектура модели: входы, обработка, выходы', sources: ['bidenkoGeoinformacionnayaProceduraOcenki2022'], wordTarget: 350, status: 'outlined' },
        ],
        sourceCount: 8, fragmentCount: 14, insightCount: 0,
      },
      {
        id: '2.2', name: 'Состав модели',
        thesis: undefined,
        blocks: [
          { id: 'b2.2.1', title: 'Компонент временного анализа (LSTM)', sources: ['arcticandantarcticresearchinstituteUSINGNEURALNETWORK2024'], wordTarget: 300, status: 'outlined' },
          { id: 'b2.2.2', title: 'Компонент пространственной сегментации (U-Net)', sources: ['stokholmAutoICEChallenge2024'], wordTarget: 300, status: 'empty' },
          { id: 'b2.2.3', title: 'Компонент пространственно-временной динамики (ConvLSTM)', sources: [], wordTarget: 300, status: 'empty' },
        ],
        sourceCount: 6, fragmentCount: 10, insightCount: 0,
      },
      {
        id: '2.3', name: 'Содержание модели',
        thesis: undefined,
        blocks: [
          { id: 'b2.3.1', title: 'Требования к данным наблюдений ДЗЗ', sources: ['zabolotskihSputnikovoeMikrovolnovoeZondirovanie2023', 'Tsepelev2023'], wordTarget: 350, status: 'draft' },
          { id: 'b2.3.2', title: 'Предобработка и нормализация', sources: [], wordTarget: 250, status: 'empty' },
          { id: 'b2.3.3', title: 'Функции потерь и критерии обучения', sources: [], wordTarget: 300, status: 'empty' },
        ],
        sourceCount: 11, fragmentCount: 18, insightCount: 0,
      },
    ],
  },
  {
    number: 3,
    name: 'Методика валидации',
    thesis: 'Специализированные метрики (IIEE, SPS) превосходят стандартные (RMSE, MAE) для пространственных прогнозов льда',
    task: 'T4: Разработать методику оценки качества прогнозов',
    sections: [
      {
        id: '3.1', name: 'Информационное наполнение',
        thesis: 'Качество валидации определяется качеством входных данных — нужна формализованная процедура',
        blocks: [
          { id: 'b3.1.1', title: 'Источники данных для валидации', sources: ['zabolotskihSputnikovoeMikrovolnovoeZondirovanie2023'], wordTarget: 300, status: 'outlined' },
          { id: 'b3.1.2', title: 'Процедура контроля качества входных данных', sources: ['bidenkoGeoinformacionnayaProceduraOcenki2022'], wordTarget: 350, status: 'empty' },
        ],
        sourceCount: 5, fragmentCount: 8, insightCount: 0,
      },
      {
        id: '3.2', name: 'Методы верификации прогнозов',
        thesis: 'IIEE декомпозирует ошибку на miss, false alarm и displacement — это критично для навигации',
        blocks: [
          { id: 'b3.2.1', title: 'Стандартные метрики: RMSE, MAE, R²', sources: ['butakovRezultatyPrognozaGidrometeorologicheskih2025'], wordTarget: 300, status: 'draft' },
          { id: 'b3.2.2', title: 'IIEE: декомпозиция на AEE и ME', sources: ['goessling2016'], wordTarget: 400, status: 'draft' },
          { id: 'b3.2.3', title: 'Spatial Probability Score (SPS)', sources: ['dukhovskoy2015'], wordTarget: 300, status: 'empty' },
          { id: 'b3.2.4', title: 'Сравнительный анализ метрик для навигационных задач', sources: ['goessling2016', 'dukhovskoy2015'], wordTarget: 350, status: 'empty' },
          { id: 'b3.2.5', title: 'Стратификация по условиям инициализации', sources: ['collow2015', 'chevallier2013', 'tietsche2014'], wordTarget: 350, status: 'empty' },
        ],
        sourceCount: 14, fragmentCount: 25, insightCount: 2,
      },
      {
        id: '3.3', name: 'Алгоритм оценки качества',
        thesis: 'Алгоритм должен комбинировать метрики с учётом навигационного контекста',
        blocks: [
          { id: 'b3.3.1', title: 'Взвешенная комбинация метрик', sources: [], wordTarget: 300, status: 'empty' },
          { id: 'b3.3.2', title: 'Пороги приемлемости для навигации', sources: [], wordTarget: 250, status: 'empty' },
        ],
        sourceCount: 3, fragmentCount: 5, insightCount: 1,
      },
    ],
  },
  {
    number: 4,
    name: 'Программная реализация',
    thesis: 'Модуль валидации реализуем как воспроизводимый Python-пакет с открытыми данными',
    task: 'T5-T6: Разработать программный модуль и провести валидацию',
    sections: [
      {
        id: '4.1', name: 'Постановка задачи',
        thesis: 'Программная реализация должна обеспечить воспроизводимость результатов',
        blocks: [
          { id: 'b4.1.1', title: 'Функциональные требования к модулю', sources: ['bidenkoGeoinformacionnayaProceduraOcenki2022'], wordTarget: 300, status: 'draft' },
          { id: 'b4.1.2', title: 'Нефункциональные требования: воспроизводимость, производительность', sources: [], wordTarget: 250, status: 'outlined' },
        ],
        sourceCount: 4, fragmentCount: 7, insightCount: 0,
      },
      {
        id: '4.2', name: 'Алгоритм валидации',
        thesis: undefined,
        blocks: [
          { id: 'b4.2.1', title: 'Пошаговый алгоритм валидации', sources: [], wordTarget: 400, status: 'outlined' },
          { id: 'b4.2.2', title: 'Обработка граничных случаев: пропуски данных, полярная ночь', sources: [], wordTarget: 300, status: 'empty' },
        ],
        sourceCount: 6, fragmentCount: 11, insightCount: 0,
      },
      {
        id: '4.3', name: 'Программная реализация',
        thesis: undefined,
        blocks: [
          { id: 'b4.3.1', title: 'Архитектура Python-модуля', sources: [], wordTarget: 350, status: 'draft' },
          { id: 'b4.3.2', title: 'Формат входных/выходных данных (NetCDF, GeoTIFF)', sources: [], wordTarget: 250, status: 'outlined' },
          { id: 'b4.3.3', title: 'Визуализация результатов (карты, графики)', sources: [], wordTarget: 300, status: 'empty' },
        ],
        sourceCount: 5, fragmentCount: 9, insightCount: 0,
      },
      {
        id: '4.4', name: 'Технологический суверенитет',
        thesis: 'Модуль должен работать на отечественной инфраструктуре без зависимости от иностранных сервисов',
        blocks: [
          { id: 'b4.4.1', title: 'Импортозамещение в ПО для обработки ДЗЗ', sources: ['Tsepelev2023'], wordTarget: 300, status: 'empty' },
          { id: 'b4.4.2', title: 'Связь с работами кафедры РГГМУ', sources: [], wordTarget: 250, status: 'empty' },
        ],
        sourceCount: 3, fragmentCount: 4, insightCount: 1,
      },
    ],
  },
]

// ── Real API state ────────────────────────────────────────────────────────

const loading = ref(false)
const error = ref<string | null>(null)
const projectName = ref('')
const draftHeadings = ref<DraftHeading[]>([])
const draftHasContent = ref(false)
const sectionSourceCounts = ref<Record<string, number>>({})

async function loadMapData() {
  if (isDemoMode.value) return
  loading.value = true
  error.value = null
  try {
    const [projectsData, coverageResult, draftResult] = await Promise.all([
      userProjects.list(),
      apiProjects.coverage().catch(() => ({ total_sources: 0, sections: {} as Record<string, number>, chapters: {} as Record<string, number> })),
      drafts.list(projectId.value!).catch(() => ({ files: [] as { name: string; headings: DraftHeading[]; word_count: number }[] })),
    ])
    const project = projectsData.projects.find(p => p.project_id === projectId.value)
    if (project) projectName.value = project.name
    sectionSourceCounts.value = coverageResult.sections
    const file = draftResult.files[0]
    draftHeadings.value = file?.headings ?? []
    draftHasContent.value = (file?.word_count ?? 0) > 0
  } catch (e: any) {
    error.value = (e as Error).message ?? 'Ошибка загрузки'
  } finally {
    loading.value = false
  }
}

/** Draft status: has any content in the file at all. Per-section tracking deferred. */
function sectionDraftStatus(_sectionId: string): 'draft' | 'empty' {
  return draftHasContent.value ? 'draft' : 'empty'
}

onMounted(loadMapData)

// Build chapters from draft headings (level-2 = chapter, level-3+ = sections under that chapter)
const realChapters = computed((): Chapter[] => {
  if (!draftHeadings.value.length) return []

  // Separate chapter-level (no dot) from section-level (has dot)
  const chapterHeadings = draftHeadings.value.filter(h => !h.section_id.includes('.'))
  const sectionMap = new Map<string, DraftHeading[]>()
  for (const h of draftHeadings.value) {
    if (!h.section_id.includes('.')) continue
    const chKey = h.section_id.split('.')[0]!
    if (!sectionMap.has(chKey)) sectionMap.set(chKey, [])
    sectionMap.get(chKey)!.push(h)
  }

  return chapterHeadings
    .sort((a, b) => parseInt(a.section_id) - parseInt(b.section_id))
    .map(ch => ({
      number: parseInt(ch.section_id),
      name: ch.full_title,
      thesis: '',
      task: '',
      sections: (sectionMap.get(ch.section_id) ?? []).map(s => ({
        id: s.section_id,
        name: s.full_title.replace(/^\d[\d.]*\s*/, ''),
        thesis: undefined,
        blocks: [],
        sourceCount: sectionSourceCounts.value[s.section_id] ?? 0,
        fragmentCount: 0,
        insightCount: 0,
      })),
    }))
})

// Active data (demo or real)
const chapters = computed(() => isDemoMode.value ? DEMO_CHAPTERS : realChapters.value)
const thesis = computed(() =>
  isDemoMode.value
    ? DEMO_THESIS
    : { title: projectName.value, goal: '', nr1: '', nr2: '' }
)

// ── Navigation ────────────────────────────────────────────────────────────

function navigateToBlock(sectionId: string, blockId: string) {
  if (isDemoMode.value) {
    router.push(`/demo/map/${sectionId}/${blockId}`)
  } else {
    router.push(`/${projectId.value}/map/${sectionId}/b1`)
  }
}

function navigateToSection(sectionId: string) {
  if (isDemoMode.value) return // demo uses toggleSection
  router.push(`/${projectId.value}/map/${sectionId}/b1`)
}

// ── State ────────────────────────────────────────────────────────────────

const expandedChapter = ref<number | null>(1)
const expandedSection = ref<string | null>('1.1')

function toggleChapter(n: number) {
  expandedChapter.value = expandedChapter.value === n ? null : n
  expandedSection.value = null
}

function toggleSection(id: string) {
  if (!isDemoMode.value) {
    navigateToSection(id)
    return
  }
  expandedSection.value = expandedSection.value === id ? null : id
}

// ── Computed ─────────────────────────────────────────────────────────────

const totalInsights = computed(() =>
  isDemoMode.value
    ? DEMO_CHAPTERS.reduce((acc, ch) =>
        acc + ch.sections.reduce((a, s) => a + s.insightCount, 0), 0)
    : 0
)

function sectionStrength(s: Section): 'strong' | 'moderate' | 'weak' {
  if (isDemoMode.value) {
    if (s.sourceCount >= 10 && s.fragmentCount >= 15) return 'strong'
    if (s.sourceCount >= 5) return 'moderate'
    return 'weak'
  }
  if (s.sourceCount >= 10) return 'strong'
  if (s.sourceCount >= 5) return 'moderate'
  return 'weak'
}

const strengthColors = {
  strong: { bg: 'bg-[var(--color-ok)]', text: 'text-[var(--color-ok)]', bar: 'var(--color-ok)' },
  moderate: { bg: 'bg-[var(--color-amber)]', text: 'text-[var(--color-amber)]', bar: 'var(--color-amber)' },
  weak: { bg: 'bg-[var(--color-err)]', text: 'text-[var(--color-err)]', bar: 'var(--color-err)' },
}

function blockStatusIcon(status: string): string {
  if (status === 'draft') return '●'
  if (status === 'outlined') return '◐'
  return '○'
}

function blockStatusColor(status: string): string {
  if (status === 'draft') return 'text-[var(--color-ok)]'
  if (status === 'outlined') return 'text-[var(--color-amber)]'
  return 'text-[var(--color-ink-muted)]'
}
</script>

<template>
  <AppLayout>
    <div class="max-w-4xl mx-auto">

      <!-- Loading -->
      <div v-if="loading" class="flex items-center justify-center py-16">
        <div class="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent" />
      </div>

      <!-- Error -->
      <div v-else-if="error" class="mb-6 rounded-md bg-[var(--color-err-pale)] px-4 py-3 text-sm text-[var(--color-err)]">
        {{ error }}
      </div>

      <!-- Content -->
      <template v-else>
        <!-- Thesis: the A→Z view -->
        <div class="mb-8 rounded-lg border border-[var(--color-accent)]/20 bg-[var(--color-accent-pale)]/30 p-6">
          <div class="flex items-start gap-4">
            <div class="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-accent)] text-white font-[var(--font-display)] text-sm font-bold flex-shrink-0">
              А
            </div>
            <div class="flex-1 min-w-0">
              <h1 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)] leading-snug">
                {{ thesis.title || '—' }}
              </h1>
              <p v-if="thesis.goal" class="mt-1 text-sm text-[var(--color-ink-light)] leading-relaxed">
                {{ thesis.goal }}
              </p>
              <div v-if="thesis.nr1 || thesis.nr2" class="mt-3 flex items-center gap-4">
                <div v-if="thesis.nr1" class="flex items-center gap-1.5">
                  <span class="text-xs font-semibold text-[var(--color-accent)]">NR1</span>
                  <span class="text-xs text-[var(--color-ink-muted)]">{{ thesis.nr1 }}</span>
                </div>
                <div v-if="thesis.nr2" class="flex items-center gap-1.5">
                  <span class="text-xs font-semibold text-[var(--color-violet)]">NR2</span>
                  <span class="text-xs text-[var(--color-ink-muted)]">{{ thesis.nr2 }}</span>
                </div>
              </div>
            </div>
            <div class="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-accent)] text-white font-[var(--font-display)] text-sm font-bold flex-shrink-0">
              Я
            </div>
          </div>

          <!-- Route line -->
          <div class="mt-4 flex items-center gap-1">
            <div
              v-for="ch in chapters"
              :key="ch.number"
              class="flex-1 h-1.5 rounded-full transition-colors"
              :class="expandedChapter === ch.number ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-rule)]'"
              :title="`Глава ${ch.number}`"
            />
          </div>
        </div>

        <!-- No draft file yet (real mode) -->
        <div
          v-if="!isDemoMode && chapters.length === 0"
          class="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-white)] px-6 py-10 text-center"
        >
          <p class="text-sm text-[var(--color-ink-muted)]">Структура черновика не задана</p>
          <p class="mt-1 text-xs text-[var(--color-ink-muted)]">
            Задайте структуру в настройках проекта — черновик создастся автоматически
          </p>
          <button
            class="mt-4 text-sm font-medium text-[var(--color-accent)] hover:underline"
            @click="router.push(`/${projectId}/outline`)"
          >
            Настроить структуру →
          </button>
        </div>

        <!-- Pending insights banner (demo only) -->
        <div v-if="totalInsights > 0" class="mb-6 flex items-center gap-2 rounded-md bg-[var(--color-amber-pale)] px-4 py-2.5">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4 text-[var(--color-amber)]">
            <path fill-rule="evenodd" d="M8 1a.75.75 0 0 1 .75.75V5h3.5a.75.75 0 0 1 .6 1.2L9.5 10.5h2.75a.75.75 0 0 1 .6 1.2l-4.5 6a.75.75 0 0 1-1.35-.45V13H4.25a.75.75 0 0 1-.6-1.2L7 7.5H4.25a.75.75 0 0 1-.6-1.2l3.75-5A.75.75 0 0 1 8 1Z" clip-rule="evenodd" />
          </svg>
          <span class="text-sm font-medium text-[var(--color-amber-deep)]">
            {{ totalInsights }} открыт{{ totalInsights === 1 ? 'ие' : totalInsights < 5 ? 'ия' : 'ий' }} на карте
          </span>
          <button class="ml-auto text-sm font-medium text-[var(--color-accent)] hover:underline">
            Показать
          </button>
        </div>

        <!-- Chapters (zoom level 1) -->
        <div class="space-y-3">
          <div
            v-for="ch in chapters"
            :key="ch.number"
            class="rounded-lg border bg-[var(--color-paper-white)] transition-all duration-200"
            :class="expandedChapter === ch.number
              ? 'border-[var(--color-accent)]/30 shadow-sm'
              : 'border-[var(--color-rule)]'"
          >
            <!-- Chapter header -->
            <button
              @click="toggleChapter(ch.number)"
              class="flex items-start gap-4 w-full px-5 py-4 text-left"
            >
              <span
                class="flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold font-[var(--font-display)] flex-shrink-0"
                :class="expandedChapter === ch.number
                  ? 'bg-[var(--color-accent)] text-white'
                  : 'bg-[var(--color-rule-light)] text-[var(--color-ink-muted)]'"
              >
                {{ ch.number }}
              </span>
              <div class="flex-1 min-w-0">
                <h2 class="font-[var(--font-display)] text-base font-semibold text-[var(--color-ink)] leading-snug">
                  {{ ch.name }}
                </h2>
                <p v-if="ch.thesis" class="mt-0.5 text-sm text-[var(--color-ink-light)] leading-relaxed italic">
                  {{ ch.thesis }}
                </p>
                <p v-if="ch.task" class="mt-1 text-xs text-[var(--color-ink-muted)]">{{ ch.task }}</p>
              </div>
              <svg
                xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"
                class="h-4 w-4 text-[var(--color-ink-muted)] flex-shrink-0 mt-1 transition-transform"
                :class="expandedChapter === ch.number ? 'rotate-180' : ''"
              >
                <path fill-rule="evenodd" d="M4.22 6.22a.75.75 0 0 1 1.06 0L8 8.94l2.72-2.72a.75.75 0 1 1 1.06 1.06l-3.25 3.25a.75.75 0 0 1-1.06 0L4.22 7.28a.75.75 0 0 1 0-1.06Z" clip-rule="evenodd" />
              </svg>
            </button>

            <!-- Sections (zoom level 2) -->
            <div v-if="expandedChapter === ch.number" class="border-t border-[var(--color-rule-light)] px-5 pb-4 pt-2">
              <div class="space-y-2">
                <div
                  v-for="sec in ch.sections"
                  :key="sec.id"
                  class="rounded-md border border-[var(--color-rule-light)] transition-all"
                  :class="[
                    expandedSection === sec.id ? 'bg-[var(--color-paper)]' : '',
                    !isDemoMode ? 'cursor-pointer hover:border-[var(--color-accent)]/40' : '',
                  ]"
                >
                  <button
                    @click="toggleSection(sec.id)"
                    class="flex items-center gap-3 w-full px-4 py-2.5 text-left"
                  >
                    <!-- Strength indicator -->
                    <span
                      class="h-2 w-2 rounded-full flex-shrink-0"
                      :class="strengthColors[sectionStrength(sec)].bg"
                      :title="sectionStrength(sec)"
                    />

                    <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)] w-8 flex-shrink-0">{{ sec.id }}</span>
                    <span class="text-sm font-medium text-[var(--color-ink)] flex-1 truncate">{{ sec.name }}</span>

                    <!-- Insight badge (demo only) -->
                    <span
                      v-if="sec.insightCount > 0"
                      class="inline-flex items-center rounded-full bg-[var(--color-amber-pale)] px-1.5 py-0.5 text-xs font-medium text-[var(--color-amber)]"
                    >
                      {{ sec.insightCount }}
                    </span>

                    <!-- Stats -->
                    <span class="text-xs text-[var(--color-ink-muted)] flex-shrink-0">
                      {{ sec.sourceCount }}s<template v-if="isDemoMode"> &middot; {{ sec.fragmentCount }}f</template>
                    </span>

                    <!-- Real mode: draft status badge -->
                    <span
                      v-if="!isDemoMode"
                      class="text-xs flex-shrink-0"
                      :class="sectionDraftStatus(sec.id) === 'draft' ? 'text-[var(--color-ok)]' : 'text-[var(--color-ink-muted)]'"
                      :title="sectionDraftStatus(sec.id) === 'draft' ? 'Черновик сохранён' : 'Пусто'"
                    >{{ sectionDraftStatus(sec.id) === 'draft' ? '●' : '○' }}</span>

                    <!-- Real mode: open arrow instead of expand -->
                    <svg
                      v-if="!isDemoMode"
                      xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"
                      class="h-3.5 w-3.5 text-[var(--color-ink-muted)] flex-shrink-0"
                    >
                      <path fill-rule="evenodd" d="M6.22 4.22a.75.75 0 0 1 1.06 0l3.25 3.25a.75.75 0 0 1 0 1.06l-3.25 3.25a.75.75 0 0 1-1.06-1.06L8.94 8 6.22 5.28a.75.75 0 0 1 0-1.06Z" clip-rule="evenodd" />
                    </svg>
                  </button>

                  <!-- Section detail — demo mode only (zoom level 3) -->
                  <div v-if="isDemoMode && expandedSection === sec.id" class="border-t border-[var(--color-rule-light)] px-4 py-3">
                    <!-- Section thesis -->
                    <p v-if="sec.thesis" class="text-sm text-[var(--color-ink-light)] italic mb-3 leading-relaxed">
                      &laquo; {{ sec.thesis }} &raquo;
                    </p>

                    <!-- Argument blocks (zoom level 4) -->
                    <div v-if="sec.blocks.length > 0" class="space-y-1.5">
                      <div
                        v-for="block in sec.blocks"
                        :key="block.id"
                        class="flex items-start gap-2 rounded-md px-3 py-2 hover:bg-[var(--color-rule-light)] transition-colors cursor-pointer"
                        @click="navigateToBlock(sec.id, block.id)"
                      >
                        <span class="mt-0.5 text-xs" :class="blockStatusColor(block.status)">
                          {{ blockStatusIcon(block.status) }}
                        </span>
                        <div class="flex-1 min-w-0">
                          <p class="text-sm text-[var(--color-ink)]">{{ block.title }}</p>
                          <div class="mt-0.5 flex items-center gap-2">
                            <a
                              v-for="src in block.sources.slice(0, 3)"
                              :key="src"
                              :href="`/demo/library/${src}`"
                              class="citekey-link"
                              @click.stop
                            >@{{ src.slice(0, 15) }}</a>
                            <span v-if="block.sources.length > 3" class="text-xs text-[var(--color-ink-muted)]">
                              +{{ block.sources.length - 3 }}
                            </span>
                          </div>
                        </div>
                        <span class="text-xs text-[var(--color-ink-muted)] flex-shrink-0">~{{ block.wordTarget }}w</span>
                      </div>
                    </div>

                    <!-- Empty blocks state -->
                    <div v-else class="text-center py-4">
                      <p class="text-xs text-[var(--color-ink-muted)]">
                        Структура аргументации не сгенерирована
                      </p>
                      <button class="mt-2 text-xs font-medium text-[var(--color-accent)] hover:underline">
                        Сгенерировать research report
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </AppLayout>
</template>
