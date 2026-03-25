<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { ref, onMounted, onUnmounted } from 'vue'

const openFaq = ref<number | null>(null)

function toggleFaq(index: number) {
  openFaq.value = openFaq.value === index ? null : index
}

/* Scroll-triggered reveals */
let observer: IntersectionObserver | null = null

onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add('visible')
        }
      })
    },
    { threshold: 0.15 }
  )
  document.querySelectorAll('.reveal, .stagger-children').forEach((el) => {
    observer!.observe(el)
  })
})

onUnmounted(() => {
  observer?.disconnect()
})

const faqs = [
  {
    q: 'Это инструмент для написания научной работы за меня?',
    a: 'Нет. LitResearch — инструмент для работы с научной литературой: извлечение фрагментов, анализ покрытия, обнаружение пробелов. Он помогает систематизировать источники и генерирует черновики обзора, но не пишет научную работу и не предназначен для обхода систем антиплагиата.',
  },
  {
    q: 'Чем это отличается от ChatGPT / Claude для работы с литературой?',
    a: 'ChatGPT и Claude генерируют ответы из обучающих данных и могут выдумывать ссылки. LitResearch работает только с вашей библиотекой — каждый фрагмент и каждая цитата привязаны к реальному PDF, который вы загрузили.',
  },
  {
    q: 'Нужен ли Zotero для работы?',
    a: 'Нет. В веб-версии вы загружаете PDF напрямую. CLI-версия (open source) поддерживает интеграцию с Zotero для тех, кто уже его использует.',
  },
  {
    q: 'Какие AI-модели используются?',
    a: 'Поддерживается более 100 провайдеров через LiteLLM: Claude, GPT, Qwen, Gemini, локальные модели через Ollama. Вы выбираете модель, которая вам подходит.',
  },
  {
    q: 'Сколько это стоит?',
    a: 'CLI-версия Klemma — open source. Веб-версия LitResearch сейчас в закрытом доступе — запишитесь на личный демо через Telegram.',
  },
  {
    q: 'В безопасности ли мои данные?',
    a: 'Ваши PDF хранятся на сервере и не передаются третьим лицам. Для AI-обработки отправляется только текст — без метаданных и файлов целиком.',
  },
]
</script>

