<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { formatMarkdown } from '@/utils/markdown'

const route = useRoute()
const router = useRouter()

// ── Mock data ────────────────────────────────────────────────────────────

const section = ref({
  id: '1.1',
  name: 'Потребности пользователей НГГМИ',
  thesis: 'Рост грузопотока по СМП создаёт потребность в оперативных ледовых прогнозах',
})

const block = ref({
  id: 'b1.1.1',
  title: 'Стратегическое значение СМП и рост грузопотока',
  wordTarget: 350,
  status: 'draft' as 'draft' | 'outlined' | 'empty',
  draftText: 'Северный морской путь (СМП) является исторически сложившейся транспортной артерией России в Арктике. Транспортная стратегия РФ до 2035 года определяет СМП как «единую национальную транспортную коммуникацию», а план развития инфраструктуры предусматривает увеличение грузопотока до 80 млн тонн к 2024 году и 150 млн тонн к 2030 году.',
  generatedText: '',
  fragments: [
    { source: 'zhuravelSevernyyMorckoyPut2020', sourceTitle: 'Северный морской путь: проблемы, возможности, решения', year: 2020, text: 'Грузопоток по СМП вырос с 7,5 млн тонн в 2016 г. до 34,9 млн тонн в 2022 г., что связано с развитием проектов «Ямал СПГ» и «Арктик СПГ-2».', intent: 'background', page: 12 },
    { source: 'kuvatovPotencialSevernogoMorskogo2014', sourceTitle: 'Потенциал Северного морского пути', year: 2014, text: 'Экономический потенциал СМП определяется сокращением длины маршрута из Европы в Восточную Азию на 30-40% по сравнению с маршрутом через Суэцкий канал.', intent: 'background', page: 45 },
    { source: 'Angudovich2025', sourceTitle: 'Перспективы развития арктического судоходства', year: 2025, text: 'Несмотря на рост грузоперевозок, навигационные риски остаются основным сдерживающим фактором: ледовые условия в переходные сезоны создают наибольшую неопределённость для планирования маршрутов.', intent: 'background', page: 3 },
    { source: 'olhovikINFORMATIONMODELMARITIME2018', sourceTitle: 'Information Model of Maritime Activities in the Arctic Zone', year: 2018, text: 'The non-fixed nature of the Northern Sea Route necessitates real-time monitoring of ice conditions — unlike fixed waterways, vessels must continuously adapt routes based on current ice situation.', intent: 'method', page: 8 },
    { source: 'RasporyazheniePravitelstvaRossiyskoy', sourceTitle: 'Транспортная стратегия РФ до 2035 года', year: 2021, text: 'СМП определяется как «единая национальная транспортная коммуникация в Арктике»; предусматривается круглогодичная навигация к 2030 году.', intent: 'background', page: null },
  ],
  prevBlock: null as { id: string; title: string } | null,
  nextBlock: { id: 'b1.1.2', title: 'Потребности навигации в ледовой информации' },
})

const chapter = ref({
  number: 1,
  thesis: 'Существующие системы НГО не обеспечивают достаточную точность прогнозов',
})

// ── Writing ──────────────────────────────────────────────────────────────

const isEditing = ref(false)
const editText = ref('')
const generating = ref(false)
const generatingProgress = ref('')

const currentText = computed(() => block.value.generatedText || block.value.draftText)
const wordCount = computed(() => currentText.value ? currentText.value.trim().split(/\s+/).filter(Boolean).length : 0)
const wordPercent = computed(() => Math.min(Math.round((wordCount.value / block.value.wordTarget) * 100), 100))
const editWordCount = computed(() => editText.value ? editText.value.trim().split(/\s+/).filter(Boolean).length : 0)

function startEditing() {
  editText.value = currentText.value
  isEditing.value = true
  nextTick(() => {
    const el = editorEl.value
    if (!el) return
    el.innerText = currentText.value
    el.focus()
    moveCursorToEnd(el)
  })
}

function commitText() {
  // editText is always ghost-free — updated by onEditorInput before ghost appears
  block.value.generatedText = editText.value
  block.value.draftText = editText.value
  block.value.status = 'draft'
}

function autoSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    commitText()
    saveStatus.value = 'saved'
    setTimeout(() => { if (saveStatus.value === 'saved') saveStatus.value = 'idle' }, 2500)
  }, 800)
}

function closeEditor() {
  clearGhost() // removes ghost from DOM, editText stays clean
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  commitText() // reads editText.value — always safe
  saveStatus.value = 'idle'
  isEditing.value = false
}

