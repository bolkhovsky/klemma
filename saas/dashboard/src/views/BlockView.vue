<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { formatMarkdown, renderDraft } from '@/utils/markdown'
import { projects as apiProjects, library as apiLibrary, userProjects, process as apiProcess, write as apiWrite, blocks as apiBlocks, drafts, computeSectionWordCounts, type DraftHeading } from '@/api/client'

const route = useRoute()
const router = useRouter()

// ── Route params ─────────────────────────────────────────────────────────
const projectId = computed(() => route.params.projectId as string | undefined)
const sectionId = computed(() => (route.params.sectionId as string) || '1.1')
const blockIdParam = computed(() => (route.params.blockId as string) || 'b1')
const isDemoMode = computed(() => !route.params.projectId)

// ── Draft file state (real mode only) ────────────────────────────────────
const draftFilename = ref('')
const draftContent = ref('')
const draftHeadings = ref<DraftHeading[]>([])
const activeSectionId = ref<string | null>(null)
const editingBody = ref('')
const isDirty = ref(false)

interface DocSection { section_id: string; full_title: string; level: number; body: string }
const sections = computed<DocSection[]>(() => {
  if (!draftContent.value || !draftHeadings.value.length) return []
  const lines = draftContent.value.split('\n')
  return draftHeadings.value.map((h, i) => {
    const nextLine = draftHeadings.value[i + 1]?.line ?? lines.length
    const body = lines.slice(h.line + 1, nextLine).join('\n').trim()
    return { section_id: h.section_id, full_title: h.full_title, level: h.level, body }
  })
})
const activeSection = computed(() => sections.value.find(s => s.section_id === activeSectionId.value) ?? null)

// Per-section word counts from file content
const sectionWordCounts = computed<Record<string, number>>(() =>
  draftContent.value ? computeSectionWordCounts(draftContent.value, draftHeadings.value) : {}
)

// Active content: section body OR full chapter body (for chapter-level nodes)
const activeContent = computed(() => {
  if (!activeSectionId.value || !draftContent.value) return ''
  const id = activeSectionId.value
  if (!id.includes('.')) {
    // Chapter: extract everything between this chapter heading and the next chapter-level heading
    const lines = draftContent.value.split('\n')
    const chIdx = draftHeadings.value.findIndex(h => h.section_id === id)
    if (chIdx < 0) return ''
    const chHeading = draftHeadings.value[chIdx]
    if (!chHeading) return ''
    const chLine = chHeading.line
    const nextChapterIdx = draftHeadings.value.findIndex((h, i) => i > chIdx && !h.section_id.includes('.'))
    const nextChapterHeading = nextChapterIdx >= 0 ? draftHeadings.value[nextChapterIdx] : undefined
    const endLine = nextChapterHeading?.line ?? lines.length
    return lines.slice(chLine + 1, endLine).join('\n').trim()
  }
  return activeSection.value?.body ?? ''
})

// View / edit mode toggle
const isViewMode = ref(false)

function tocIndent(level: number): string {
  const map: Record<number, string> = { 3: 'pl-3', 4: 'pl-6', 5: 'pl-9' }
  return map[level] ?? ''
}