<template>
  <div class="relative min-h-screen bg-paper">
    <!-- Nav -->
    <nav class="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
      <span class="font-display text-2xl font-semibold text-ink">LitResearch</span>
      <div class="flex items-center gap-4">
        <RouterLink to="/login" class="text-base font-medium text-ink-muted hover:text-ink transition-colors">
          Войти
        </RouterLink>
        <RouterLink
          to="/register"
          class="rounded-lg bg-cta px-5 py-2.5 text-base font-semibold text-white hover:bg-cta-light transition-colors"
        >
          Записаться
        </RouterLink>
      </div>
    </nav>

    <!-- Hero -->
    <header class="relative mx-auto max-w-4xl px-6 pt-20 pb-24 text-center">
      <!-- Decorative polar circles -->
      <div class="pointer-events-none absolute top-8 left-1/2 -translate-x-1/2 h-[480px] w-[480px] rounded-full border border-rule-light opacity-40 float"></div>
      <div class="pointer-events-none absolute top-20 left-1/2 -translate-x-1/2 h-[360px] w-[360px] rounded-full border border-amber opacity-20 float-delayed"></div>

      <div class="relative">
        <p class="animate-in font-mono text-sm tracking-widest text-accent uppercase">
          Инфраструктура для исследователей
        </p>
        <h1 class="animate-in animate-in-delay-1 mt-5 font-display text-5xl leading-tight font-bold tracking-tight text-ink sm:text-6xl">
          Каких статей не хватает<br />в вашей научной работе?
        </h1>
        <p class="animate-in animate-in-delay-2 mx-auto mt-8 max-w-2xl text-xl leading-relaxed text-ink-light">
          LitResearch анализирует библиографии ваших источников, находит пробелы
          по главам и предлагает конкретные работы для их закрытия.
          Черновик обзора — с реальными цитатами, не с выдуманными.
        </p>
        <div class="animate-in animate-in-delay-3 mt-12 flex justify-center gap-4">
          <RouterLink
            to="/register"
            class="beam-border rounded-lg bg-cta px-8 py-4 text-base font-semibold text-white shadow-sm hover:bg-cta-light transition-colors"
          >
            Записаться на демо
          </RouterLink>
          <a
            href="#how-it-works"
            class="rounded-lg border border-rule px-8 py-4 text-base font-semibold text-ink-light hover:bg-paper-warm transition-colors"
          >
            Как это работает
          </a>
        </div>
      </div>
    </header>

    <!-- Product screenshot -->
    <section class="relative mx-auto max-w-5xl px-6 pb-16 reveal">
      <div class="rounded-xl border border-rule bg-paper-white shadow-lg overflow-hidden">
        <!-- Browser chrome -->
        <div class="flex items-center gap-2 border-b border-rule bg-paper-warm px-4 py-2.5">
          <span class="h-3 w-3 rounded-full bg-err/40"></span>
          <span class="h-3 w-3 rounded-full bg-warn/40"></span>
          <span class="h-3 w-3 rounded-full bg-ok/40"></span>
          <span class="ml-3 flex-1 rounded-md bg-paper px-3 py-1 text-xs text-ink-muted font-mono">litresearch.ru/health</span>
        </div>
        <img
          src="/screenshot-health.png"
          alt="LitResearch — экран здоровья библиотеки: общая оценка, покрытие по главам, статистика источников"
          class="w-full"
          loading="eager"
        />
      </div>
    </section>

    <!-- Value bar -->
    <section class="relative border-y border-rule bg-paper-warm grain">
      <div class="relative mx-auto grid max-w-5xl grid-cols-2 gap-8 px-6 py-10 sm:grid-cols-4 stagger-children">
        <div class="text-center">
          <div class="font-display text-base font-bold text-accent">Только реальные ссылки</div>
          <div class="mt-1 text-sm text-ink-muted">из вашей библиотеки</div>
        </div>
        <div class="text-center">
          <div class="font-display text-base font-bold text-amber">Покрытие по главам</div>
          <div class="mt-1 text-sm text-ink-muted">пробелы видны сразу</div>
        </div>
        <div class="text-center">
          <div class="font-display text-base font-bold text-violet">100+ AI-моделей</div>
          <div class="mt-1 text-sm text-ink-muted">Claude, GPT, Qwen, Ollama</div>
        </div>
        <div class="text-center">
          <div class="font-display text-base font-bold text-accent">1M токенов бесплатно</div>
          <div class="mt-1 text-sm text-ink-muted">CLI — open source</div>
        </div>
      </div>
    </section>

    <!-- Problem -->
    <section class="reveal mx-auto max-w-3xl px-6 py-20">
      <p class="font-mono text-sm tracking-widest text-accent uppercase">Проблема</p>
      <h2 class="mt-3 font-display text-3xl font-bold text-ink">Знакомая ситуация?</h2>
      <div class="mt-8 space-y-6 text-ink-light text-lg leading-relaxed">
        <p>
          Вы скачали 80 статей по теме. Прочитали двадцать. Выписали цитаты в заметки —
          но через неделю не можете найти, где именно был тот аргумент про метод валидации.
          Научный руководитель спрашивает: «А где у тебя методологические ссылки в третьей
          главе?» — и вы не знаете ответа.
        </p>
        <p>
          Zotero хранит PDF, но не извлекает из них знания. Semantic Scholar ищет
          по 225 миллионам статей, но не знает структуру вашей работы. ChatGPT
          генерирует красивые обзоры — с выдуманными ссылками.
        </p>
        <p class="rounded-lg border-l-2 border-amber bg-amber-pale/30 py-4 pl-5 pr-4 text-base font-medium text-ink">
          Ни один существующий инструмент не совмещает извлечение фрагментов
          с классификацией цитирования и отслеживание пробелов по разделам.
        </p>
      </div>
    </section>

    <!-- How it works -->
    <section id="how-it-works" class="relative border-y border-rule bg-paper-warm grain py-20">
      <div class="relative mx-auto max-w-4xl px-6">
        <p class="text-center font-mono text-sm tracking-widest text-accent uppercase">Процесс</p>
        <h2 class="mt-3 text-center font-display text-3xl font-bold text-ink">Три шага</h2>
        <div class="mt-14 grid grid-cols-1 gap-12 md:grid-cols-3 stagger-children">
          <div class="group text-center">
            <div
              class="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-accent font-display text-2xl font-bold text-white shadow-sm transition-transform group-hover:scale-110"
            >
              01
            </div>
            <h3 class="mt-5 font-display text-xl font-semibold text-ink">Загрузите PDF</h3>
            <p class="mt-3 text-base leading-relaxed text-ink-muted">
              Перетащите файлы статей в проект. Без Zotero, без настроек — просто PDF.
            </p>
          </div>
          <div class="group text-center">
            <div
              class="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-amber font-display text-2xl font-bold text-white shadow-sm transition-transform group-hover:scale-110"
            >
              02
            </div>
            <h3 class="mt-5 font-display text-xl font-semibold text-ink">Получите цитаты</h3>
            <p class="mt-3 text-base leading-relaxed text-ink-muted">
              AI извлекает цитатные фрагменты, классифицирует тип цитирования
              и привязывает каждый к разделу вашей работы.
            </p>
          </div>
          <div class="group text-center">
            <div
              class="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-violet font-display text-2xl font-bold text-white shadow-sm transition-transform group-hover:scale-110"
            >
              03
            </div>
            <h3 class="mt-5 font-display text-xl font-semibold text-ink">Закройте пробелы</h3>
            <p class="mt-3 text-base leading-relaxed text-ink-muted">
              Покрытие по главам, недостающие типы ссылок, рекомендации
              конкретных работ для закрытия пробелов.
            </p>
          </div>
        </div>
      </div>
    </section>

    <!-- Key benefits -->
    <section class="mx-auto max-w-4xl px-6 py-20">
      <div class="reveal">
        <p class="text-center font-mono text-sm tracking-widest text-accent uppercase">Ценность</p>
        <h2 class="mt-3 text-center font-display text-3xl font-bold text-ink">
          Что вы получаете
        </h2>
      </div>
      <div class="mt-12 grid grid-cols-1 gap-6 md:grid-cols-2 stagger-children">
        <div class="hover-lift rounded-lg border border-rule bg-paper-white p-7">
          <div class="flex items-center gap-3">
            <div class="flex h-9 w-9 items-center justify-center rounded-md bg-accent-pale text-sm font-bold text-accent-deep font-mono">01</div>
            <h3 class="font-display text-lg font-semibold text-ink">
              Обзор литературы за час, не за семестр
            </h3>
          </div>
          <p class="mt-4 text-base leading-relaxed text-ink-muted">
            Каждая цитата автоматически получает тип (фон, метод, сравнение результатов),
            оценку качества и привязку к разделу вашей работы. Ручная работа над
            обзором литературы сокращается в разы.
          </p>
        </div>
        <div class="hover-lift rounded-lg border border-rule bg-paper-white p-7">
          <div class="flex items-center gap-3">
            <div class="flex h-9 w-9 items-center justify-center rounded-md bg-amber-pale text-sm font-bold text-amber-deep font-mono">02</div>
            <h3 class="font-display text-lg font-semibold text-ink">
              Пробелы видны до научрука
            </h3>
          </div>
          <p class="mt-4 text-base leading-relaxed text-ink-muted">
            Система анализирует библиографии ваших источников, находит самые цитируемые
            работы, которых нет в вашей библиотеке, и помогает добавить их.
            Методологические пробелы приоритизируются выше фоновых.
          </p>
        </div>
        <div class="hover-lift rounded-lg border border-rule bg-paper-white p-7">
          <div class="flex items-center gap-3">
            <div class="flex h-9 w-9 items-center justify-center rounded-md bg-violet-pale text-sm font-bold text-violet-deep font-mono">03</div>
            <h3 class="font-display text-lg font-semibold text-ink">
              Черновик с реальными цитатами
            </h3>
          </div>
          <p class="mt-4 text-base leading-relaxed text-ink-muted">
            LitResearch работает только с вашей библиотекой. Каждая ссылка
            ведёт к конкретному PDF. Никаких выдуманных источников —
            это принципиальное архитектурное решение.
          </p>
        </div>
        <div class="hover-lift rounded-lg border border-rule bg-paper-white p-7">
          <div class="flex items-center gap-3">
            <div class="flex h-9 w-9 items-center justify-center rounded-md bg-accent-pale text-sm font-bold text-accent-deep font-mono">04</div>
            <h3 class="font-display text-lg font-semibold text-ink">
              Брифинги по разделам
            </h3>
          </div>
          <p class="mt-4 text-base leading-relaxed text-ink-muted">
            Исследовательские брифинги по каждой главе: какие источники покрывают тему,
            какова структура аргументации, что ещё нужно прочитать.
            Обновляются постепенно при добавлении новых источников.
          </p>
        </div>
      </div>
    </section>

    <!-- Complements, not replaces -->
    <section class="relative border-y border-rule bg-paper-warm grain py-20">
      <div class="relative mx-auto max-w-3xl px-6">
        <div class="reveal">
          <p class="text-center font-mono text-sm tracking-widest text-accent uppercase">Позиционирование</p>
          <h2 class="mt-3 text-center font-display text-3xl font-bold text-ink">
            Дополняет, а не заменяет
          </h2>
          <p class="mt-4 text-center text-base text-ink-muted">
            LitResearch — инструмент для работы с научной литературой,<br class="hidden sm:inline" />
            не для написания научных работ и не для обхода антиплагиата.
          </p>
        </div>
        <div class="mt-10 space-y-4 stagger-children">
          <div class="hover-lift flex items-start gap-4 rounded-lg border border-rule bg-paper-white p-6">
            <div class="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-pale font-mono text-sm font-bold text-accent-deep">
              Z
            </div>
            <div>
              <h3 class="text-base font-semibold text-ink">Zotero хранит ваши PDF</h3>
              <p class="mt-1 text-base text-ink-muted">
                LitResearch извлекает из них знания: фрагменты, классификацию
                цитирования, привязку к главам. Zotero остаётся вашей библиотекой.
              </p>
            </div>
          </div>
          <div class="hover-lift flex items-start gap-4 rounded-lg border border-rule bg-paper-white p-6">
            <div class="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-pale font-mono text-sm font-bold text-amber-deep">
              S2
            </div>
            <div>
              <h3 class="text-base font-semibold text-ink">Semantic Scholar ищет по 225М статей</h3>
              <p class="mt-1 text-base text-ink-muted">
                LitResearch использует его API для разрешения пробелов:
                когда система находит недостающую работу, S2 помогает получить её метаданные и PDF.
              </p>
            </div>
          </div>
          <div class="hover-lift flex items-start gap-4 rounded-lg border border-rule bg-paper-white p-6">
            <div class="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-violet-pale font-mono text-sm font-bold text-violet-deep">
              AI
            </div>
            <div>
              <h3 class="text-base font-semibold text-ink">ChatGPT генерирует текст</h3>
              <p class="mt-1 text-base text-ink-muted">
                LitResearch генерирует текст тоже — но только на основе ваших источников.
                Каждая ссылка верифицируема. Это не замена мышлению, а инструмент
                систематизации.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- For whom -->
    <section class="mx-auto max-w-3xl px-6 py-20">
      <div class="reveal">
        <p class="text-center font-mono text-sm tracking-widest text-accent uppercase">Аудитория</p>
        <h2 class="mt-3 text-center font-display text-3xl font-bold text-ink">Для кого</h2>
      </div>
      <div class="mt-10 space-y-4 stagger-children">
        <div class="hover-lift flex gap-4 rounded-lg border border-rule bg-paper-white p-6">
          <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-accent text-sm font-bold text-white font-mono">
            PhD
          </div>
          <div>
            <h3 class="font-display text-lg font-semibold text-ink">Аспиранты</h3>
            <p class="mt-1 text-base text-ink-muted">
              Диссертация на 100+ источников. Нужно видеть покрытие по главам,
              находить пробелы и генерировать черновики обзора литературы с реальными цитатами.
            </p>
          </div>
        </div>
        <div class="hover-lift flex gap-4 rounded-lg border border-rule bg-paper-white p-6">
          <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-amber text-sm font-bold text-white font-mono">
            R
          </div>
          <div>
            <h3 class="font-display text-lg font-semibold text-ink">Исследователи</h3>
            <p class="mt-1 text-base text-ink-muted">
              Обзорная статья или грант. Быстро обработать десятки статей,
              структурировать аргументацию и убедиться, что ничего не пропущено.
            </p>
          </div>
        </div>
        <div class="hover-lift flex gap-4 rounded-lg border border-rule bg-paper-white p-6">
          <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-violet text-sm font-bold text-white font-mono">
            PI
          </div>
          <div>
            <h3 class="font-display text-lg font-semibold text-ink">Научные руководители</h3>
            <p class="mt-1 text-base text-ink-muted">
              Видеть покрытие литературы в работах аспирантов: какие разделы
              подкреплены, а какие требуют доработки — без перечитывания всего текста.
            </p>
          </div>
        </div>
      </div>
    </section>

    <!-- Under the hood: pipeline -->
    <section class="relative border-y border-rule bg-paper-warm grain py-20">
      <div class="relative mx-auto max-w-4xl px-6">
        <div class="reveal">
          <p class="text-center font-mono text-sm tracking-widest text-accent uppercase">Архитектура</p>
          <h2 class="mt-3 text-center font-display text-3xl font-bold text-ink">
            Что под капотом
          </h2>
          <p class="mt-4 text-center text-base text-ink-muted">
            Каждый шаг процесса опирается на конкретную научную методологию
          </p>
        </div>

        <!-- Pipeline flow -->
        <div class="mt-14 space-y-0 stagger-children">
          <!-- Step 1 -->
          <div class="flex items-stretch gap-5">
            <div class="flex flex-col items-center">
              <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-accent font-mono text-sm font-bold text-white shadow-sm">
                01
              </div>
              <div class="mt-1 w-px grow bg-gradient-to-b from-accent/40 to-rule"></div>
            </div>
            <div class="pb-8">
              <h3 class="text-base font-semibold text-ink">Structure Analysis</h3>
              <p class="mt-1 text-sm leading-relaxed text-ink-muted">
                PDF разбивается на тематические сегменты и риторические ходы.
                <span class="whitespace-nowrap rounded bg-accent-pale/50 px-1.5 py-0.5 font-mono text-xs text-accent-deep">Cohan et al., NAACL 2019</span>
              </p>
            </div>
          </div>

          <!-- Step 2 -->
          <div class="flex items-stretch gap-5">
            <div class="flex flex-col items-center">
              <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-amber font-mono text-sm font-bold text-white shadow-sm">
                02
              </div>
              <div class="mt-1 w-px grow bg-gradient-to-b from-amber/40 to-rule"></div>
            </div>
            <div class="pb-8">
              <h3 class="text-base font-semibold text-ink">Citation Worthiness</h3>
              <p class="mt-1 text-sm leading-relaxed text-ink-muted">
                Каждый сегмент оценивается на «достойность цитирования» (0–1).
                Фильтр по порогу 0.4 отсекает нерелевантный контент.
                <span class="whitespace-nowrap rounded bg-accent-pale/50 px-1.5 py-0.5 font-mono text-xs text-accent-deep">Chen et al., ACM TOSEM 2025</span>
              </p>
            </div>
          </div>

          <!-- Step 3 -->
          <div class="flex items-stretch gap-5">
            <div class="flex flex-col items-center">
              <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-accent font-mono text-sm font-bold text-white shadow-sm">
                03
              </div>
              <div class="mt-1 w-px grow bg-gradient-to-b from-accent/40 to-rule"></div>
            </div>
            <div class="pb-8">
              <h3 class="text-base font-semibold text-ink">Fragment Extraction + Intent</h3>
              <p class="mt-1 text-sm leading-relaxed text-ink-muted">
                Фрагменты с типом цитирования:
                <span class="font-mono text-xs text-accent">background</span>,
                <span class="font-mono text-xs text-accent">method</span>,
                <span class="font-mono text-xs text-accent">result_comparison</span>.
                Методологические весят 3x.
                <span class="whitespace-nowrap rounded bg-accent-pale/50 px-1.5 py-0.5 font-mono text-xs text-accent-deep">SciCite, NAACL 2019</span>
              </p>
            </div>
          </div>

          <!-- Step 4 -->
          <div class="flex items-stretch gap-5">
            <div class="flex flex-col items-center">
              <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-violet font-mono text-sm font-bold text-white shadow-sm">
                04
              </div>
              <div class="mt-1 w-px grow bg-gradient-to-b from-violet/40 to-rule"></div>
            </div>
            <div class="pb-8">
              <h3 class="text-base font-semibold text-ink">Gap Scoring + Semantic Reranking</h3>
              <p class="mt-1 text-sm leading-relaxed text-ink-muted">
                Пробелы ранжируются:
                <span class="font-mono text-xs text-ink-light">freq &times; quality &times; w_section &times; w_intent</span>.
                Переранжирование через SPECTER-эмбеддинги.
                <span class="whitespace-nowrap rounded bg-accent-pale/50 px-1.5 py-0.5 font-mono text-xs text-accent-deep">Cohan et al., ACL 2020</span>
              </p>
            </div>
          </div>

          <!-- Step 5 -->
          <div class="flex items-stretch gap-5">
            <div class="flex flex-col items-center">
              <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-accent font-mono text-sm font-bold text-white shadow-sm">
                05
              </div>
              <div class="mt-1 w-px grow bg-gradient-to-b from-accent/40 to-rule"></div>
            </div>
            <div class="pb-8">
              <h3 class="text-base font-semibold text-ink">Candidate Resolution</h3>
              <p class="mt-1 text-sm leading-relaxed text-ink-muted">
                Для каждого пробела — полные метаданные через CrossRef + Semantic Scholar API.
                Формирование команды для автоматического добавления.
                <span class="whitespace-nowrap rounded bg-accent-pale/50 px-1.5 py-0.5 font-mono text-xs text-accent-deep">Kinney et al., 2023</span>
              </p>
            </div>
          </div>

          <!-- Step 6 -->
          <div class="flex items-start gap-5">
            <div class="flex flex-col items-center">
              <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-amber font-mono text-sm font-bold text-white shadow-sm">
                06
              </div>
            </div>
            <div>
              <h3 class="text-base font-semibold text-ink">Draft Generation (RAG)</h3>
              <p class="mt-1 text-sm leading-relaxed text-ink-muted">
                Черновик через RAG по закрытому корпусу вашей библиотеки.
                Структура: CARS для введений, argument-based группировка для обзоров.
                <span class="whitespace-nowrap rounded bg-accent-pale/50 px-1.5 py-0.5 font-mono text-xs text-accent-deep">Lewis et al., NeurIPS 2020</span>
                <span class="whitespace-nowrap rounded bg-accent-pale/50 px-1.5 py-0.5 font-mono text-xs text-accent-deep">Swales, 1990</span>
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- FAQ -->
    <section class="mx-auto max-w-3xl px-6 py-20">
      <div class="reveal">
        <p class="text-center font-mono text-sm tracking-widest text-accent uppercase">FAQ</p>
        <h2 class="mt-3 text-center font-display text-3xl font-bold text-ink">
          Частые вопросы
        </h2>
      </div>
      <div class="mt-10 divide-y divide-rule">
        <div v-for="(faq, i) in faqs" :key="i" class="py-5">
          <button
            class="flex w-full items-center justify-between text-left group"
            @click="toggleFaq(i)"
          >
            <span class="pr-4 text-base font-medium text-ink group-hover:text-accent transition-colors">{{ faq.q }}</span>
            <span
              class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-rule text-sm text-ink-muted transition-all"
              :class="openFaq === i ? 'rotate-45 bg-accent border-accent text-white' : 'group-hover:border-accent'"
            >+</span>
          </button>
          <p
            v-if="openFaq === i"
            class="mt-3 text-base leading-relaxed text-ink-muted"
          >
            {{ faq.a }}
          </p>
        </div>
      </div>
    </section>

    <!-- Final CTA -->
    <section class="relative border-t border-rule bg-paper-warm grain py-24">
      <!-- Decorative circles -->
      <div class="pointer-events-none absolute bottom-8 right-1/4 h-56 w-56 rounded-full border border-amber opacity-15 float"></div>

      <div class="relative mx-auto max-w-2xl px-6 text-center reveal">
        <h2 class="font-display text-4xl font-bold text-ink sm:text-5xl">
          Перестаньте перечитывать.<br />Начните писать.
        </h2>
        <p class="mt-5 text-lg text-ink-muted">
          Запишитесь на личный демо — настроим проект и покажем результат на ваших PDF.
        </p>
        <div class="mt-10 flex justify-center gap-4">
          <RouterLink
            to="/register"
            class="beam-border rounded-lg bg-cta px-8 py-4 text-base font-semibold text-white shadow-sm hover:bg-cta-light transition-colors"
          >
            Записаться на демо
          </RouterLink>
          <RouterLink
            to="/login"
            class="rounded-lg border border-rule px-8 py-4 text-base font-semibold text-ink-light hover:bg-paper-white transition-colors"
          >
            Войти
          </RouterLink>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-rule py-8 text-center text-sm text-ink-muted">
      LitResearch &copy; 2026. Powered by
      <a href="https://github.com/bolkhovsky/klemma" class="underline hover:text-accent transition-colors">Klemma</a>
      (open source).
    </footer>
  </div>
</template>