async function generateDraft() {
  generating.value = true
  generatingProgress.value = 'Собираю контекст цепочки...'
  pushAssistantMessage('Начинаю генерацию. Контекст: тезис главы 1, тезис раздела 1.1, 5 привязанных фрагментов.')
  await new Promise(r => setTimeout(r, 800))
  generatingProgress.value = `Анализирую ${block.value.fragments.length} фрагментов...`
  await new Promise(r => setTimeout(r, 600))
  generatingProgress.value = `Генерирую текст (~${block.value.wordTarget} слов)...`
  await new Promise(r => setTimeout(r, 2000))

  block.value.generatedText = `Северный морской путь (СМП) является исторически сложившейся транспортной артерией России в Арктике, стратегическое значение которой определяется Транспортной стратегией РФ до 2035 года как «единой национальной транспортной коммуникации» [@RasporyazheniePravitelstvaRossiyskoy]. Грузопоток по СМП демонстрирует устойчивый рост: с 7,5 млн тонн в 2016 году до 34,9 млн тонн в 2022 году, что связано прежде всего с развитием проектов «Ямал СПГ» и «Арктик СПГ-2» [@zhuravelSevernyyMorckoyPut2020]. Фактический грузопоток в 2023 году составил 36,2 млн тонн, что подтверждает устойчивую тенденцию, хотя и с отставанием от плановых показателей [@alekseevaVliyanieIntensivnogoSudohodstva2024].

Экономический потенциал СМП определяется сокращением длины маршрута из Европы в Восточную Азию на 30–40% по сравнению с традиционным маршрутом через Суэцкий канал [@kuvatovPotencialSevernogoMorskogo2014]. Вместе с тем навигационные риски остаются основным сдерживающим фактором развития арктического судоходства: ледовые условия в переходные сезоны (октябрь–ноябрь, май–июнь) создают наибольшую неопределённость для планирования маршрутов [@Angudovich2025].

Принципиальной особенностью СМП, отличающей его от фиксированных водных путей, является нефиксированность трассы: суда вынуждены непрерывно адаптировать маршрут в зависимости от текущей ледовой обстановки [@olhovikINFORMATIONMODELMARITIME2018]. Это обстоятельство формирует потребность в оперативном мониторинге и прогнозировании ледовой обстановки, что является отправной точкой настоящего исследования.`

  block.value.status = 'draft'
  generating.value = false
  generatingProgress.value = ''
  pushAssistantMessage(`Готово — ${block.value.generatedText.split(/\s+/).length} слов, ${block.value.fragments.length} источников. Нажмите на текст чтобы отредактировать.`)
}

// ── Ghost text (inline AI continuation) ──────────────────────────────────

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
  editText.value = el.innerText
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
  const text = editText.value.trim()
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
  editText.value = editorEl.value?.innerText || ''
  autoSave()
  if (editText.value.trim().length < 30) return
  ghostTimer = setTimeout(fetchGhostSuggestion, 700)
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
  editText.value = editorEl.value?.innerText || ''
}

// ── Fragment search ──────────────────────────────────────────────────────

const searchQuery = ref('')
const searchResults = ref<typeof block.value.fragments>([])
const searching = ref(false)

const mockLibrary = [
  { source: 'alekseevaVliyanieIntensivnogoSudohodstva2024', sourceTitle: 'Влияние интенсивного судоходства на экологию АЗРФ', year: 2024, text: 'Фактический грузопоток по СМП в 2023 году составил 36,2 млн тонн, увеличившись на 3,7% по сравнению с 2022 годом.', intent: 'background', page: 7 },
  { source: 'butakovRezultatyPrognozaGidrometeorologicheskih2025', sourceTitle: 'Результаты прогноза гидрометеорологических условий', year: 2025, text: 'Прогноз грузопотока по СМП на 2025 год — свыше 40 млн тонн при условии ввода в эксплуатацию третьей линии «Арктик СПГ-2».', intent: 'background', page: 15 },
  { source: 'massonnet2012', sourceTitle: 'A model study of the impact of sea ice on Arctic climate', year: 2012, text: 'Баренцево море демонстрирует аномально высокую предсказуемость (skill score 0.7 на горизонте 4 мес) по сравнению с Чукотским (0.3 на 2 мес).', intent: 'result_comparison', page: 1204 },
  { source: 'smirnovMonitoringFizikomehanicheskogoSostoyaniya2020', sourceTitle: 'Мониторинг физико-механического состояния ледяного покрова', year: 2020, text: 'Существующая сеть гидрометеорологических наблюдений на побережье АЗРФ включает 48 станций, из которых 12 оснащены автоматическими средствами.', intent: 'background', page: 33 },
  { source: 'bidenkoGeoinformacionnayaProceduraOcenki2022', sourceTitle: 'Геоинформационная процедура оценки ледовой обстановки', year: 2022, text: 'Предлагается процедура валидации, включающая три этапа: предобработку спутниковых данных, пространственное сопоставление и расчёт метрик.', intent: 'method', page: 89 },
]