function scrollToDocSection(id: string) {
  document.getElementById(`doc-sec-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// ── Fragment type ─────────────────────────────────────────────────────────
interface Fragment {
  source: string
  sourceTitle: string
  year: number | null
  text: string
  intent: string
  page: number | null
}

// ── Section / block / chapter state ──────────────────────────────────────
const section = ref({ id: '1.1', name: '', thesis: '' })

const block = ref({
  id: 'b1',
  title: '',
  wordTarget: 350,
  status: 'empty' as 'draft' | 'outlined' | 'empty',
  draftText: '',
  generatedText: '',
  fragments: [] as Fragment[],
  prevBlock: null as { id: string; title: string } | null,
  nextBlock: null as { id: string; title: string } | null,
})

const chapter = ref({ number: 1, thesis: '' })

// ── Loading state ─────────────────────────────────────────────────────────
const loading = ref(false)
const loadError = ref('')

// ── Static mock data (demo mode only) ─────────────────────────────────────
const MOCK_FRAGMENTS: Fragment[] = [
  { source: 'zhuravelSevernyyMorckoyPut2020', sourceTitle: 'Северный морской путь: проблемы, возможности, решения', year: 2020, text: 'Грузопоток по СМП вырос с 7,5 млн тонн в 2016 г. до 34,9 млн тонн в 2022 г., что связано с развитием проектов «Ямал СПГ» и «Арктик СПГ-2».', intent: 'background', page: 12 },
  { source: 'kuvatovPotencialSevernogoMorskogo2014', sourceTitle: 'Потенциал Северного морского пути', year: 2014, text: 'Экономический потенциал СМП определяется сокращением длины маршрута из Европы в Восточную Азию на 30-40% по сравнению с маршрутом через Суэцкий канал.', intent: 'background', page: 45 },
  { source: 'Angudovich2025', sourceTitle: 'Перспективы развития арктического судоходства', year: 2025, text: 'Несмотря на рост грузоперевозок, навигационные риски остаются основным сдерживающим фактором: ледовые условия в переходные сезоны создают наибольшую неопределённость для планирования маршрутов.', intent: 'background', page: 3 },
  { source: 'olhovikINFORMATIONMODELMARITIME2018', sourceTitle: 'Information Model of Maritime Activities in the Arctic Zone', year: 2018, text: 'The non-fixed nature of the Northern Sea Route necessitates real-time monitoring of ice conditions.', intent: 'method', page: 8 },
  { source: 'RasporyazheniePravitelstvaRossiyskoy', sourceTitle: 'Транспортная стратегия РФ до 2035 года', year: 2021, text: 'СМП определяется как «единая национальная транспортная коммуникация в Арктике»; предусматривается круглогодичная навигация к 2030 году.', intent: 'background', page: null },
]

const MOCK_LIBRARY: Fragment[] = [
  { source: 'alekseevaVliyanieIntensivnogoSudohodstva2024', sourceTitle: 'Влияние интенсивного судоходства на экологию АЗРФ', year: 2024, text: 'Фактический грузопоток по СМП в 2023 году составил 36,2 млн тонн, увеличившись на 3,7% по сравнению с 2022 годом.', intent: 'background', page: 7 },
  { source: 'butakovRezultatyPrognozaGidrometeorologicheskih2025', sourceTitle: 'Результаты прогноза гидрометеорологических условий', year: 2025, text: 'Прогноз грузопотока по СМП на 2025 год — свыше 40 млн тонн при условии ввода в эксплуатацию третьей линии «Арктик СПГ-2».', intent: 'background', page: 15 },
  { source: 'massonnet2012', sourceTitle: 'A model study of the impact of sea ice on Arctic climate', year: 2012, text: 'Баренцево море демонстрирует аномально высокую предсказуемость (skill score 0.7 на горизонте 4 мес).', intent: 'result_comparison', page: 1204 },
  { source: 'smirnovMonitoringFizikomehanicheskogoSostoyaniya2020', sourceTitle: 'Мониторинг физико-механического состояния ледяного покрова', year: 2020, text: 'Существующая сеть гидрометеорологических наблюдений на побережье АЗРФ включает 48 станций, из которых 12 оснащены автоматическими средствами.', intent: 'background', page: 33 },
  { source: 'bidenkoGeoinformacionnayaProceduraOcenki2022', sourceTitle: 'Геоинформационная процедура оценки ледовой обстановки', year: 2022, text: 'Предлагается процедура валидации, включающая три этапа: предобработку спутниковых данных, пространственное сопоставление и расчёт метрик.', intent: 'method', page: 89 },
]

// ── Data loading ──────────────────────────────────────────────────────────
async function loadSectionData() {
  loading.value = true
  loadError.value = ''
  try {
    if (isDemoMode.value) {
      section.value = { id: sectionId.value, name: 'Потребности пользователей НГГМИ', thesis: 'Рост грузопотока по СМП создаёт потребность в оперативных ледовых прогнозах' }
      block.value = { id: blockIdParam.value, title: 'Стратегическое значение СМП и рост грузопотока', wordTarget: 350, status: 'draft', draftText: 'Северный морской путь (СМП) является исторически сложившейся транспортной артерией России в Арктике. Транспортная стратегия РФ до 2035 года определяет СМП как «единую национальную транспортную коммуникацию», а план развития инфраструктуры предусматривает увеличение грузопотока до 80 млн тонн к 2024 году и 150 млн тонн к 2030 году.', generatedText: '', fragments: [...MOCK_FRAGMENTS], prevBlock: null, nextBlock: { id: 'b1.1.2', title: 'Потребности навигации в ледовой информации' } }
      chapter.value = { number: 1, thesis: 'Существующие системы НГО не обеспечивают достаточную точность прогнозов' }
      return
    }

    // Real API: load section sources + fragment details
    section.value.id = sectionId.value
    block.value.id = blockIdParam.value

    // Fire sectionSources + project list in parallel
    const [sectionData, projectsData] = await Promise.all([
      apiProjects.sectionSources(sectionId.value),
      userProjects.list(),
    ])

    // Resolve section name immediately (no extra wait)
    const project = projectsData.projects.find(p => p.project_id === projectId.value)
    const outlineSection = project?.outline?.find(s => s.id === sectionId.value)
    section.value.name = outlineSection?.name || `Раздел ${sectionId.value}`
    block.value.title = outlineSection?.name || `Раздел ${sectionId.value}`

    // Fetch all source details in parallel
    const citekeys = sectionData.citekeys.slice(0, 8)
    const sourceResults = await Promise.allSettled(citekeys.map(k => apiLibrary.get(k)))

    const fragments: Fragment[] = []
    for (let i = 0; i < citekeys.length; i++) {
      const result = sourceResults[i]
      if (result?.status !== 'fulfilled') continue
      const src = result.value
      for (const f of (src.fragments || []).slice(0, 2)) {
        fragments.push({ source: citekeys[i]!, sourceTitle: src.title || citekeys[i]!, year: src.year || null, text: f.text || '', intent: f.citation_intent || 'background', page: f.page_number || null })
      }
    }
    block.value.fragments = fragments

    // Load saved draft (MD file on disk via sync-compatible endpoint)
    if (projectId.value) {
      try {
        const saved = await apiBlocks.get(projectId.value, sectionId.value, blockIdParam.value)
        if (saved.text) {
          block.value.draftText = saved.text
          block.value.status = 'draft'
        }
      } catch {
        // No saved draft yet — that's fine
      }
    }

  } catch (e: any) {
    loadError.value = e.message || 'Ошибка загрузки данных'
  } finally {
    loading.value = false
  }
}

// ── Writing ──────────────────────────────────────────────────────────────

const isEditing = ref(false)
const generating = ref(false)
const generatingProgress = ref('')

const currentText = computed(() =>
  isDemoMode.value
    ? (block.value.generatedText || block.value.draftText)
    : activeContent.value
)
const wordCount = computed(() => {
  if (isDemoMode.value) return currentText.value ? currentText.value.trim().split(/\s+/).filter(Boolean).length : 0
  const id = activeSectionId.value
  if (!id) return 0
  if (!id.includes('.')) {
    // Chapter: sum of its subsection word counts
    return sections.value
      .filter(s => s.section_id.startsWith(id + '.'))
      .reduce((sum, s) => sum + (sectionWordCounts.value[s.section_id] ?? 0), 0)
  }
  return sectionWordCounts.value[id] ?? 0
})
const wordPercent = computed(() => Math.min(Math.round((wordCount.value / block.value.wordTarget) * 100), 100))
const editWordCount = computed(() => editingBody.value ? editingBody.value.trim().split(/\s+/).filter(Boolean).length : 0)

function startEditing() {
  // Demo mode: open contenteditable for the single block
  if (isDemoMode.value) {
    editingBody.value = currentText.value
    isEditing.value = true
    nextTick(() => {
      const el = editorEl.value
      if (!el) return
      el.innerText = currentText.value
      el.focus()
      moveCursorToEnd(el)
    })
    return
  }
  // Real mode: focus existing active section editor
  nextTick(() => editorEl.value?.focus())
}

function commitText() {
  // Demo mode only: persist editingBody to block state
  block.value.generatedText = editingBody.value
  block.value.draftText = editingBody.value
  block.value.status = 'draft'
}

function autoSave() {
  isDirty.value = true
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    if (isDemoMode.value) {
      commitText()
      saveStatus.value = 'saved'
      setTimeout(() => { if (saveStatus.value === 'saved') saveStatus.value = 'idle' }, 2500)
    } else {
      await saveSection()
    }
  }, 800)
}

function closeEditor() {
  if (isDemoMode.value) {
    clearGhost()
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    commitText()
    saveStatus.value = 'idle'
    isEditing.value = false
  } else {
    deactivateSection()
  }
}

// ── Draft section management ──────────────────────────────────────────────

async function activateSection(id: string) {
  if (activeSectionId.value === id) return
  if (isDirty.value && activeSectionId.value) await saveSection()
  clearGhost()

  activeSectionId.value = id
  isDirty.value = false
  isViewMode.value = true  // default to view mode on navigation

  if (projectId.value) {
    router.replace({ name: 'block', params: { projectId: projectId.value, sectionId: id, blockId: blockIdParam.value } })
  }

  // Preload editing body so switching to edit mode has content ready
  await nextTick()
  editingBody.value = activeContent.value
  isEditing.value = true
}

function deactivateSection() {
  clearGhost()
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  if (isDirty.value && activeSectionId.value) saveSection()
  activeSectionId.value = null
  isDirty.value = false
  isEditing.value = false
}

async function saveSection() {
  if (!activeSectionId.value || !draftFilename.value || isDemoMode.value) return
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  saveStatus.value = 'saving'
  isDirty.value = false
  try {
    await drafts.upsertSection(projectId.value!, draftFilename.value, activeSectionId.value, editingBody.value)
    saveStatus.value = 'saved'
    const updated = await drafts.get(projectId.value!, draftFilename.value)
    draftContent.value = updated.content
    draftHeadings.value = updated.headings
    setTimeout(() => { if (saveStatus.value === 'saved') saveStatus.value = 'idle' }, 2500)
  } catch {
    saveStatus.value = 'idle'
  }
}

async function generateDraft() {
  generating.value = true
  generatingProgress.value = 'Собираю контекст...'
  pushAssistantMessage(`Начинаю генерацию. ${block.value.fragments.length} фрагментов в контексте.`)

  if (isDemoMode.value) {
    await _generateDraftMock()
    return
  }

  // Real API: POST /write/draft + poll
  try {
    generatingProgress.value = 'Ставлю задачу...'
    const job = await apiWrite.draft(sectionId.value, projectId.value, block.value.wordTarget)
    generatingProgress.value = 'Задача в очереди, жду результат...'

    for (let attempt = 0; attempt < 30; attempt++) {
      await new Promise(r => setTimeout(r, 2000))
      const status = await apiProcess.jobStatus(job.job_id)
      if (status.status === 'finished') {
        const result = status.result || {}
        if (result.text) {
          generating.value = false
          generatingProgress.value = ''
          if (!isDemoMode.value && activeSectionId.value) {
            editingBody.value = result.text
            await saveSection()
            await nextTick()
            if (editorEl.value) {
              editorEl.value.innerText = result.text
            }
          } else {
            block.value.generatedText = result.text
            block.value.status = 'draft'
          }
          pushAssistantMessage(`Черновик готов — ${result.text.split(/\s+/).length} слов.`)
          return
        }
        if (result.status === 'error') {
          generating.value = false
          generatingProgress.value = ''
          pushAssistantMessage(`Ошибка генерации: ${result.detail || 'неизвестная ошибка'}`)
          return
        }
        // Empty text without error — show demo
        pushAssistantMessage('Черновик пуст. Показываю демо-пример.')
        await _generateDraftMock()
        return
      }
      if (status.status === 'failed') {
        const result = status.result || {}
        generating.value = false
        generatingProgress.value = ''
        pushAssistantMessage(`Ошибка генерации: ${result.error || 'задача завершилась с ошибкой'}`)
        return
      }
      generatingProgress.value = `Генерирую... (${(attempt + 1) * 2}с)`
    }
    pushAssistantMessage('Таймаут. Показываю демо-текст.')
    await _generateDraftMock()
  } catch (e: any) {
    pushAssistantMessage(`Ошибка API: ${e.message}. Показываю демо-текст.`)
    await _generateDraftMock()
  }
}

async function _generateDraftMock() {
  generatingProgress.value = `Анализирую ${block.value.fragments.length} фрагментов...`
  await new Promise(r => setTimeout(r, 600))
  generatingProgress.value = `Генерирую текст (~${block.value.wordTarget} слов)...`
  await new Promise(r => setTimeout(r, 2000))
  block.value.generatedText = `Северный морской путь (СМП) является исторически сложившейся транспортной артерией России в Арктике, стратегическое значение которой определяется Транспортной стратегией РФ до 2035 года как «единой национальной транспортной коммуникации» [@RasporyazheniePravitelstvaRossiyskoy]. Грузопоток по СМП демонстрирует устойчивый рост: с 7,5 млн тонн в 2016 году до 34,9 млн тонн в 2022 году [@zhuravelSevernyyMorckoyPut2020].

Экономический потенциал СМП определяется сокращением длины маршрута из Европы в Восточную Азию на 30–40% по сравнению с маршрутом через Суэцкий канал [@kuvatovPotencialSevernogoMorskogo2014]. Вместе с тем навигационные риски остаются основным сдерживающим фактором развития арктического судоходства: ледовые условия в переходные сезоны создают наибольшую неопределённость для планирования маршрутов [@Angudovich2025].

Принципиальной особенностью СМП является нефиксированность трассы: суда вынуждены непрерывно адаптировать маршрут в зависимости от текущей ледовой обстановки [@olhovikINFORMATIONMODELMARITIME2018]. Это формирует потребность в оперативном мониторинге и прогнозировании ледовой обстановки.`
  block.value.status = 'draft'
  generating.value = false
  generatingProgress.value = ''
  pushAssistantMessage(`Готово — ${block.value.generatedText.split(/\s+/).length} слов, ${block.value.fragments.length} источников.`)
}

// ── Ghost text (inline AI continuation) ──────────────────────────────────

const ghostEnabled = ref(localStorage.getItem('klemma_ghost_enabled') !== 'false')
watch(ghostEnabled, v => {
  localStorage.setItem('klemma_ghost_enabled', String(v))
  if (!v) clearGhost()
})

const editorEl = ref<HTMLElement>()
const ghostText = ref('')
const isSpinning = ref(false)
let ghostTimer: ReturnType<typeof setTimeout> | null = null
let streamingTimer: ReturnType<typeof setTimeout> | null = null
let spinInterval: ReturnType<typeof setInterval> | null = null

const saveStatus = ref<'idle' | 'saving' | 'saved'>('idle')
let saveTimer: ReturnType<typeof setTimeout> | null = null

function moveCursorToEnd(el: HTMLElement) {
  const range = document.createRange()
  const sel = window.getSelection()
  range.selectNodeContents(el)
  range.collapse(false)
  sel?.removeAllRanges()
  sel?.addRange(range)
}

function clearGhost() {
  ghostText.value = ''
  isSpinning.value = false
  if (streamingTimer) { clearTimeout(streamingTimer); streamingTimer = null }
  if (ghostTimer) { clearTimeout(ghostTimer); ghostTimer = null }
  if (spinInterval) { clearInterval(spinInterval); spinInterval = null }
  editorEl.value?.querySelectorAll('.ghost-text, .ghost-spinner').forEach(s => s.remove())
}

function acceptGhost() {
  const el = editorEl.value
  if (!el || !ghostText.value) return
  el.querySelectorAll('.ghost-text').forEach(ghost => {
    ghost.replaceWith(document.createTextNode(ghost.textContent || ''))
  })
  editingBody.value = el.innerText
  ghostText.value = ''
  if (streamingTimer) { clearTimeout(streamingTimer); streamingTimer = null }
  moveCursorToEnd(el)
  autoSave()
  pushAssistantMessage('Предложение принято. Продолжайте.')
}

function getMockSuggestion(text: string): string {
  const lower = text.toLowerCase()
  if (lower.includes('грузопоток') || lower.includes('арктик спг') || lower.includes('ямал')) {
    return ' По прогнозу Министерства транспорта РФ, к 2030 году объём перевозок по СМП должен достичь 150 млн тонн, что потребует существенного расширения ледокольного флота и инфраструктуры навигационно-гидрографического обеспечения.'
  }
  if (lower.includes('ледов') || lower.includes('лёд') || lower.includes('концентрац')) {
    return ' Систематические наблюдения ведёт Арктический и антарктический научно-исследовательский институт (ААНИИ), однако точность краткосрочных прогнозов снижается в периоды активной циклонической деятельности.'
  }
  if (lower.includes('прогноз') || lower.includes('точност') || lower.includes('нейросет')) {
    return ' Применение нейросетевых моделей позволяет повысить точность прогнозирования концентрации морского льда на 15–25% по сравнению с детерминированными численными методами [@bidenkoGeoinformacionnayaProceduraOcenki2022].'
  }
  if (lower.includes('навигац') || lower.includes('судоходств') || lower.includes('маршрут')) {
    return ' Критическим периодом для безопасной навигации являются сентябрь–октябрь: первый осенний ледостав в Восточно-Сибирском море создаёт трудно предсказуемые условия для транзитных рейсов.'
  }
  return ' Данный аспект имеет принципиальное значение для разработки методики оценки качества нейросетевых прогнозов ледовой обстановки в акватории Северного морского пути [@bidenkoGeoinformacionnayaProceduraOcenki2022].'
}

function fetchGhostSuggestion() {
  const text = editingBody.value.trim()
  if (text.length < 30) return

  const suggestion = getMockSuggestion(text)
  const el = editorEl.value
  if (!el) return

  // Phase 1: spinner  · ·· ···
  isSpinning.value = true
  const spinnerSpan = document.createElement('span')
  spinnerSpan.className = 'ghost-spinner'
  spinnerSpan.style.color = 'var(--color-ink-muted)'
  spinnerSpan.style.opacity = '0.7'
  spinnerSpan.style.pointerEvents = 'none'
  spinnerSpan.style.userSelect = 'none'
  spinnerSpan.style.marginLeft = '6px'
  spinnerSpan.style.letterSpacing = '3px'
  spinnerSpan.style.fontSize = '11px'
  spinnerSpan.style.border = '1px solid var(--color-rule)'
  spinnerSpan.style.borderRadius = '999px'
  spinnerSpan.style.padding = '1px 7px 2px'
  spinnerSpan.style.verticalAlign = 'middle'
  spinnerSpan.style.display = 'inline-block'
  el.appendChild(spinnerSpan)

  let dots = 1
  spinnerSpan.textContent = '·'
  spinInterval = setInterval(() => {
    if (!el.contains(spinnerSpan)) { clearInterval(spinInterval!); spinInterval = null; return }
    dots = dots >= 3 ? 1 : dots + 1
    spinnerSpan.textContent = '·'.repeat(dots)
  }, 200)

  // Phase 2: after one full dot cycle (600ms) — replace with full text at once
  streamingTimer = setTimeout(() => {
    if (spinInterval) { clearInterval(spinInterval); spinInterval = null }
    if (!el.contains(spinnerSpan)) return
    spinnerSpan.remove()
    isSpinning.value = false

    const ghostSpan = document.createElement('span')
    ghostSpan.className = 'ghost-text'
    ghostSpan.style.color = 'var(--color-ink-muted)'
    ghostSpan.style.opacity = '0.4'
    ghostSpan.style.pointerEvents = 'none'
    ghostSpan.style.userSelect = 'none'
    ghostSpan.textContent = suggestion
    el.appendChild(ghostSpan)
    ghostText.value = suggestion
  }, 600)
}

function onEditorInput() {
  clearGhost()
  editingBody.value = editorEl.value?.innerText || ''
  autoSave()
  if (!ghostEnabled.value) return
  const text = editingBody.value.trim()
  if (text.length < 30) return
  // Only suggest at sentence boundaries — after ". ", "! ", "? " or end-of-line
  if (!/[.!?][\s\n]*$/.test(text)) return
  ghostTimer = setTimeout(fetchGhostSuggestion, 1500)
}

function onEditorKeydown(e: KeyboardEvent) {
  if (e.key === 'Tab' && ghostText.value) {
    e.preventDefault()
    acceptGhost()
    return
  }
  if (e.key === 'Escape') {
    clearGhost()
    return
  }
  if (e.metaKey && e.key === 'Enter') {
    e.preventDefault()
    closeEditor()
  }
}

function onEditorPaste(e: ClipboardEvent) {
  e.preventDefault()
  const text = e.clipboardData?.getData('text/plain') || ''
  const sel = window.getSelection()
  if (!sel?.rangeCount) return
  sel.deleteFromDocument()
  const node = document.createTextNode(text)
  sel.getRangeAt(0).insertNode(node)
  sel.collapseToEnd()
  editingBody.value = editorEl.value?.innerText || ''
}

// ── Fragment search ──────────────────────────────────────────────────────

const searchQuery = ref('')
const searchResults = ref<Fragment[]>([])
const searching = ref(false)
const libraryItems = ref<Fragment[]>([])

async function ensureLibraryLoaded() {
  if (isDemoMode.value || libraryItems.value.length > 0) return
  try {
    const data = await apiLibrary.list(projectId.value)
    libraryItems.value = data.sources.map((s: any) => ({
      source: s.citekey,
      sourceTitle: s.title || s.citekey,
      year: s.year || null,
      text: s.abstract || '',
      intent: 'background',
      page: null,
    }))
  } catch { /* silent — search will just return empty */ }
}

function _searchIn(pool: Fragment[], q: string) {
  return pool.filter(f =>
    !block.value.fragments.some(bf => bf.source === f.source) &&
    (f.sourceTitle.toLowerCase().includes(q) || f.source.toLowerCase().includes(q) || f.text.toLowerCase().includes(q))
  )
}

let searchTimeout: ReturnType<typeof setTimeout> | null = null
function onSearchInput() {
  if (searchTimeout) clearTimeout(searchTimeout)
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) { searchResults.value = []; return }
  searching.value = true

  if (!isDemoMode.value && libraryItems.value.length === 0) {
    ensureLibraryLoaded().then(() => {
      searchResults.value = _searchIn(libraryItems.value, q)
      searching.value = false
    })
    return
  }

  searchTimeout = setTimeout(() => {
    const pool = isDemoMode.value ? MOCK_LIBRARY : libraryItems.value
    searchResults.value = _searchIn(pool, q)
    searching.value = false
  }, 300)
}

async function attachFragment(f: Fragment) {
  block.value.fragments.push(f)
  searchResults.value = searchResults.value.filter(r => r.source !== f.source)
  pushAssistantMessage(`Привязан [@${f.source}] (${f.year}). Теперь ${block.value.fragments.length} фрагментов — можно перегенерировать текст чтобы учесть новый источник.`)
  if (!isDemoMode.value) {
    try { await apiProjects.assignSections(f.source, [sectionId.value]) } catch { /* non-fatal */ }
  }
}

function detachFragment(index: number) {
  const removed = block.value.fragments[index]
  block.value.fragments.splice(index, 1)
  if (removed) pushAssistantMessage(`Отвязан @${removed.source}. Если он упоминается в тексте — стоит убрать ссылку вручную.`)
}

// ── AI Assistant (reactive strip) ────────────────────────────────────────

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

  // Simulate response
  await new Promise(r => setTimeout(r, 1000))
  pushAssistantMessage(`В библиотеке есть 2 дополнительных источника по этой теме. Попробуйте найти их через поиск справа: «грузопоток 2024» или «арктик спг».`)
}

// Hide "Сохранено" badge the moment ghost spinner kicks in
watch(isSpinning, (spinning) => {
  if (spinning) saveStatus.value = 'idle'
})

// Watch text edits to trigger AI reactions
watch(isEditing, (editing) => {
  if (editing) {
    pushAssistantMessage('Режим редактирования. Пишите — я слежу за цитированием и покрытием аргументов.')
  }
})

// When switching to edit mode, restore editor content from editingBody
watch(isViewMode, async (viewMode) => {
  if (!viewMode && isEditing.value) {
    await nextTick()
    if (editorEl.value) {
      editorEl.value.innerText = editingBody.value
      editorEl.value.focus()
      moveCursorToEnd(editorEl.value)
    }
  }
})

onMounted(async () => {
  await loadSectionData()

  if (!isDemoMode.value && projectId.value) {
    try {
      const data = await drafts.init(projectId.value)
      draftFilename.value = data.name
      draftContent.value = data.content
      draftHeadings.value = data.headings
      // Activate section from URL param (works for both chapter and section IDs)
      const targetId = sectionId.value
      const validId = draftHeadings.value.find(h => h.section_id === targetId)?.section_id
        ?? draftHeadings.value[0]?.section_id
      if (validId) {
        activeSectionId.value = validId
        isViewMode.value = true
        isEditing.value = true
        editingBody.value = activeContent.value
      }
    } catch { /* non-fatal — fall back to demo-style editing */ }
  }

  pushAssistantMessage(`${block.value.title ? `Блок «${block.value.title}»` : 'Раздел'} — ${block.value.fragments.length} фрагментов привязано. ${currentText.value ? `Черновик: ${wordCount.value}/${block.value.wordTarget} слов.` : 'Текст не написан — нажмите «Написать блок».'}`)
})

// ── Download ─────────────────────────────────────────────────────────────

function downloadDraft() {
  if (!draftContent.value || !draftFilename.value) return
  const blob = new Blob([draftContent.value], { type: 'text/markdown; charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = draftFilename.value
  a.click()
  URL.revokeObjectURL(url)
}

// ── Helpers ──────────────────────────────────────────────────────────────

const intentLabels: Record<string, { label: string; color: string }> = {
  background: { label: 'фон', color: 'var(--color-ink-muted)' },
  method: { label: 'метод', color: 'var(--color-accent)' },
  result_comparison: { label: 'результат', color: 'var(--color-violet)' },
  extends: { label: 'развивает', color: 'var(--color-ok)' },
  contrasts: { label: 'оспаривает', color: 'var(--color-cta)' },
}

function timeAgo(d: Date): string {
  const s = Math.round((Date.now() - d.getTime()) / 1000)
  if (s < 10) return 'сейчас'
  if (s < 60) return `${s}с`
  return `${Math.round(s / 60)}м`
}
</script>

<template>
  <AppLayout>
    <div class="max-w-6xl mx-auto">
      <!-- Loading / error banners -->
      <div v-if="loading" class="flex items-center gap-2 mb-4 text-sm text-[var(--color-ink-muted)]">
        <div class="h-4 w-4 border-2 border-[var(--color-accent)] border-t-transparent rounded-full animate-spin flex-shrink-0" />
        Загружаю данные раздела...
      </div>
      <div v-if="loadError" class="mb-4 rounded-md bg-[var(--color-err-bg)] border border-[var(--color-err)]/30 px-4 py-2 text-sm text-[var(--color-err)]">
        {{ loadError }}
      </div>

      <!-- Breadcrumb + nav -->
      <div class="flex items-center gap-1.5 text-sm mb-3 flex-wrap">
        <button @click="router.push(projectId ? `/${projectId}/map` : '/demo/map')" class="text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">Карта</button>
        <span v-if="chapter.number" class="text-[var(--color-rule)]">/</span>
        <button v-if="chapter.number" @click="router.push(projectId ? `/${projectId}/map` : '/demo/map')" class="text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">Гл. {{ chapter.number }}</button>
        <span class="text-[var(--color-rule)]">/</span>
        <button @click="router.push(projectId ? `/${projectId}/map` : '/demo/map')" class="text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">{{ section.id }}</button>
        <span class="text-[var(--color-rule)]">/</span>
        <span class="text-[var(--color-ink)] font-medium truncate">{{ block.title || section.id }}</span>
        <div class="flex-1" />
        <button v-if="block.prevBlock" class="rounded-md px-2 py-1 text-xs text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)]">&larr; Пред</button>
        <button v-if="block.nextBlock" class="rounded-md px-2 py-1 text-xs text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)]">След &rarr;</button>
      </div>

      <!-- Chain context bar -->
      <div v-if="chapter.thesis || section.thesis" class="flex items-center gap-3 rounded-md bg-[var(--color-paper-warm)] px-4 py-2 mb-4 text-xs text-[var(--color-ink-muted)]">
        <span v-if="chapter.thesis"><strong class="text-[var(--color-accent)]">Гл. {{ chapter.number }}:</strong> <em>{{ chapter.thesis }}</em></span>
        <span v-if="chapter.thesis && section.thesis" class="text-[var(--color-rule)]">&rarr;</span>
        <span v-if="section.thesis"><strong class="text-[var(--color-accent)]">{{ section.id }}:</strong> <em>{{ section.thesis }}</em></span>
      </div>

      <!-- MAIN LAYOUT: real=3col (TOC|center|fragments), demo=2col -->

      <!-- REAL MODE: TOC + full document + fragments -->
      <div v-if="!isDemoMode" class="flex gap-4 items-start">

        <!-- LEFT: TOC (w-48 sticky) -->
        <nav class="w-48 flex-shrink-0 sticky top-8">
          <div class="flex items-center mb-2">
            <p class="flex-1 text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-muted)]">Содержание</p>
            <button
              v-if="draftContent && draftFilename"
              @click="downloadDraft"
              class="rounded p-1 text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] hover:bg-[var(--color-accent-pale)] transition-colors"
              :title="`Скачать ${draftFilename}`"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-3.5 w-3.5">
                <path d="M8.75 2.75a.75.75 0 0 0-1.5 0v5.69L5.03 6.22a.75.75 0 0 0-1.06 1.06l3.5 3.5a.75.75 0 0 0 1.06 0l3.5-3.5a.75.75 0 0 0-1.06-1.06L8.75 8.44V2.75Z" />
                <path d="M3.5 9.75a.75.75 0 0 0-1.5 0v1.5A2.75 2.75 0 0 0 4.75 14h6.5A2.75 2.75 0 0 0 14 11.25v-1.5a.75.75 0 0 0-1.5 0v1.5c0 .69-.56 1.25-1.25 1.25h-6.5c-.69 0-1.25-.56-1.25-1.25v-1.5Z" />
              </svg>
            </button>
          </div>
          <div class="flex flex-col gap-0.5">
            <button v-for="sec in sections" :key="sec.section_id"
              @click="activateSection(sec.section_id)"
              class="flex items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs transition-colors leading-tight w-full"
              :class="[tocIndent(sec.level), activeSectionId === sec.section_id
                ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)] font-medium'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]']">
              <span class="font-[var(--font-mono)] flex-shrink-0 text-[10px] opacity-60">{{ sec.section_id }}</span>
              <span class="truncate flex-1">{{ sec.full_title.replace(/^\d[\d.]*\s*/, '') }}</span>
              <span v-if="sectionWordCounts[sec.section_id]" class="font-[var(--font-mono)] text-[9px] opacity-50 flex-shrink-0">{{ sectionWordCounts[sec.section_id] }}</span>
            </button>
          </div>
        </nav>

        <!-- CENTER: Unified editor (view / edit modes) -->
        <div class="flex-1 min-w-0 flex flex-col gap-4">

          <!-- Title row + word count + mode toggle -->
          <div class="flex items-center gap-3">
            <h1 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)] leading-snug flex-1">
              {{ sections.find(s => s.section_id === activeSectionId)?.full_title.replace(/^\d[\d.]*\s*/, '') ?? activeSectionId ?? '—' }}
            </h1>
            <!-- Chapter: show total word count only; Section: show N/target -->
            <template v-if="activeSectionId && !activeSectionId.includes('.')">
              <span class="font-[var(--font-mono)] text-xs" :class="wordCount > 0 ? 'text-[var(--color-ok)]' : 'text-[var(--color-ink-muted)]'">
                {{ wordCount }}w
              </span>
            </template>
            <template v-else>
              <span class="font-[var(--font-mono)] text-xs" :class="wordPercent >= 80 ? 'text-[var(--color-ok)]' : wordPercent >= 40 ? 'text-[var(--color-amber)]' : 'text-[var(--color-ink-muted)]'">
                {{ wordCount }}/{{ block.wordTarget }}
              </span>
              <div class="w-16 h-1.5 rounded-full bg-[var(--color-rule-light)] overflow-hidden">
                <div class="h-full rounded-full transition-all duration-500"
                  :class="wordPercent >= 80 ? 'bg-[var(--color-ok)]' : wordPercent >= 40 ? 'bg-[var(--color-amber)]' : 'bg-[var(--color-rule)]'"
                  :style="{ width: `${wordPercent}%` }" />
              </div>
            </template>
            <!-- View / Edit toggle -->
            <button
              v-if="activeSectionId"
              @click="isViewMode = !isViewMode"
              class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
              :class="isViewMode
                ? 'border-[var(--color-rule)] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
                : 'border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent-pale)]'"
            >{{ isViewMode ? 'Редактировать' : 'Просмотр' }}</button>
          </div>

          <!-- THE EDITOR BLOCK -->
          <div class="rounded-lg border bg-[var(--color-paper-white)] transition-colors"
            :class="!isViewMode && isEditing ? 'border-[var(--color-accent)]/40 shadow-sm' : 'border-[var(--color-rule)]'"
          >
            <!-- Generating spinner -->
            <div v-if="generating" class="flex items-center justify-center py-24">
              <div class="text-center">
                <div class="h-8 w-8 border-2 border-[var(--color-accent)] border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                <p class="text-sm text-[var(--color-ink-light)]">{{ generatingProgress }}</p>
              </div>
            </div>

            <!-- VIEW mode: rendered markdown -->
            <div v-else-if="isViewMode && activeSectionId"
              class="draft-prose px-8 py-6 text-[15px] text-[var(--color-ink)] leading-[1.8] font-[var(--font-body)] cursor-text"
              @click="isViewMode = false"
              v-html="activeContent ? renderDraft(activeContent) : '<p class=\'text-[var(--color-ink-muted)] italic\'>Раздел пуст — нажмите для редактирования</p>'"
            />

            <!-- EDIT mode: contenteditable -->
            <div v-else-if="!isViewMode && isEditing">
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
                <transition name="ghost-hint">
                  <div v-if="ghostText && !isSpinning"
                    class="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-md bg-[var(--color-paper-warm)] border border-[var(--color-rule)] px-2.5 py-1 text-xs text-[var(--color-ink-muted)] shadow-sm pointer-events-none select-none">
                    <kbd class="font-[var(--font-mono)] text-[var(--color-accent)] font-semibold">Tab</kbd>
                    <span>принять</span>
                  </div>
                </transition>
              </div>
              <div class="flex items-center gap-3 border-t border-[var(--color-rule-light)] px-6 py-2.5">
                <transition name="ghost-hint">
                  <span v-if="saveStatus === 'saved'" class="text-xs text-[var(--color-ok)]">Сохранено ✓</span>
                </transition>
                <div class="flex-1" />
                <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">{{ editWordCount }} слов</span>
                <button
                  @click="ghostEnabled = !ghostEnabled"
                  :title="ghostEnabled ? 'Выключить kAI автодополнение' : 'Включить kAI автодополнение'"
                  class="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold tracking-tight transition-all duration-150"
                  :class="ghostEnabled
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent-pale)] text-[var(--color-accent)]'
                    : 'border-[var(--color-rule)] bg-transparent text-[var(--color-ink-muted)]'"
                >
                  <span
                    class="h-[7px] w-[7px] rounded-full border transition-all duration-150 flex-shrink-0"
                    :class="ghostEnabled
                      ? 'bg-[var(--color-accent)] border-[var(--color-accent)]'
                      : 'bg-transparent border-[var(--color-ink-muted)]'"
                  />
                  kAI
                </button>
                <button @click="generateDraft"
                  class="rounded-md border border-[var(--color-rule)] px-2.5 py-1 text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] transition-colors"
                  title="Перегенерировать">↺</button>
              </div>
            </div>

            <!-- No section selected -->
            <div v-else class="flex items-center justify-center py-20">
              <p class="text-sm text-[var(--color-ink-muted)]">Выберите раздел в содержании слева</p>
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

        <!-- RIGHT: Fragments panel (w-52) -->
        <div class="w-52 flex-shrink-0 flex flex-col" style="max-height: calc(100vh - 9rem);">
          <!-- Search -->
          <div class="mb-2 flex-shrink-0">
            <div class="relative">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-ink-muted)]">
                <path fill-rule="evenodd" d="M9.965 11.026a5 5 0 1 1 1.06-1.06l2.755 2.754a.75.75 0 1 1-1.06 1.06l-2.755-2.754ZM10.5 7a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0Z" clip-rule="evenodd" />
              </svg>
              <input v-model="searchQuery" @input="onSearchInput" type="text"
                class="w-full rounded-md border border-[var(--color-rule)] bg-[var(--color-paper)] pl-8 pr-3 py-1.5 text-sm placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none"
                placeholder="Найти в библиотеке..." />
            </div>
          </div>
          <!-- Search results -->
          <div v-if="searchQuery.trim() && (searchResults.length > 0 || searching)" class="mb-2 flex-shrink-0">
            <div class="rounded-md border border-[var(--color-accent)]/30 bg-[var(--color-accent-pale)]/20 overflow-hidden">
              <div class="px-3 py-1 text-xs font-semibold text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]/40">Результаты</div>
              <div v-if="searching" class="px-3 py-3 text-center text-xs text-[var(--color-ink-muted)]">Ищу...</div>
              <div v-else-if="searchResults.length === 0" class="px-3 py-2 text-center text-xs text-[var(--color-ink-muted)]">Не найдено</div>
              <div v-else class="divide-y divide-[var(--color-rule-light)]">
                <div v-for="r in searchResults" :key="r.source" class="px-3 py-2 hover:bg-[var(--color-accent-pale)]/40">
                  <div class="flex items-center gap-1 mb-0.5">
                    <a :href="`/${projectId}/library/${r.source}`" class="citekey-link" @click.prevent>@{{ r.source.length > 14 ? r.source.slice(0, 14) + '..' : r.source }}</a>
                    <span class="text-xs text-[var(--color-ink-muted)]">{{ r.year }}</span>
                    <button @click="attachFragment(r)"
                      class="ml-auto rounded bg-[var(--color-accent)] px-1.5 py-0.5 text-xs font-medium text-white hover:bg-[var(--color-accent-deep)]">+</button>
                  </div>
                  <p class="text-xs text-[var(--color-ink-light)] leading-relaxed line-clamp-2">{{ r.text }}</p>
                </div>
              </div>
            </div>
          </div>
          <!-- Attached fragments -->
          <div class="flex-1 overflow-y-auto space-y-2">
            <div class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider px-1">
              Привязано ({{ block.fragments.length }})
            </div>
            <div v-for="(f, i) in block.fragments" :key="f.source + i"
              class="group rounded-md border border-[var(--color-rule-light)] bg-[var(--color-paper-white)] px-3 py-2 relative">
              <div class="flex items-center gap-1 mb-0.5">
                <a :href="`/${projectId}/library/${f.source}`" class="citekey-link">@{{ f.source.length > 14 ? f.source.slice(0, 14) + '..' : f.source }}</a>
                <span class="text-xs text-[var(--color-ink-muted)]">{{ f.year }}</span>
                <span class="ml-auto text-xs" :style="{ color: intentLabels[f.intent]?.color }">{{ intentLabels[f.intent]?.label }}</span>
              </div>
              <p class="text-xs text-[var(--color-ink-light)] leading-relaxed">{{ f.text }}</p>
              <button @click="detachFragment(i)"
                class="absolute top-1.5 right-1.5 h-4 w-4 rounded flex items-center justify-center text-[var(--color-ink-muted)] hover:text-[var(--color-err)] hover:bg-[var(--color-err-bg)] opacity-0 group-hover:opacity-100 transition-all"
                title="Отвязать">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-2.5 w-2.5">
                  <path d="M5.28 4.22a.75.75 0 0 0-1.06 1.06L6.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L8 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L9.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L8 6.94 5.28 4.22Z" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- DEMO MODE: single-block layout -->
      <div v-else class="grid grid-cols-1 lg:grid-cols-4 gap-4">

        <!-- LEFT: Text + AI assistant (3/4) -->
        <div class="lg:col-span-3 flex flex-col gap-4">

          <!-- Block title + word progress -->
          <div class="flex items-center gap-3">
            <h1 class="font-[var(--font-display)] text-xl font-semibold text-[var(--color-ink)] leading-snug flex-1">
              {{ block.title }}
            </h1>
            <span class="font-[var(--font-mono)] text-xs" :class="wordPercent >= 80 ? 'text-[var(--color-ok)]' : wordPercent >= 40 ? 'text-[var(--color-amber)]' : 'text-[var(--color-ink-muted)]'">
              {{ wordCount }}/{{ block.wordTarget }}
            </span>
            <div class="w-16 h-1.5 rounded-full bg-[var(--color-rule-light)] overflow-hidden">
              <div class="h-full rounded-full transition-all duration-500"
                :class="wordPercent >= 80 ? 'bg-[var(--color-ok)]' : wordPercent >= 40 ? 'bg-[var(--color-amber)]' : 'bg-[var(--color-rule)]'"
                :style="{ width: `${wordPercent}%` }" />
            </div>
          </div>

          <!-- THE TEXT -->
          <div class="rounded-lg border bg-[var(--color-paper-white)] transition-colors"
            :class="isEditing ? 'border-[var(--color-accent)]/40 shadow-sm' : 'border-[var(--color-rule)]'"
          >
            <!-- Generating spinner -->
            <div v-if="generating" class="flex items-center justify-center py-24">
              <div class="text-center">
                <div class="h-8 w-8 border-2 border-[var(--color-accent)] border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                <p class="text-sm text-[var(--color-ink-light)]">{{ generatingProgress }}</p>
              </div>
            </div>

            <!-- Editing mode -->
            <div v-else-if="isEditing">
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
                <transition name="ghost-hint">
                  <div v-if="ghostText && !isSpinning"
                    class="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-md bg-[var(--color-paper-warm)] border border-[var(--color-rule)] px-2.5 py-1 text-xs text-[var(--color-ink-muted)] shadow-sm pointer-events-none select-none">
                    <kbd class="font-[var(--font-mono)] text-[var(--color-accent)] font-semibold">Tab</kbd>
                    <span>принять</span>
                  </div>
                </transition>
              </div>
              <div class="flex items-center gap-3 border-t border-[var(--color-rule-light)] px-6 py-2.5">
                <transition name="ghost-hint">
                  <span v-if="saveStatus === 'saved'" class="text-xs text-[var(--color-ok)]">Сохранено ✓</span>
                </transition>
                <div class="flex-1" />
                <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">{{ editWordCount }} слов</span>
                <button
                  @click="ghostEnabled = !ghostEnabled"
                  :title="ghostEnabled ? 'Выключить kAI автодополнение' : 'Включить kAI автодополнение'"
                  class="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold tracking-tight transition-all duration-150"
                  :class="ghostEnabled
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent-pale)] text-[var(--color-accent)]'
                    : 'border-[var(--color-rule)] bg-transparent text-[var(--color-ink-muted)]'"
                >
                  <span
                    class="h-[7px] w-[7px] rounded-full border transition-all duration-150 flex-shrink-0"
                    :class="ghostEnabled
                      ? 'bg-[var(--color-accent)] border-[var(--color-accent)]'
                      : 'bg-transparent border-[var(--color-ink-muted)]'"
                  />
                  kAI
                </button>
                <button @click="closeEditor" class="rounded-md px-3 py-1.5 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">Закрыть</button>
              </div>
            </div>

            <!-- Display mode -->
            <div v-else-if="currentText" class="relative group">
              <div class="draft-prose px-8 py-6 text-[15px] text-[var(--color-ink)] leading-[1.8] font-[var(--font-body)] cursor-text"
                @click="startEditing"
                v-html="renderDraft(currentText)" />
              <div class="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button @click.stop="startEditing"
                  class="flex items-center gap-1 rounded-md bg-white/90 border border-[var(--color-rule)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent-pale)] transition-colors shadow-sm">
                  Редактировать
                </button>
                <button @click.stop="generateDraft"
                  class="rounded-md bg-white/90 border border-[var(--color-rule)] p-1.5 text-[var(--color-ink-muted)] hover:text-[var(--color-accent)] transition-colors shadow-sm"
                  title="Перегенерировать">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-3.5 w-3.5">
                    <path fill-rule="evenodd" d="M13.836 2.477a.75.75 0 0 1 .75.75v3.182a.75.75 0 0 1-.75.75h-3.182a.75.75 0 0 1 0-1.5h1.37l-.84-.841a4.5 4.5 0 0 0-7.08.932.75.75 0 0 1-1.3-.75 6 6 0 0 1 9.44-1.242l.842.84V3.227a.75.75 0 0 1 .75-.75Zm-.911 7.5A.75.75 0 0 1 13.199 11a6 6 0 0 1-9.44 1.241l-.84-.84v1.371a.75.75 0 0 1-1.5 0V9.591a.75.75 0 0 1 .75-.75H5.35a.75.75 0 0 1 0 1.5H3.98l.841.841a4.5 4.5 0 0 0 7.08-.932.75.75 0 0 1 1.025-.273Z" clip-rule="evenodd" />
                  </svg>
                </button>
              </div>
            </div>

            <!-- Empty state -->
            <div v-else class="flex items-center justify-center py-20">
              <div class="text-center">
                <p class="text-sm text-[var(--color-ink-muted)] mb-4">{{ block.fragments.length }} фрагментов привязано</p>
                <div class="flex items-center justify-center gap-3">
                  <button @click="generateDraft" class="rounded-md bg-[var(--color-accent)] px-5 py-2.5 text-sm font-medium text-white hover:bg-[var(--color-accent-deep)] transition-colors">Написать блок</button>
                  <button @click="editingBody = ''; isEditing = true" class="rounded-md border border-[var(--color-rule)] px-4 py-2.5 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">Писать самому</button>
                </div>
              </div>
            </div>
          </div>

          <!-- AI ASSISTANT STRIP — always visible under text -->
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

        <!-- RIGHT: Fragments panel (1/4) — demo mode -->
        <div class="lg:col-span-1 flex flex-col" style="max-height: calc(100vh - 9rem);">
          <!-- Search -->
          <div class="mb-2 flex-shrink-0">
            <div class="relative">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-ink-muted)]">
                <path fill-rule="evenodd" d="M9.965 11.026a5 5 0 1 1 1.06-1.06l2.755 2.754a.75.75 0 1 1-1.06 1.06l-2.755-2.754ZM10.5 7a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0Z" clip-rule="evenodd" />
              </svg>
              <input v-model="searchQuery" @input="onSearchInput" type="text"
                class="w-full rounded-md border border-[var(--color-rule)] bg-[var(--color-paper)] pl-8 pr-3 py-1.5 text-sm placeholder-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:outline-none"
                placeholder="Найти в библиотеке..." />
            </div>
          </div>
          <!-- Search results -->
          <div v-if="searchQuery.trim() && (searchResults.length > 0 || searching)" class="mb-2 flex-shrink-0">
            <div class="rounded-md border border-[var(--color-accent)]/30 bg-[var(--color-accent-pale)]/20 overflow-hidden">
              <div class="px-3 py-1 text-xs font-semibold text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]/40">Результаты</div>
              <div v-if="searching" class="px-3 py-3 text-center text-xs text-[var(--color-ink-muted)]">Ищу...</div>
              <div v-else-if="searchResults.length === 0" class="px-3 py-2 text-center text-xs text-[var(--color-ink-muted)]">Не найдено</div>
              <div v-else class="divide-y divide-[var(--color-rule-light)]">
                <div v-for="r in searchResults" :key="r.source" class="px-3 py-2 hover:bg-[var(--color-accent-pale)]/40">
                  <div class="flex items-center gap-1 mb-0.5">
                    <a :href="`/demo/library/${r.source}`" class="citekey-link" @click.prevent>@{{ r.source.length > 14 ? r.source.slice(0, 14) + '..' : r.source }}</a>
                    <span class="text-xs text-[var(--color-ink-muted)]">{{ r.year }}</span>
                    <button @click="attachFragment(r)"
                      class="ml-auto rounded bg-[var(--color-accent)] px-1.5 py-0.5 text-xs font-medium text-white hover:bg-[var(--color-accent-deep)]">+</button>
                  </div>
                  <p class="text-xs text-[var(--color-ink-light)] leading-relaxed line-clamp-2">{{ r.text }}</p>
                </div>
              </div>
            </div>
          </div>
          <!-- Attached fragments -->
          <div class="flex-1 overflow-y-auto space-y-2">
            <div class="text-xs font-semibold text-[var(--color-ink-muted)] uppercase tracking-wider px-1">
              Привязано ({{ block.fragments.length }})
            </div>
            <div v-for="(f, i) in block.fragments" :key="f.source + i"
              class="group rounded-md border border-[var(--color-rule-light)] bg-[var(--color-paper-white)] px-3 py-2 relative">
              <div class="flex items-center gap-1 mb-0.5">
                <a :href="`/demo/library/${f.source}`" class="citekey-link">@{{ f.source.length > 14 ? f.source.slice(0, 14) + '..' : f.source }}</a>
                <span class="text-xs text-[var(--color-ink-muted)]">{{ f.year }}</span>
                <span class="ml-auto text-xs" :style="{ color: intentLabels[f.intent]?.color }">{{ intentLabels[f.intent]?.label }}</span>
              </div>
              <p class="text-xs text-[var(--color-ink-light)] leading-relaxed">{{ f.text }}</p>
              <button @click="detachFragment(i)"
                class="absolute top-1.5 right-1.5 h-4 w-4 rounded flex items-center justify-center text-[var(--color-ink-muted)] hover:text-[var(--color-err)] hover:bg-[var(--color-err-bg)] opacity-0 group-hover:opacity-100 transition-all"
                title="Отвязать">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-2.5 w-2.5">
                  <path d="M5.28 4.22a.75.75 0 0 0-1.06 1.06L6.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L8 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L9.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L8 6.94 5.28 4.22Z" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.ghost-text {
  color: var(--color-ink-muted);
  opacity: 0.38;
  pointer-events: none;
  user-select: none;
}

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

/* Prose styles for rendered draft markdown */
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