let searchTimeout: ReturnType<typeof setTimeout> | null = null
function onSearchInput() {
  if (searchTimeout) clearTimeout(searchTimeout)
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) { searchResults.value = []; return }
  searching.value = true
  searchTimeout = setTimeout(() => {
    searchResults.value = mockLibrary.filter(f =>
      !block.value.fragments.some(bf => bf.source === f.source) &&
      (f.sourceTitle.toLowerCase().includes(q) || f.source.toLowerCase().includes(q) || f.text.toLowerCase().includes(q))
    )
    searching.value = false
  }, 300)
}

function attachFragment(f: typeof mockLibrary[0]) {
  block.value.fragments.push(f)
  searchResults.value = searchResults.value.filter(r => r.source !== f.source)
  pushAssistantMessage(`Привязан [@${f.source}] (${f.year}). Теперь ${block.value.fragments.length} фрагментов — можно перегенерировать текст чтобы учесть новый источник.`)
}

function detachFragment(index: number) {
  const removed = block.value.fragments[index]
  block.value.fragments.splice(index, 1)
  pushAssistantMessage(`Отвязан @${removed.source}. Если он упоминается в тексте — стоит убрать ссылку вручную.`)
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

onMounted(() => {
  pushAssistantMessage(`Блок «${block.value.title}» — ${block.value.fragments.length} фрагментов привязано. ${currentText.value ? `Черновик: ${wordCount.value}/${block.value.wordTarget} слов.` : 'Текст не написан — нажмите «Написать блок».'}`)
})

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
      <!-- Breadcrumb + nav -->
      <div class="flex items-center gap-1.5 text-sm mb-3 flex-wrap">
        <button @click="router.push('/demo/map')" class="text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">Карта</button>
        <span class="text-[var(--color-rule)]">/</span>
        <button @click="router.push('/demo/map')" class="text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">Гл. {{ chapter.number }}</button>
        <span class="text-[var(--color-rule)]">/</span>
        <button @click="router.push('/demo/map')" class="text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">{{ section.id }}</button>
        <span class="text-[var(--color-rule)]">/</span>
        <span class="text-[var(--color-ink)] font-medium truncate">{{ block.title }}</span>
        <div class="flex-1" />
        <button v-if="block.prevBlock" class="rounded-md px-2 py-1 text-xs text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)]">&larr; Пред</button>
        <button v-if="block.nextBlock" class="rounded-md px-2 py-1 text-xs text-[var(--color-ink-muted)] hover:bg-[var(--color-rule-light)]">След &rarr;</button>
      </div>

      <!-- Chain context bar -->
      <div class="flex items-center gap-3 rounded-md bg-[var(--color-paper-warm)] px-4 py-2 mb-4 text-xs text-[var(--color-ink-muted)]">
        <span><strong class="text-[var(--color-accent)]">Гл. {{ chapter.number }}:</strong> <em>{{ chapter.thesis }}</em></span>
        <span class="text-[var(--color-rule)]">&rarr;</span>
        <span><strong class="text-[var(--color-accent)]">{{ section.id }}:</strong> <em>{{ section.thesis }}</em></span>
      </div>

      <!-- MAIN LAYOUT: text + assistant left, fragments right -->
      <div class="grid grid-cols-1 lg:grid-cols-4 gap-4">

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
                <!-- Autosave status -->
                <transition name="ghost-hint">
                  <span v-if="saveStatus === 'saved'" class="text-xs text-[var(--color-ok)]">Сохранено ✓</span>
                </transition>
                <div class="flex-1" />
                <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">{{ editWordCount }} слов</span>
                <button @click="closeEditor" class="rounded-md px-3 py-1.5 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">Закрыть</button>
              </div>
            </div>

            <!-- Display mode -->
            <div v-else-if="currentText" class="relative group">
              <div class="px-8 py-6 text-[15px] text-[var(--color-ink)] leading-[1.8] font-[var(--font-body)] cursor-text"
                @click="startEditing"
                v-html="formatMarkdown(currentText)" />
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
                  <button @click="editText = ''; isEditing = true" class="rounded-md border border-[var(--color-rule)] px-4 py-2.5 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors">Писать самому</button>
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
            <!-- User input -->
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

        <!-- RIGHT: Fragments panel (1/4) — always visible -->
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
</style>
