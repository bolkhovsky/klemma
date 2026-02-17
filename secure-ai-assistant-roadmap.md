# Secure AI Assistant — MVP Roadmap (12 недель)

## Упрощённая сборка для среднего бизнеса на базе PocketBase

**Целевая аудитория:** руководители компаний 50–500 человек, собственники бизнеса, руководители отделов
**Главное УТП:** безопасность данных — DLP, контроль доступа, аудит, self-hosted развёртывание
**Принцип:** максимальная простота установки и эксплуатации — один `docker compose up`

---

## 1. Почему PocketBase — правильный выбор

PocketBase — open-source backend в одном бинарнике (Go), который из коробки даёт то, что в enterprise-стеке требует 4–5 отдельных сервисов:

| Что нужно | Enterprise-стек (сложно) | PocketBase (просто) |
|-----------|------------------------|---------------------|
| Аутентификация | Keycloak + python-jose + Redis | Встроено: email/password, OAuth2, MFA |
| Контроль доступа | PyCasbin + PostgreSQL adapter | Встроено: Collection API Rules |
| База данных | PostgreSQL + Alembic миграции | Встроено: SQLite с автомиграциями |
| Файловое хранилище | MinIO / S3 | Встроено: local FS или S3-compatible |
| Admin-панель | Разработка с нуля (React) | Встроено: готовый Dashboard UI |
| REST API | FastAPI + маршрутизация вручную | Встроено: автогенерация REST API |
| Realtime | WebSocket-сервер вручную | Встроено: SSE-подписки |

**Итого:** вместо Docker Compose с 7 контейнерами — **2 контейнера**: PocketBase + AI Engine (Python).

---

## 2. Сравнение с OpenClaw: наши конкурентные преимущества

OpenClaw (194K звёзд на GitHub, TypeScript/Node.js) — самый популярный AI-ассистент 2026 года. Но для бизнеса он категорически непригоден:

**Проблемы OpenClaw, которые мы решаем:**

- **Нет DLP** — агент свободно передаёт PII (персональные данные, ИНН, номера карт) в облачные LLM. Cisco указала, что такие агенты становятся «скрытыми каналами утечки данных». Мы: Presidio-фильтрация на входе и выходе.
- **Нет контроля доступа** — архитектура single-user. Для команды нужны отдельные серверы. Мы: PocketBase Collection Rules с ролями admin/manager/user.
- **Нет аудита** — история хранится как .jsonl без защиты от изменений. Мы: append-only аудит-лог в PocketBase с hash-цепочкой.
- **Plaintext-ключи** — API-ключи, токены хранятся открытым текстом. Малвари RedLine и Lumma уже нацелены на эти файлы. Мы: шифрование секретов, переменные окружения.
- **12% вредоносных плагинов** — аудит Koi Security нашёл 341 вредоносный навык в ClawHub. Мы: никакого маркетплейса — только проверенные интеграции.
- **CVE-2026-25253** — критическая command injection (CVSS 8.8). Около 1000 незащищённых инстансов найдены через Shodan.

**Наш питч для клиента в одном предложении:** «OpenClaw создан для разработчиков-одиночек, наш ассистент — для команд, которым важна безопасность данных.»

---

## 3. Сценарии использования: ценность для бизнеса

### Кому и зачем нужен AI-ассистент

Каждый сценарий ниже — это конкретная боль, которую ассистент решает. Именно эти истории нужно рассказывать клиентам при продаже.

---

### Сценарий 1: «Второй мозг руководителя»

**Персона:** Елена, CEO компании на 200 человек (логистика)
**Боль:** Получает 150+ писем в день, 30+ сообщений в мессенджерах, 5–7 совещаний. К вечеру не помнит, что обещала утром. Критические решения откладываются.

**Как ассистент помогает:**

- Елена диктует мысли в Telegram по дороге на работу: «Надо обсудить с Ивановым задержку поставки из Китая, пересмотреть бюджет IT на Q3, подготовить борд к среде»
- Ассистент структурирует: создаёт задачи, привязывает к проектам, напоминает в нужный момент
- Перед совещанием ассистент готовит summary: «Иванов последний раз отчитывался 2 недели назад, задержка была 14 дней, вот его метрики»
- После совещания — фиксирует решения и обещания из голосового summary

**Ценность:** 2–3 часа в день, которые руководитель тратит на «вспомнить, найти, подготовиться» → освобождаются для стратегических решений.

**Ключевая метрика:** время от получения информации до принятия решения сокращается с дней до часов.

---

### Сценарий 2: «Корпоративная база знаний, которая отвечает»

**Персона:** Алексей, HR-директор (компания, 350 человек)
**Боль:** Сотрудники задают одни и те же вопросы: «Как оформить отпуск?», «Какой у нас ДМС?», «Где шаблон заявления?». HR-команда из 4 человек тратит 40% времени на ответы.

**Как ассистент помогает:**

- В RAG загружены: HR-регламенты, описания бенефитов, шаблоны документов, FAQ, оргструктура
- Сотрудник спрашивает в web UI: «Мне нужен отпуск с 15 по 28 марта. Что делать?»
- Ассистент отвечает на основе документов: пошаговый процесс, ссылки на шаблоны, имя непосредственного руководителя (из оргструктуры)
- Новые сотрудники получают onboarding-бота: «Спроси меня что угодно о компании»
- Вопрос, на который нет ответа в базе → автоматически создаётся тикет для HR

**Ценность:** 40% времени HR-команды (≈ 1.5 FTE) высвобождается. Качество ответов стандартизировано — нет человеческого фактора «забыл / не знал / ответил неточно».

**Ключевая метрика:** количество обращений к HR-команде снижается на 60–70% в первый месяц.

---

### Сценарий 3: «Безопасная аналитика по данным компании»

**Персона:** Дмитрий, финансовый директор (производство, 150 человек)
**Боль:** Данные разбросаны по Excel-файлам, 1С, CRM. Чтобы получить ответ на вопрос «Какая рентабельность по проекту X?», нужно 2 дня работы аналитика.

**Как ассистент помогает:**

- В RAG загружены: финансовые отчёты (PDF), выгрузки из 1С (CSV/Excel), бюджеты
- Дмитрий спрашивает: «Сравни рентабельность по проектам за Q1 и Q2»
- DLP гарантирует: конкретные суммы и имена контрагентов не утекут в облако (hybrid-режим маскирует PII перед отправкой в LLM)
- Ассистент строит сравнительную таблицу на основе загруженных данных
- В аудит-логе видно, кто и когда запрашивал финансовые данные

**Ценность:** ответ на аналитический вопрос — минуты вместо дней. Финдиректор самостоятелен, не зависит от загрузки аналитика.

**Ключевая метрика:** время получения аналитического ответа: 2 дня → 5 минут.

**Критический фактор безопасности:** именно здесь DLP-pipeline становится must-have, а не nice-to-have. Ни один CEO не допустит утечку финансовых данных в облачную LLM.

---

### Сценарий 4: «Подготовка документов и писем»

**Персона:** Марина, руководитель отдела продаж (IT-компания, 80 человек)
**Боль:** Каждую неделю — 3 коммерческих предложения, 5 ответов на тендеры, 10+ деловых писем. Копируется один и тот же текст с ошибками.

**Как ассистент помогает:**

- В RAG: шаблоны коммерческих предложений, кейсы, описания продуктов, ценовая политика, информация о конкурентах
- Марина: «Подготовь КП для банка "Развитие" на внедрение CRM. Они просили интеграцию с 1С и мобильное приложение»
- Ассистент генерирует КП на основе шаблона + контекст банка + релевантные кейсы
- Перед отправкой: DLP проверяет, что в КП нет внутренних цен для других клиентов или данных из NDA-проектов
- История всех КП сохраняется → ассистент учится стилю компании

**Ценность:** время на подготовку КП: 3–4 часа → 30 минут (включая human review).

**Ключевая метрика:** количество отправленных КП в месяц растёт на 40% при том же размере команды.

---

### Сценарий 5: «AI-помощник для малого бизнеса без IT-отдела»

**Персона:** Олег, собственник сети кофеен (6 точек, 45 сотрудников)
**Боль:** Нет IT-отдела, нет CRM, нет аналитика. Всё в голове и в WhatsApp. Не понимает, какая точка прибыльна, а какая убыточна.

**Как ассистент помогает:**

- Установка за 10 минут на арендованный сервер (или VPS за $30/мес)
- Олег загружает: Excel с выручкой по точкам, меню с себестоимостью, расписание смен
- Каждое утро в Telegram: «Доброе утро! Вчера лучшая точка — Арбат (+12% к среднему). Кончается овсяное молоко на Тверской, заказ нужен сегодня»
- Олег голосом: «Посчитай, если я подниму цены на кофе на 15%, сколько я могу потерять клиентов, чтобы остаться в плюсе?» → ассистент считает break-even
- Раз в неделю: автоматический дайджест по бизнесу

**Ценность:** малый бизнес получает «персонального аналитика + секретаря» за $30–50/мес вместо найма сотрудника за $1500+/мес.

**Ключевая метрика:** собственник экономит 5–8 часов в неделю на рутине и впервые видит реальные цифры своего бизнеса.

---

### Сценарий 6: «Безопасное пространство для стратегических обсуждений»

**Персона:** Совет директоров, компания на 400 человек
**Боль:** Стратегические вопросы (M&A, реструктуризация, новые рынки) нельзя обсуждать с ChatGPT — утечка данных недопустима. Пользуются дорогими консультантами ($500+/час).

**Как ассистент помогает:**

- Air-gapped инсталляция на сервере компании — данные никогда не покидают периметр
- Локальная LLM (Llama 3.1 70B на сервере с GPU) — ни один символ не уходит в интернет
- Борд загружает конфиденциальные документы: финмодели, оценки, due diligence
- Ассистент помогает анализировать сделки, строить сценарии, готовить board decks
- Иммутабельный аудит-лог: если потребуется — полная доказательная база, кто запрашивал какую информацию

**Ценность:** стратегический AI-ассистент, который не стоит $500/час и не создаёт риск утечки. Окупается за 1–2 месяца vs. стоимость консультантов.

**Ключевая метрика:** скорость подготовки стратегических материалов × 3–5, нулевой риск утечки данных.

---

### Сводная карта сценариев для презентации клиентам

| Сценарий | Персона | Главная боль | Режим деплоя | ROI |
|----------|---------|-------------|--------------|-----|
| Второй мозг руководителя | CEO / топ-менеджер | Перегрузка информацией | Cloud / Hybrid | 2–3 ч/день |
| Корпоративная база знаний | HR / офис-менеджер | Рутинные вопросы сотрудников | Hybrid | 1.5 FTE |
| Безопасная аналитика | CFO / аналитик | Разрозненные данные | Hybrid | 2 дня → 5 мин |
| Подготовка документов | Руководитель продаж | Ручная подготовка КП | Cloud / Hybrid | 3 ч → 30 мин на КП |
| AI для малого бизнеса | Собственник | Нет IT, нет аналитики | Cloud | $30/мес vs $1500/мес |
| Стратегический ассистент | Совет директоров | Конфиденциальность | On-premise | ×3–5 скорость |

---

## 4. Архитектура AI-модуля: подход PAI

### Почему PAI — правильная основа

PAI (Personal AI Infrastructure) Даниэля Мисслера — open-source framework (6.3K звёзд), построенный на фундаментальном принципе: **AI должен знать контекст пользователя, учиться на взаимодействиях и непрерывно улучшаться**. В отличие от stateless-чатботов, PAI реализует полный цикл: Observe → Think → Plan → Execute → Verify → **Learn** → Improve.

Мы адаптируем ключевые концепции PAI для бизнес-контекста — не копируя реализацию (PAI привязан к Claude Code + TypeScript + CLI), а перенося архитектурные примитивы в наш Python + PocketBase стек.

### Что мы берём из PAI

**1. TELOS → Business Context System**

TELOS в PAI — это 10+ markdown-файлов, определяющих «кто вы» (MISSION, GOALS, CHALLENGES, STRATEGIES и т.д.). Для бизнеса мы адаптируем это в **Business Context** — структурированный контекст компании, который ассистент всегда «знает»:

```python
# PocketBase collection: "business_context"
# Каждый tenant имеет свой набор контекстных файлов

CONTEXT_TYPES = {
    "mission":      "Миссия и ценности компании",
    "goals":        "Текущие цели (квартал/год)",
    "team":         "Оргструктура, роли, контакты",
    "products":     "Продукты/услуги, ценности, позиционирование",
    "processes":    "Ключевые бизнес-процессы и регламенты",
    "challenges":   "Текущие проблемы и блокеры",
    "vocabulary":   "Корпоративный глоссарий и жаргон",
    "tone":         "Тон коммуникации (формальный/неформальный)",
}
```

В PocketBase это collection `business_context` с полями `tenant`, `type` (select из CONTEXT_TYPES), `content` (text), `updated_by` (relation → users). При каждом запросе AI Engine подгружает релевантный контекст в system prompt — ассистент знает компанию, а не просто отвечает «в общем».

**Принципиальное отличие от generic ChatGPT:** когда сотрудник спрашивает «Как оформить командировку?», ассистент отвечает не «Обычно командировки оформляются через HR-отдел...», а «Согласно вашему регламенту DP-007, нужно заполнить форму на портале ERP, согласовать с руководителем отдела и передать в бухгалтерию Елене Смирновой (доб. 2145) за 5 рабочих дней до выезда».

**2. Memory System → Learning Pipeline**

В PAI три уровня памяти (hot/warm/cold) с непрерывным обучением. Мы реализуем адаптированную версию:

```python
class MemoryService:
    """
    Трёхуровневая система памяти, адаптированная из PAI.
    
    HOT:  Текущая сессия — контекст разговора (в RAM)
    WARM: Краткосрочная — последние взаимодействия, паттерны (PocketBase)
    COLD: Долгосрочная — извлечённые знания, обобщения (RAG / ChromaDB)
    """
    
    async def capture_signal(self, interaction_id: str, signal: dict):
        """
        После каждого взаимодействия фиксируем сигнал обратной связи.
        PAI принцип: каждое взаимодействие генерирует данные для обучения.
        """
        # signal = {
        #   "rating": 4,           # пользователь оценил ответ (1-5)
        #   "was_useful": True,     # пользователь кликнул "полезно"
        #   "was_edited": False,    # пользователь отредактировал ответ
        #   "follow_up": False,     # пришлось переспрашивать
        #   "topic": "hr_policy",   # автоклассификация
        # }
        await self.pb.collection("memory_signals").create({
            "interaction": interaction_id,
            "signal_type": signal.get("type", "implicit"),
            "data": signal,
            "tenant": self.tenant_id,
        })
    
    async def get_relevant_context(self, query: str, tenant_id: str) -> str:
        """
        Собираем контекст из всех уровней памяти.
        PAI принцип: система знает, что работало раньше.
        """
        # HOT: текущая сессия (уже в messages)
        
        # WARM: недавние похожие вопросы от этого пользователя
        recent = await self.get_recent_patterns(tenant_id, query, limit=3)
        
        # COLD: RAG по knowledge base
        rag_context = await self.rag.search(tenant_id, query)
        
        return self.merge_context(recent, rag_context)
```

**Ключевое отличие от обычного RAG:** система не просто ищет документы — она помнит, какие ответы работали раньше, и учитывает обратную связь. Если пользователь три раза переспрашивал по теме «командировки», ассистент автоматически даёт более детальный ответ по этой теме в будущем.

**3. Skill System → Business Skills**

PAI организует возможности как модульные Skills с routing. Мы адаптируем это для бизнес-сценариев:

```python
# Каждый Skill — это отдельный модуль с чётким контрактом
BUSINESS_SKILLS = {
    "knowledge_qa": {
        "description": "Ответы на вопросы по базе знаний компании",
        "triggers": ["как", "что такое", "где найти", "регламент", "политика"],
        "requires_rag": True,
        "requires_dlp": True,
    },
    "document_draft": {
        "description": "Подготовка документов: КП, письма, отчёты",
        "triggers": ["подготовь", "напиши", "составь", "шаблон"],
        "requires_rag": True,      # для доступа к шаблонам
        "requires_dlp": True,
        "output_format": "document",
    },
    "data_analysis": {
        "description": "Анализ данных из загруженных файлов",
        "triggers": ["посчитай", "сравни", "аналитика", "динамика", "рентабельность"],
        "requires_rag": True,
        "requires_dlp": True,       # финансовые данные — чувствительны
        "access_level": "managers",  # не для всех сотрудников
    },
    "meeting_prep": {
        "description": "Подготовка к встречам и совещаниям",
        "triggers": ["подготовь к встрече", "summary по проекту", "что обсуждали"],
        "requires_rag": True,
        "requires_memory": True,    # нужны предыдущие встречи
    },
    "daily_digest": {
        "description": "Ежедневный/еженедельный дайджест",
        "triggers": ["дайджест", "что нового", "итоги дня"],
        "scheduled": True,          # может запускаться по расписанию
        "requires_memory": True,
    },
}

class SkillRouter:
    """
    PAI принцип: Intelligent Routing.
    Запрос → определение Skill → выполнение с правильным контекстом.
    """
    async def route(self, query: str, user: dict) -> str:
        # 1. Классифицируем запрос → определяем skill
        skill = await self.classify(query)
        
        # 2. Проверяем доступ (RBAC)
        if skill.get("access_level") and not self.has_access(user, skill["access_level"]):
            return "У вас нет доступа к этой функции. Обратитесь к администратору."
        
        # 3. Собираем контекст, специфичный для skill
        context = await self.build_context(skill, query, user)
        
        # 4. Выполняем с правильным system prompt
        return await self.execute(skill, query, context)
```

**Принцип из PAI: «Scaffolding > Model».** Не имеет значения, какую LLM использует клиент (GPT-4o, Claude, Llama) — архитектура навыков, контекста и памяти определяет качество ответов. Одна и та же модель с business context + skill routing + memory отвечает в 3–5 раз полезнее, чем без scaffolding.

**4. Hook System → Business Automation**

В PAI hooks — это реакции на события жизненного цикла. Для бизнеса это автоматизация рутины:

```python
BUSINESS_HOOKS = {
    "on_document_upload": {
        "description": "При загрузке документа → автоиндексация в RAG",
        "actions": ["extract_text", "chunk", "embed", "index"],
    },
    "on_new_employee": {
        "description": "При добавлении пользователя → onboarding бот",
        "actions": ["send_welcome", "create_onboarding_chat", "load_starter_docs"],
    },
    "on_daily_schedule": {
        "description": "Каждое утро → дайджест для руководителей",
        "actions": ["collect_updates", "generate_digest", "send_telegram"],
        "schedule": "0 8 * * 1-5",  # пн-пт в 8:00
    },
    "on_pii_detected": {
        "description": "При обнаружении PII → alert администратору",
        "actions": ["log_audit", "notify_admin", "increment_risk_score"],
    },
    "on_low_rating": {
        "description": "Плохая оценка ответа → анализ и улучшение",
        "actions": ["log_feedback", "analyze_failure", "update_memory"],
    },
}
```

**5. User/System Separation → Tenant Customization**

PAI строго разделяет USER/ (пользовательские настройки) и SYSTEM/ (инфраструктура). При обновлении системы пользовательские данные не затрагиваются. Мы применяем тот же принцип:

```
SYSTEM (обновляется нами):        TENANT (настраивается клиентом):
├── security pipeline              ├── business_context/
├── DLP rules (base)               │   ├── mission.md
├── skill definitions              │   ├── team.md
├── guardrails (base)              │   ├── products.md
├── API endpoints                  │   └── processes.md
└── core prompts                   ├── custom_dlp_rules/
                                   │   └── blocked_terms.txt
                                   ├── documents/
                                   │   └── (загруженные файлы)
                                   ├── templates/
                                   │   └── (шаблоны КП, писем)
                                   └── settings.json
```

Обновление системы (`docker compose pull && docker compose up -d`) не трогает данные tenant — всё в отдельном volume. Клиент не боится обновлений.

### Итого: 7 архитектурных принципов из PAI, адаптированных для бизнеса

| # | Принцип PAI | Адаптация для бизнеса |
|---|------------|----------------------|
| 1 | **User Centricity** — система вокруг человека, не технологии | Business Context: ассистент знает компанию, а не просто отвечает на вопросы |
| 2 | **TELOS** — глубокий контекст | 8 типов бизнес-контекста: миссия, цели, команда, продукты, процессы... |
| 3 | **Memory System** — непрерывное обучение | 3-уровневая память (hot/warm/cold) с capture signals и feedback loop |
| 4 | **Skill System** — модульные навыки | 5 бизнес-навыков с intelligent routing и RBAC |
| 5 | **Hook System** — автоматизация | Бизнес-хуки: onboarding, дайджесты, alert на PII, анализ обратной связи |
| 6 | **User/System Separation** — безопасные обновления | Разделение SYSTEM (наш код) и TENANT (данные клиента) |
| 7 | **Scaffolding > Model** — архитектура важнее модели | Один и тот же Llama 3.1 + business context + skills = 3–5× полезнее голой модели |

---

## 5. Интеграции для российского рынка

Для компаний 50–500 человек в России набор бизнес-систем достаточно предсказуем. Ниже — карта интеграций от «must have в MVP» до «конкурентное преимущество».

### Приоритеты интеграций

| Приоритет | Система | Покрытие рынка | В каком спринте |
|-----------|---------|---------------|-----------------|
| 🔴 P0 — MVP | **Битрикс24** (CRM + задачи + чат) | ~65% компаний 50–500 чел. | Sprint 3–4 |
| 🔴 P0 — MVP | **MyMeet** (запись и транскрибация совещаний) | Флагманский кейс | Sprint 3 |
| 🔴 P0 — MVP | **Telegram** (бот-интерфейс) | Повсеместно | Sprint 4 |
| 🟡 P1 — v1.1 | **1С** (бухгалтерия / ERP) | ~80% компаний | Sprint 5 или post-MVP |
| 🟡 P1 — v1.1 | **Яндекс 360** (почта + диск + календарь) | Растёт после ухода Google | Sprint 5 или post-MVP |
| 🟢 P2 — v1.2 | **amoCRM** (альтернативная CRM) | ~20% компаний | Post-MVP |
| 🟢 P2 — v1.2 | **Корп. мессенджеры** (VK Teams, Compass, Пачка) | Госсектор, крупный бизнес | Post-MVP |
| 🟢 P2 — v1.2 | **ЭДО** (СБИС, Диадок, 1С:ДО) | Обязателен для B2B | Post-MVP |

---

### 🔴 P0: Битрикс24 — ключевая интеграция

Битрикс24 — доминирующая платформа для среднего бизнеса в России. CRM, задачи, чат, телефония, автоматизация — всё в одном. Интеграция с Битрикс24 даёт ассистенту доступ к «нервной системе» компании.

**Способ подключения:** REST API через входящий вебхук (не требует публикации приложения в Маркете Битрикс24). Вебхук создаётся за 2 минуты в админке Битрикс24 (Приложения → Вебхуки) и даёт полный доступ к REST API от имени пользователя.

**Python SDK:** `fast-bitrix24` (MIT, PyPI, async/sync, batching, autothrottling). Лучший вариант для нашего стека — зрелая библиотека с контролем rate limits.

**Что ассистент сможет делать:**

```python
# integrations/bitrix24.py

from fast_bitrix24 import BitrixAsync

class Bitrix24Integration:
    def __init__(self, webhook_url: str):
        self.bx = BitrixAsync(webhook_url)

    # ── CRM: Сделки ──────────────────────────────────
    async def get_deals(self, filter: dict = None) -> list:
        """Получить сделки с фильтрацией."""
        return await self.bx.get_all(
            "crm.deal.list",
            params={"filter": filter or {}, "select": ["*", "UF_*"]}
        )

    async def get_deal_summary(self, stage: str = None) -> dict:
        """Сводка по сделкам для руководителя."""
        deals = await self.get_deals(
            {"STAGE_ID": stage} if stage else {}
        )
        total = sum(float(d.get("OPPORTUNITY", 0)) for d in deals)
        by_stage = {}
        for d in deals:
            s = d.get("STAGE_ID", "unknown")
            by_stage.setdefault(s, {"count": 0, "sum": 0})
            by_stage[s]["count"] += 1
            by_stage[s]["sum"] += float(d.get("OPPORTUNITY", 0))
        return {"total_deals": len(deals), "total_amount": total, "by_stage": by_stage}

    # ── CRM: Контакты и компании ─────────────────────
    async def find_contact(self, query: str) -> list:
        """Поиск контакта по имени, телефону или email."""
        return await self.bx.get_all(
            "crm.contact.list",
            params={"filter": {"%NAME": query}, "select": ["*"]}
        )

    # ── Задачи ────────────────────────────────────────
    async def get_my_tasks(self, user_id: int) -> list:
        """Задачи пользователя (открытые)."""
        return await self.bx.get_all(
            "tasks.task.list",
            params={
                "filter": {"RESPONSIBLE_ID": user_id, "STATUS": [2, 3]},
                "select": ["ID", "TITLE", "DEADLINE", "PRIORITY", "STATUS"]
            }
        )

    async def create_task(self, title: str, responsible_id: int,
                          deadline: str = None, description: str = "") -> dict:
        """Создать задачу."""
        return await self.bx.call(
            "tasks.task.add",
            {"fields": {
                "TITLE": title,
                "RESPONSIBLE_ID": responsible_id,
                "DEADLINE": deadline,
                "DESCRIPTION": description,
            }}
        )

    # ── Активность (звонки, письма, встречи) ──────────
    async def get_recent_activities(self, entity_id: int,
                                     entity_type: str = "DEAL") -> list:
        """Последние активности по сделке/контакту."""
        return await self.bx.get_all(
            "crm.activity.list",
            params={
                "filter": {"OWNER_ID": entity_id, "OWNER_TYPE_ID": entity_type},
                "order": {"CREATED": "DESC"},
                "select": ["*"]
            }
        )
```

**Бизнес-сценарии с Битрикс24:**

| Запрос пользователя | Что делает ассистент |
|---------------------|---------------------|
| «Какие сделки на этапе согласования?» | `crm.deal.list` с фильтром по стадии → таблица с суммами |
| «Создай задачу Иванову: подготовить отчёт до пятницы» | Находит Иванова в контактах Б24 → `tasks.task.add` |
| «Что у нас по клиенту "Альфа-Строй"?» | `crm.company.list` → сделки → активности → полная карточка |
| «Сводка по воронке продаж за этот месяц» | `crm.deal.list` → группировка по стадиям → summary |
| «Кто давно не контактировал с клиентами?» | `crm.activity.list` → анализ дат → список "забытых" клиентов |
| «Мои задачи на сегодня» | `tasks.task.list` по текущему пользователю |

**Важно для безопасности:** вебхук Битрикс24 привязан к конкретному пользователю и его правам. Нужно создавать отдельный вебхук с ограниченными правами (только CRM + Задачи + Активность, без «Управление пользователями»). В PocketBase collection `integrations` хранится зашифрованный webhook URL для каждого tenant.

**Структура хранения в PocketBase:**

```
Collection: integrations
- id: auto
- tenant: relation → tenants
- type: select ["bitrix24", "1c", "yandex360", "amocrm", "telegram"]
- config: json (зашифрованный через Fernet)
  {
    "webhook_url": "https://company.bitrix24.ru/rest/1/abc123/",
    "default_responsible_id": 1,
    "sync_interval_minutes": 15
  }
- enabled: bool
- last_sync: date
- created: auto
```


---

### 🔴 P0: MyMeet — помощник-транскрибатор совещаний

**Это флагманский кейс консалтинга.** Проект BONUM (ООО «БОНУМ», ~800 сотрудников, производство, договор №002К/2025) — работающий прототип полного цикла: запись совещания → транскрипция → генерация протокола → извлечение задач → создание задач в Битрикс24.

| Показатель | Было | Стало |
|------------|------|-------|
| Время подготовки протокола | 30 мин | 5 мин |
| Выполнение задач | 20% | 80%+ (цель) |
| Совещаний в месяц | 150–200 | 150–200 |
| Потенциальная экономия | — | ~13 млн ₽/год |

Интеграция с MyMeet превращает ассистента из «чат-бота, который отвечает на вопросы» в **систему, которая сама производит ценный бизнес-артефакт** — протокол совещания с задачами и ответственными.

#### Текущая архитектура (BONUM v0.5 — в production)

Прототип работает на стеке **MyMeet → Нодуль (JS-ноды) → Claude API → Битрикс24**:

```
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│   Битрикс24      │      │     MyMeet       │      │     Нодуль       │
│   Календарь      │─────▶│   Транскрибация  │─────▶│   Оркестрация    │
└──────────────────┘      └──────────────────┘      └────────┬─────────┘
                                                             │
                          ┌──────────────────┐               │
                          │   Claude API     │◀──────────────┤
                          │   Постобработка  │               │
                          └────────┬─────────┘               │
                                   ▼                         ▼
                          ┌─────────────────────────────────────┐
                          │            Битрикс24                │
                          │  Протоколы • Задачи • Уведомления   │
                          └─────────────────────────────────────┘
```

**Компоненты:**
- **MyMeet** (backend.mymeet.ai) — запись и транскрибация совещаний с диаризацией спикеров
- **Нодуль** (nodul.ru) — low-code оркестратор; JS-ноды (`fetch_meetings.js`, `sites_config.js`, `employees_*.js`, `bitrix24_upload.js`); хранит состояние в своей БД (коллекции: `sites`, `employees_cache`, `meetings_processing_status`, `events`, `tasks`)
- **Claude API** — маппинг спикеров по справочнику, коррекция терминов, генерация markdown-протокола, извлечение задач
- **Битрикс24** — загрузка DOCX-протокола на Диск (`disk.folder.uploadfile`), создание задач (`tasks.task.add`), уведомление в чат площадки (`im.message.add`)

**Что уже работает (BONUM v0.5):**
- [x] Автоматическая запись совещаний через MyMeet
- [x] Транскрибация с диаризацией спикеров
- [x] LLM-маппинг спикеров по справочнику сотрудников
- [x] LLM-коррекция терминов по словарю
- [x] Генерация markdown-протоколов по повестке ОМС
- [x] Конвертация в DOCX
- [x] Загрузка в Битрикс24 Диск (в папку площадки)
- [x] Уведомления в чаты Битрикс24
- [x] Маршрутизация по площадкам (Аксай, Новочеркасск, Траст)
- [x] Извлечение задач из протоколов
- [x] Создание задач в Битрикс24
- [ ] Анализ нарушений регламента ОМС (следующий этап)
- [ ] Эскалация между уровнями ОМС
- [ ] Интеграция с KPI

#### Целевая архитектура (наш продукт)

Нодуль заменяется на AI Engine — вся оркестрация переходит внутрь нашего стека. Это даёт: DLP-контроль, иммутабельный аудит, мультитенантность, единый UI, память (PAI Memory), тестируемость:

```
MyMeet API
    │  /api/video/report → транскрипт + спикеры + chapters
    ▼
AI Engine (Python FastAPI) — Security Pipeline
    │
    ├─ 1. Speaker Mapping
    │     Справочник сотрудников (employees_cache в PocketBase)
    │     × контекст разговора → LLM маппит «Спикер 1» → «Иванов И.В., директор филиала»
    │
    ├─ 2. Term Correction
    │     Словарь (term_dictionary в PocketBase):
    │       «китроцилиндр» → «гидроцилиндр»
    │       «МААП» / «МА АП» → «MAaUP»
    │       «ЕЛ КТД» / «ЕЛКТЭ» → «ЕЛКТД»
    │       «Bittrex» → «Битрикс24»
    │
    ├─ 3. Protocol Generation (LLM)
    │     System prompt + транскрипт + повестка ОМС → структурированный markdown
    │     Секции филиального ОМС:
    │       1. Охрана труда / Пожарная безопасность
    │       2. Качество / Изоляторы брака
    │       3. Рекламации
    │       4. Обеспечение / Дефицит (KPI MAaUP)
    │       5. Производство (сварка → сборка → ГОК)
    │       6. Служба главного инженера
    │       7. ЕЛКТД (техническая дисциплина)
    │
    ├─ 4. Task Extraction (LLM)
    │     Протокол → задачи [{title, responsible, deadline, description}]
    │     Маппинг ответственного → Bitrix24 user_id из employees_cache
    │
    ├─ 5. DLP Check (выходной контроль — Presidio)
    │
    ├─ 6. DOCX Generation (python-docx)
    │
    └─ 7. Audit Log (иммутабельный, hash-chain)
    │
    ▼
Битрикс24
    ├─ disk.folder.uploadfile → протокол (DOCX) в папку площадки
    ├─ tasks.task.add → задачи с ответственными и сроками
    └─ im.message.add → уведомление в чат площадки
```

#### MyMeet API — endpoints

**Base URL:** `https://backend.mymeet.ai`
**Auth:** API Key (`api_key` query parameter)
**Swagger:** https://backend.mymeet.ai/docs/

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/workspaces/active/all-meetings` | GET | Список встреч (`page`, `perPage`) |
| `/api/video/report` | GET | Детальный отчёт: транскрипт, chapters, speakers, tasks |
| `/api/meeting/status` | GET | Статус обработки: `processing` / `completed` / `failed` |
| `/api/storage/download` | GET | Выгрузка в `pdf` / `md` / `json` / `docx` |

**Структура ответа `/api/video/report`:**

```
followup_v2:
├── name               — название встречи
├── date               — ISO дата
├── duration           — длительность (секунды)
├── source, status
├── speakers[]         — [{id, speaker, color}]
├── chapters[]         — [{name, startSeconds, endSeconds, transcript[]}]
│   └── transcript[]   — [{speaker, text, startSeconds, endSeconds}]
├── templates[]        — AI-сущности (извлечённые MyMeet)
│   └── {name: "default-meeting", entities: [{name: "tasks", text: {actions: [...]}}]}
└── keywords[]
```

**Извлечение задач из response (Python):**

```python
tasks = next(
    (e["text"]["actions"]
     for t in data.get("templates", []) if t["name"] == "default-meeting"
     for e in t.get("entities", []) if e["name"] == "tasks"),
    []
)
```

**Ограничения API и workaround'ы:**

| Ограничение MyMeet | Наш workaround |
|---------------------|----------------|
| Нет webhook при завершении записи | Cron каждые 4 часа (проверка новых completed-встреч) |
| Нет переименования спикеров через API | LLM-маппинг по справочнику сотрудников (employees_cache) |
| Нет кастомных словарей транскрибации | LLM-коррекция по term_dictionary |
| Нет привязки к календарю Б24 | Матчинг по ключевым словам в названии встречи → площадка |

#### Python-интеграция

```python
# integrations/mymeet.py

import httpx
from datetime import datetime, timedelta
from dataclasses import dataclass, field

@dataclass
class MeetingTranscript:
    meeting_id: str
    name: str
    date: str
    duration: int                    # секунды
    speakers: list[dict]             # [{id, speaker, color}]
    chapters: list[dict]             # [{name, startSeconds, endSeconds, transcript[]}]
    tasks_raw: list[dict]            # из templates → default-meeting → tasks → actions
    full_text: str                   # "[Спикер 1]: текст\n..."
    keywords: list[str] = field(default_factory=list)

class MyMeetIntegration:
    """
    Клиент MyMeet API.
    Docs: https://backend.mymeet.ai/docs/
    """

    BASE_URL = "https://backend.mymeet.ai"

    def __init__(self, api_key: str):
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            params={"api_key": api_key},
            timeout=30.0,
        )

    async def get_recent_meetings(
        self, hours: int = 5, page: int = 0, per_page: int = 50
    ) -> list[dict]:
        """
        Получить завершённые совещания за последние N часов.
        hours=5 при cron каждые 4 часа — запас на перекрытие.
        """
        r = await self.client.get(
            "/api/workspaces/active/all-meetings",
            params={"page": page, "perPage": per_page},
        )
        r.raise_for_status()
        meetings = r.json()
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [
            m for m in meetings
            if m.get("status") == "completed"
            and datetime.fromisoformat(m["date"].replace("Z", "+00:00")) > cutoff
        ]

    async def get_report(self, meeting_id: str) -> MeetingTranscript:
        """Получить полный отчёт: транскрипт, спикеры, chapters, задачи."""
        r = await self.client.get(
            "/api/video/report",
            params={"meeting_id": meeting_id},
        )
        r.raise_for_status()
        data = r.json().get("followup_v2", {})

        # Извлекаем задачи из templates → default-meeting → tasks → actions
        tasks_raw = next(
            (e["text"]["actions"]
             for t in data.get("templates", []) if t["name"] == "default-meeting"
             for e in t.get("entities", []) if e["name"] == "tasks"),
            []
        )

        # Собираем полный текст транскрипта с chapter'ами и диаризацией
        full_text_parts = []
        for chapter in data.get("chapters", []):
            full_text_parts.append(f"\n### {chapter.get('name', 'Без названия')}\n")
            for seg in chapter.get("transcript", []):
                speaker = seg.get("speaker", "?")
                text = seg.get("text", "")
                full_text_parts.append(f"[{speaker}]: {text}")

        return MeetingTranscript(
            meeting_id=meeting_id,
            name=data.get("name", ""),
            date=data.get("date", ""),
            duration=data.get("duration", 0),
            speakers=data.get("speakers", []),
            chapters=data.get("chapters", []),
            tasks_raw=tasks_raw,
            full_text="\n".join(full_text_parts),
            keywords=data.get("keywords", []),
        )

    async def get_status(self, meeting_id: str) -> str:
        """Статус обработки: processing / completed / failed."""
        r = await self.client.get(
            "/api/meeting/status",
            params={"meeting_id": meeting_id},
        )
        r.raise_for_status()
        return r.json().get("status", "unknown")

    async def download(self, meeting_id: str, fmt: str = "md") -> bytes:
        """Скачать отчёт: pdf, md, json, docx."""
        r = await self.client.get(
            "/api/storage/download",
            params={"meeting_id": meeting_id, "format": fmt},
        )
        r.raise_for_status()
        return r.content
```

#### Сервис генерации протоколов

```python
# services/protocol_generator.py

class ProtocolGenerator:
    """
    Полный pipeline: MyMeet transcript → LLM → протокол + задачи → Битрикс24.
    Заменяет JS-ноды Нодуля единым Python-сервисом с DLP и аудитом.
    """

    # Стандартная повестка филиального ОМС (переопределяется в meeting_sites)
    DEFAULT_OMS_SECTIONS = [
        "Охрана труда / Пожарная безопасность",
        "Качество / Изоляторы брака",
        "Рекламации",
        "Обеспечение / Дефицит (KPI MAaUP)",
        "Производство (сварка → сборка → ГОК)",
        "Служба главного инженера",
        "ЕЛКТД (техническая дисциплина)",
    ]

    def __init__(self, mymeet: MyMeetIntegration, llm: LLMRouter,
                 bitrix: Bitrix24Integration, dlp: DLPService,
                 pb: PocketBaseClient):
        self.mymeet = mymeet
        self.llm = llm
        self.bitrix = bitrix
        self.dlp = dlp
        self.pb = pb

    async def process_meeting(self, meeting_id: str, tenant_id: str,
                               site: dict) -> dict:
        """
        Полный цикл обработки одного совещания.
        site — из collection meeting_sites: {code, name, bitrix_chat_id, bitrix_folder_id, sections, match_keywords}
        """
        import time
        start = time.monotonic()

        # ── 1. Транскрипт из MyMeet ──────────────────────
        transcript = await self.mymeet.get_report(meeting_id)

        # ── 2. Справочники из PocketBase ─────────────────
        employees = await self.pb.collection("employees_cache").get_full_list(
            query_params={"filter": f'tenant = "{tenant_id}"'}
        )
        term_records = await self.pb.collection("term_dictionary").get_full_list(
            query_params={"filter": f'tenant = "{tenant_id}"'}
        )
        term_map = {t["wrong_term"]: t["correct_term"] for t in term_records}

        # ── 3. LLM: маппинг спикеров + коррекция + протокол ──
        sections = site.get("sections") or self.DEFAULT_OMS_SECTIONS
        protocol_prompt = self._build_protocol_prompt(
            transcript, employees, term_map, sections
        )
        protocol_result = await self.llm.complete([
            {"role": "system", "content": PROTOCOL_SYSTEM_PROMPT},
            {"role": "user", "content": protocol_prompt},
        ])
        protocol_md = protocol_result["content"]

        # ── 4. DLP: выходной контроль ────────────────────
        dlp_result = self.dlp.scan(protocol_md)
        if dlp_result.was_modified:
            protocol_md = dlp_result.sanitized

        # ── 5. LLM: извлечение задач ─────────────────────
        tasks_result = await self.llm.complete([
            {"role": "system", "content": TASKS_EXTRACTION_PROMPT},
            {"role": "user", "content": protocol_md},
        ])
        tasks = self._parse_tasks(tasks_result["content"], employees)

        # ── 6. DOCX ──────────────────────────────────────
        docx_bytes = self._markdown_to_docx(protocol_md, transcript.name, transcript.date)

        # ── 7. Битрикс24: загрузка протокола ─────────────
        filename = f"Протокол_{site['code']}_{transcript.date[:10]}.docx"
        file_url = await self.bitrix.upload_file(
            folder_id=site["bitrix_folder_id"],
            filename=filename,
            content=docx_bytes,
        )

        # ── 8. Битрикс24: создание задач ─────────────────
        created_tasks = []
        for task in tasks:
            bx_user = self._find_bitrix_user(task["responsible"], employees)
            if not bx_user:
                continue
            result = await self.bitrix.create_task(
                title=task["title"],
                responsible_id=bx_user["bitrix_id"],
                deadline=task.get("deadline"),
                description=(
                    f"Из протокола ОМС: {transcript.name}\n"
                    f"Дата: {transcript.date}\n\n{task.get('description', '')}"
                ),
            )
            created_tasks.append(result)

        # ── 9. Битрикс24: уведомление в чат ──────────────
        if site.get("bitrix_chat_id"):
            await self.bitrix.send_message(
                chat_id=site["bitrix_chat_id"],
                message=(
                    f"📋 Протокол ОМС: {transcript.name}\n"
                    f"📅 {transcript.date[:10]} | ⏱ {transcript.duration // 60} мин.\n"
                    f"📎 {file_url}\n"
                    f"✅ Задач создано: {len(created_tasks)}"
                ),
            )

        elapsed = round(time.monotonic() - start, 1)

        # ── 10. Аудит + сохранение ───────────────────────
        await self.pb.collection("meetings_protocols").create({
            "tenant": tenant_id, "meeting_id": meeting_id,
            "meeting_name": transcript.name, "date": transcript.date,
            "site": site["code"], "protocol_markdown": protocol_md,
            "protocol_file_url": file_url, "tasks_extracted": len(created_tasks),
            "speakers_mapped": [s["speaker"] for s in transcript.speakers],
            "processing_time_sec": elapsed, "model_used": protocol_result.get("model"),
        })

        await self.pb.collection("audit_logs").create({
            "tenant": tenant_id, "action": "protocol_generated",
            "details": {
                "meeting_id": meeting_id, "site": site["code"],
                "tasks_created": len(created_tasks),
                "pii_detected": dlp_result.was_modified,
                "processing_time_sec": elapsed,
            },
        })

        return {
            "protocol_url": file_url, "tasks_created": len(created_tasks),
            "meeting_name": transcript.name, "site": site["code"],
            "processing_time_sec": elapsed,
        }

    def _build_protocol_prompt(self, transcript: MeetingTranscript,
                                employees: list, term_map: dict,
                                sections: list[str]) -> str:
        employee_list = "\n".join(
            f"- {e['name']} — {e.get('position', '?')}" for e in employees
        )
        corrections = "\n".join(
            f"- «{wrong}» → «{correct}»" for wrong, correct in term_map.items()
        )
        agenda = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sections))

        return f"""## Совещание: {transcript.name}
Дата: {transcript.date} | Длительность: {transcript.duration // 60} мин.

### СПИКЕРЫ (из транскрибации)
{chr(10).join(f"- {s['speaker']}" for s in transcript.speakers)}

### СПРАВОЧНИК СОТРУДНИКОВ (для маппинга)
{employee_list}

### СЛОВАРЬ КОРРЕКЦИИ ТЕРМИНОВ
{corrections}

### ПОВЕСТКА (разделы протокола)
{agenda}

### ТРАНСКРИПТ
{transcript.full_text}

---
ЗАДАЧА: составь протокол ОМС в формате markdown.
1. Сопоставь спикеров с сотрудниками по контексту (кто о чём говорит, должность, отдел).
2. Исправь ошибки транскрибации по словарю.
3. Для каждого пункта повестки: краткое обсуждение, решения, задачи (ответственный + срок).
4. Если раздел не обсуждался — отметь «Не рассматривалось»."""
```

#### PocketBase collections для протоколов

```
Collection: meetings_protocols
- id: auto
- tenant: relation → tenants
- meeting_id: text (MyMeet UUID)
- meeting_name: text
- date: date
- site: text (код площадки: aksay, novocherkassk, trust)
- protocol_markdown: text (полный md-протокол)
- protocol_file_url: url (ссылка на DOCX в Б24)
- tasks_extracted: number
- speakers_mapped: json
- processing_time_sec: number
- model_used: text
- created: auto

Collection: meeting_sites (конфигурация площадок)
- id: auto
- tenant: relation → tenants
- code: text unique (aksay, novocherkassk, trust)
- name: text (Аксайский филиал)
- bitrix_chat_id: text (ID чата Б24)
- bitrix_folder_id: text (ID папки на Б24 Диске)
- sections: json (массив пунктов повестки ОМС)
- match_keywords: json (ключевые слова для автоматчинга)

Collection: employees_cache (кэш сотрудников из Б24)
- id: auto
- tenant: relation → tenants
- bitrix_id: number (ID пользователя в Б24)
- name: text
- position: text
- department: text
- last_sync: date (обновляется при user.get)

Collection: term_dictionary (словарь коррекции транскрибации)
- id: auto
- tenant: relation → tenants
- wrong_term: text
- correct_term: text
- context: text (подсказка для LLM: «производство», «KPI», «ПО»)
```

**Пример данных meeting_sites (из реального BONUM):**

```json
[
  {
    "code": "aksay",
    "name": "Аксайский филиал",
    "bitrix_chat_id": "4882",
    "bitrix_folder_id": "96356",
    "sections": [
      "Охрана труда / Пожарная безопасность",
      "Качество / Изоляторы брака",
      "Рекламации",
      "Обеспечение / Дефицит (KPI MAaUP)",
      "Производство (сварка → сборка → ГОК)",
      "Служба главного инженера",
      "ЕЛКТД (техническая дисциплина)"
    ],
    "match_keywords": ["аксай", "аксайск", "aksay"]
  }
]
```

**Пример данных term_dictionary (из реального BONUM):**

| wrong_term | correct_term | context |
|------------|-------------|---------|
| китроцилиндр | гидроцилиндр | производство |
| МААП, МА АП | MAaUP | KPI обеспечения |
| ЕЛ КТД, ЕЛКТЭ | ЕЛКТД | техническая дисциплина |
| Bittrex | Битрикс24 | ПО |

#### Hook: автоматическая обработка новых совещаний

```python
# hooks/meeting_processor.py

async def check_new_meetings(tenant_id: str):
    """
    Cron каждые 4 часа (ограничение: MyMeet не имеет webhook).
    Проверяет новые completed-совещания → обрабатывает каждое → протокол в Б24.
    """
    config = await get_integration_config(tenant_id, "mymeet")
    mymeet = MyMeetIntegration(api_key=config["api_key"])
    meetings = await mymeet.get_recent_meetings(hours=5)

    sites = await pb.collection("meeting_sites").get_full_list(
        query_params={"filter": f'tenant = "{tenant_id}"'}
    )

    for meeting in meetings:
        # Уже обработано?
        existing = await pb.collection("meetings_protocols").get_full_list(
            query_params={"filter": f'meeting_id = "{meeting["id"]}"'}
        )
        if existing:
            continue

        # Определяем площадку по ключевым словам в названии
        site = match_site_by_keywords(meeting["name"], sites)
        if not site:
            log.warning("site_not_matched", meeting_name=meeting["name"])
            continue

        # Полный pipeline
        try:
            result = await protocol_generator.process_meeting(
                meeting_id=meeting["id"],
                tenant_id=tenant_id,
                site=site,
            )
            log.info("protocol_generated", **result)
        except Exception as e:
            log.error("protocol_failed", meeting_id=meeting["id"], error=str(e))


def match_site_by_keywords(meeting_name: str, sites: list[dict]) -> dict | None:
    """Сопоставление названия встречи с площадкой по ключевым словам."""
    name_lower = meeting_name.lower()
    for site in sites:
        for kw in site.get("match_keywords", []):
            if kw.lower() in name_lower:
                return site
    return None
```

#### Переход с Нодуля на AI Engine

| Аспект | Нодуль (текущий BONUM v0.5) | AI Engine (наш продукт) |
|--------|----------------------------|------------------------|
| Язык | JavaScript (ноды) | Python (FastAPI) |
| Оркестрация | Low-code canvas | Код с unit-тестами (pytest) |
| Хранение состояния | Нодуль DB (проприетарная) | PocketBase (открытая, SQLite) |
| DLP / PII контроль | **Нет** | Presidio → маскирование PII в протоколах |
| Аудит | Нодуль events (базовый лог) | Иммутабельный hash-chain audit_logs |
| Мультитенантность | **Нет** (1 клиент = 1 сценарий) | Встроенная (tenant isolation) |
| Масштабирование | Новый сценарий + настройка для каждого клиента | Новый tenant + конфиг sites + employees |
| Мониторинг | Dashboard Нодуля | Аудит-логи + Telegram alerts + admin UI |
| Тестируемость | Ручная (запуск сценария) | pytest + fixtures + CI |
| Стоимость | Подписка Нодуль + хостинг | Self-hosted, бесплатно |

**Ключевое преимущество перехода:** один и тот же код обслуживает всех клиентов с протоколами совещаний. БОНУМ — первый tenant. Каждый новый клиент = `meeting_sites` + `employees_cache` + `term_dictionary` + API-ключ MyMeet. Без клонирования сценариев.

#### Бизнес-сценарии с MyMeet

| Запрос / Триггер | Что делает ассистент |
|-----------------|---------------------|
| **Автоматически** (cron каждые 4 ч) | Проверяет новые completed-совещания → протокол → задачи в Б24 → уведомление в чат |
| «Что обсуждали на ОМС по Аксаю?» | Ищет последний протокол aksay → markdown summary |
| «Какие задачи поставили на вчерашнем совещании?» | meetings_protocols → задачи с ответственными и сроками |
| «Какие задачи с прошлого ОМС не выполнены?» | Протокол → task IDs → Б24 `tasks.task.list` → просроченные |
| «Статистика совещаний за месяц» | Агрегация: количество, средняя длительность, задач создано/выполнено |
| «Добавь термин "ГОК" = "группа обслуживания клиентов"» | term_dictionary → улучшает будущие протоколы |
| `/protocol aksay` (Telegram) | Последний протокол Аксая — summary в чат |

#### Почему MyMeet — флагманский кейс для продаж

Это не «ещё одна фича», а **самодостаточный продукт внутри продукта**, который:

- **Уже доказан:** работающий прототип BONUM v0.5 в production на реальных совещаниях
- **Производит измеримый ROI:** 30 мин → 5 мин на протокол × 150–200 совещаний/мес ≈ 75 часов руководительского времени. При ~4000 ₽/час ≈ 300 000 ₽/мес экономии
- **Показывает полный цикл ценности:** от «сырого» совещания до конкретных задач с дедлайнами в CRM — всё автоматически
- **Демонстрирует DLP:** финансовые данные из совещания маскируются при hybrid-режиме (чего нет в текущем Нодуль-решении)
- **Работает автоматически:** руководитель не должен ничего делать — протокол появляется в Б24 сам
- **Визуально впечатляет на демо:** «Вот запись совещания. Через 5 минут — вот протокол, вот задачи в Б24»
- **Масштабируется без кода:** новый клиент = конфигурация площадок + справочник сотрудников + словарь терминов (вся настройка — в Admin UI)


### 🔴 P0: Telegram — основной бот-интерфейс

Telegram — де-факто стандарт для бизнес-коммуникаций в России. Для нашей ЦА (руководители, собственники) это самый привычный канал.

**Python SDK:** `aiogram` v3 (async, FSM, middleware — идеально для нашего стека).

**Архитектура бота:**

```python
# integrations/telegram_bot.py

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from aiogram.filters import Command

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Привязка Telegram → пользователь в PocketBase."""
    # 1. Проверяем, привязан ли telegram_id к аккаунту
    user = await pb.find_user_by_telegram(message.from_user.id)
    if not user:
        # 2. Отправляем ссылку на авторизацию через web UI
        await message.answer(
            "Привяжите Telegram к аккаунту:\n"
            f"{WEB_URL}/link-telegram?code={generate_link_code(message.from_user.id)}"
        )
        return
    await message.answer(f"Здравствуйте, {user['name']}! Задайте вопрос.")

@router.message(F.text)
async def handle_message(message: Message):
    """Основной обработчик — запрос через security pipeline."""
    user = await pb.find_user_by_telegram(message.from_user.id)
    if not user:
        await message.answer("Сначала привяжите аккаунт: /start")
        return

    # Полный pipeline: DLP → Guardrails → LLM → DLP → Audit
    response = await chat_service.process(
        message=message.text,
        user_id=user["id"],
        tenant_id=user["tenant"],
        channel="telegram"
    )
    await message.answer(response.reply)

@router.message(Command("deals"))
async def cmd_deals(message: Message):
    """Быстрая команда: сводка по сделкам из Битрикс24."""
    user = await pb.find_user_by_telegram(message.from_user.id)
    bx = await get_bitrix_for_tenant(user["tenant"])
    summary = await bx.get_deal_summary()
    await message.answer(
        f"📊 Сделки:\n"
        f"Всего: {summary['total_deals']}\n"
        f"Сумма: {summary['total_amount']:,.0f} ₽\n"
        + "\n".join(f"  {k}: {v['count']} ({v['sum']:,.0f} ₽)"
                    for k, v in summary['by_stage'].items())
    )
```

**Ключевые команды бота для руководителя:**

| Команда | Действие |
|---------|----------|
| Свободный текст | Вопрос ассистенту (с DLP-pipeline) |
| `/deals` | Сводка по сделкам из Битрикс24 |
| `/tasks` | Мои задачи на сегодня из Битрикс24 |
| `/digest` | Дайджест за вчера (встречи, задачи, сделки) |
| `/doc <запрос>` | Поиск по базе знаний компании |
| `/mode local` | Переключить на локальную LLM (для конфиденциальных вопросов) |

---

### 🟡 P1: 1С — бухгалтерия и ERP

1С — «кровеносная система» российского бизнеса. Интеграция даёт ассистенту доступ к финансовым данным, складу, зарплатам.

**Способ подключения:** 1С поддерживает OData/REST через HTTP-сервисы (начиная с 1С:Предприятие 8.3.5). Для облачных версий (1С:Фреш) — REST API. Для коробочных — публикация HTTP-сервиса на веб-сервере (Apache/IIS).

**Python-клиент:**

```python
# integrations/onec.py

import httpx
from typing import Optional

class OneCIntegration:
    """Интеграция с 1С через OData/HTTP-сервисы."""

    def __init__(self, base_url: str, username: str, password: str):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            auth=(username, password),
            headers={"Accept": "application/json"}
        )

    async def get_cash_balance(self) -> dict:
        """Остатки денежных средств."""
        r = await self.client.get(
            "/odata/standard.odata/AccumulationRegister_ДенежныеСредства/Balance"
        )
        return r.json()

    async def get_revenue(self, period_start: str, period_end: str) -> dict:
        """Выручка за период."""
        r = await self.client.get(
            "/odata/standard.odata/AccumulationRegister_Продажи/Turnovers",
            params={"$filter": f"Period ge datetime'{period_start}' "
                               f"and Period le datetime'{period_end}'"}
        )
        return r.json()

    async def get_debts(self) -> list:
        """Дебиторская задолженность."""
        r = await self.client.get(
            "/odata/standard.odata/AccumulationRegister_РасчетыСКонтрагентами/Balance",
            params={"$filter": "СуммаBalance gt 0",
                    "$orderby": "СуммаBalance desc"}
        )
        return r.json().get("value", [])
```

**Бизнес-сценарии с 1С:**

| Запрос | Что делает ассистент |
|--------|---------------------|
| «Сколько денег на счетах?» | OData → остатки ДС → ответ |
| «Выручка за прошлый месяц vs позапрошлый» | OData → регистр продаж → сравнение |
| «Кто нам должен больше всех?» | OData → дебиторка → топ-10 должников |
| «Какая рентабельность по проекту X?» | Сделки из Б24 + затраты из 1С → расчёт |

**Важно для безопасности:** финансовые данные из 1С — самые чувствительные. DLP должен маскировать суммы и контрагентов в hybrid-режиме. Доступ к 1С-интеграции — только для роли `admin` и `manager`.

---

### 🟡 P1: Яндекс 360 для бизнеса

После ухода Google Workspace и Microsoft 365 многие российские компании перешли на Яндекс 360 (почта, диск, календарь, мессенджер).

**Способ подключения:** Яндекс 360 API (REST, OAuth 2.0). Для почты — IMAP (стандартный). Для Диска — WebDAV или Яндекс Диск API. Для Календаря — CalDAV.

**Сценарии:**

| Запрос | Интеграция |
|--------|-----------|
| «Какие у меня встречи сегодня?» | CalDAV → Яндекс Календарь |
| «Найди файл "бюджет Q3" на Диске» | Яндекс Диск API → поиск → ссылка |
| «Напиши письмо Петрову про согласование» | IMAP/SMTP → отправка (с DLP-проверкой) |
| «Что пришло важного на почту за сегодня?» | IMAP → фильтрация → summary |

**Python:** `caldav` (CalDAV), `imaplib` + `aiosmtplib` (почта), `yadisk` (Яндекс Диск API).

---

### 🟢 P2: Дополнительные интеграции (post-MVP)

**amoCRM** — альтернативная CRM, популярна в малом бизнесе. REST API с OAuth 2.0. Python SDK: `amocrm-api`.

**VK Teams** — корпоративный мессенджер от VK для крупного бизнеса и госсектора (замена Slack/Teams). Bot API аналогичен Telegram. Важен для compliance (ФЗ-584 запрещает зарубежные мессенджеры в ряде отраслей).

**ЭДО (СБИС, Диадок, 1С:ДО)** — электронный документооборот. Ассистент может искать по документам, проверять статусы подписания, уведомлять о просроченных подписях.

**Мой Офис / Р7-Офис** — российские альтернативы MS Office. Совместная работа с документами.

---

### Архитектура интеграций: Plugin-система

Все интеграции реализуются как **плагины** с единым интерфейсом:

```python
# integrations/base.py

from abc import ABC, abstractmethod
from typing import Any

class IntegrationPlugin(ABC):
    """Базовый класс для всех интеграций."""

    name: str = ""
    description: str = ""
    required_config_keys: list[str] = []

    @abstractmethod
    async def connect(self, config: dict) -> bool:
        """Подключение и проверка credentials."""
        ...

    @abstractmethod
    async def execute(self, action: str, params: dict) -> Any:
        """Выполнить действие."""
        ...

    @abstractmethod
    def get_available_actions(self) -> list[dict]:
        """Список доступных действий для Skill Router."""
        ...

    async def health_check(self) -> bool:
        """Проверка доступности."""
        ...


class Bitrix24Plugin(IntegrationPlugin):
    name = "bitrix24"
    description = "CRM, задачи и коммуникации Битрикс24"
    required_config_keys = ["webhook_url"]

    def get_available_actions(self) -> list[dict]:
        return [
            {"action": "deals_summary", "description": "Сводка по сделкам",
             "triggers": ["сделки", "воронка", "продажи", "CRM"]},
            {"action": "find_contact", "description": "Поиск контакта",
             "triggers": ["контакт", "клиент", "найди"]},
            {"action": "my_tasks", "description": "Мои задачи",
             "triggers": ["задачи", "таски", "что делать"]},
            {"action": "create_task", "description": "Создать задачу",
             "triggers": ["создай задачу", "поставь задачу"]},
            {"action": "client_card", "description": "Карточка клиента",
             "triggers": ["что по клиенту", "история клиента"]},
        ]

# Реестр плагинов
PLUGIN_REGISTRY: dict[str, type[IntegrationPlugin]] = {
    "bitrix24": Bitrix24Plugin,
    "onec": OneCPlugin,
    "yandex360": Yandex360Plugin,
    "amocrm": AmoCRMPlugin,
}
```

**Skill Router** автоматически учитывает подключённые интеграции: если у tenant включён Битрикс24, запрос «мои задачи» маршрутизируется в `Bitrix24Plugin.execute("my_tasks")`. Если не включён — ассистент отвечает из общей базы знаний.

---

### Настройка интеграций для клиента: 5 минут в Admin UI

Подключение интеграций должно быть максимально простым для ЦА (руководители, не разработчики):

```
Admin UI → Настройки → Интеграции

┌─────────────────────────────────────────────┐
│  🟢 Битрикс24                    [Включено] │
│  Webhook: https://comp.bitrix24.ru/rest/... │
│  Последняя синхронизация: 5 мин. назад      │
│  [Проверить подключение] [Отключить]        │
├─────────────────────────────────────────────┤
│  ⚪ 1С:Предприятие               [Выключено]│
│  [Подключить →]                             │
├─────────────────────────────────────────────┤
│  ⚪ Яндекс 360                   [Выключено]│
│  [Подключить →]                             │
├─────────────────────────────────────────────┤
│  🟢 Telegram Bot                 [Включено] │
│  @company_assistant_bot                     │
│  Привязано пользователей: 12                │
└─────────────────────────────────────────────┘
```

Для Битрикс24 администратору нужно: зайти в Битрикс24 → Приложения → Вебхуки → Создать → Скопировать URL → Вставить в Admin UI. Всё.

---

### Влияние на спринты

Интеграции встраиваются в существующий план:

| Спринт | Что добавляется |
|--------|----------------|
| Sprint 1–2 | `IntegrationPlugin` базовый класс, collection `integrations` в PocketBase |
| Sprint 3 | **Битрикс24 Plugin**: CRM-сделки, контакты, задачи. **MyMeet Plugin + ProtocolGenerator**: полный pipeline транскрибации совещаний → протоколы → задачи в Б24. Collections: meetings_protocols, meeting_sites, employees_cache, term_dictionary |
| Sprint 4 | **Telegram Bot** с командами `/deals`, `/tasks`, `/digest`. Привязка Telegram → PocketBase user. Admin UI: страница управления интеграциями |
| Sprint 5 | **1С Plugin** (OData, базовые финансовые запросы). **Яндекс 360** (CalDAV-календарь, IMAP-почта) |
| Post-MVP | amoCRM, VK Teams, ЭДО, кросс-интеграции (сделка Б24 + затраты 1С = рентабельность) |

---

## 6. Целевая архитектура

```
┌────────────────────────────────────────────┐
│              ПОЛЬЗОВАТЕЛИ                   │
│  Web UI (React/SPA)  │  Telegram Bot        │
└──────────┬───────────┴───────┬──────────────┘
           │                   │
           ▼                   ▼
┌─────────────────────────────────────────────┐
│           POCKETBASE (порт 8090)            │
│                                             │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  │
│  │  Auth   │  │ API Rules│  │  Admin UI │  │
│  │ email/  │  │ (RBAC)   │  │ Dashboard │  │
│  │ OAuth2  │  │          │  │           │  │
│  │ MFA     │  │          │  │           │  │
│  └─────────┘  └──────────┘  └───────────┘  │
│                                             │
│  Collections:                               │
│  • users (auth)    — пользователи + роли    │
│  • tenants         — компании/workspace     │
│  • conversations   — история чатов          │
│  • audit_logs      — аудит (append-only)    │
│  • documents       — файлы knowledge base   │
│  • settings        — настройки по tenant    │
└──────────────────┬──────────────────────────┘
                   │ HTTP (internal network)
                   ▼
┌─────────────────────────────────────────────┐
│        AI ENGINE (Python, порт 8000)        │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │        Security Pipeline             │   │
│  │                                      │   │
│  │  Input                               │   │
│  │   ├─ Presidio PII Scan              │   │
│  │   ├─ Prompt Injection Detection     │   │
│  │   └─ Topic Guardrails              │   │
│  │                                      │   │
│  │  LLM Router (LiteLLM)               │   │
│  │   ├─ Cloud: OpenAI / Anthropic      │   │
│  │   ├─ Hybrid: маскированные данные   │   │
│  │   └─ Local: Ollama (air-gapped)     │   │
│  │                                      │   │
│  │  Output                              │   │
│  │   ├─ PII Leak Detection             │   │
│  │   └─ Safety Filter                  │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  RAG: ChromaDB (embedded, per-tenant)       │
│  Embeddings: sentence-transformers (local)  │
└─────────────────────────────────────────────┘
```

**Всего 2 контейнера.** Для air-gapped добавляется 3-й — Ollama с локальной LLM.

### Почему ChromaDB вместо Qdrant

Для компаний 50–500 человек Qdrant — overkill. ChromaDB:
- Embedded-режим (без отдельного сервера, работает внутри Python-процесса)
- Per-tenant изоляция через `collection` + metadata filtering
- Достаточно для ~100K документов на tenant при таком масштабе
- Нулевая конфигурация: `pip install chromadb` — и работает

---

## 7. Структура PocketBase Collections

### `users` (Auth Collection)

```
id              — auto
email           — string, unique, required
password        — auto (hashed)
name            — string
role            — select: ["admin", "manager", "user"]
tenant          — relation → tenants
verified        — auto
avatar          — file
```

**API Rules:**
```
List:   @request.auth.id != "" && tenant = @request.auth.tenant
View:   @request.auth.id != "" && (id = @request.auth.id || @request.auth.role = "admin")
Create: @request.auth.role = "admin" && @request.auth.tenant = @request.body.tenant
Update: id = @request.auth.id || @request.auth.role = "admin"
Delete: @request.auth.role = "admin"
```

### `tenants` (Base Collection)

```
id              — auto
name            — string, required
slug            — string, unique
plan            — select: ["starter", "business", "enterprise"]
settings        — json (DLP config, allowed models, etc.)
max_users       — number, default: 50
created         — auto
```

### `conversations` (Base Collection)

```
id              — auto
user            — relation → users
tenant          — relation → tenants
title           — string
messages        — json (массив {role, content, timestamp})
model_used      — string
tokens_in       — number
tokens_out      — number
pii_detected    — bool, default: false
created         — auto
updated         — auto
```

**API Rules:**
```
List:   @request.auth.id != "" && user = @request.auth.id
View:   @request.auth.id != "" && (user = @request.auth.id || @request.auth.role ?= "admin")
Create: @request.auth.id != ""
Update: user = @request.auth.id
Delete: user = @request.auth.id || @request.auth.role = "admin"
```

### `audit_logs` (Base Collection)

```
id              — auto
tenant          — relation → tenants
user            — relation → users
action          — string (chat, login, export, admin_action, etc.)
details         — json
ip_address      — string
pii_entities    — json (типы найденных PII, без значений)
dlp_action      — select: ["passed", "redacted", "blocked"]
prev_hash       — string (SHA-256 предыдущей записи)
created         — auto
```

**API Rules:**
```
List:   @request.auth.role = "admin" || @request.auth.role = "manager"
View:   @request.auth.role = "admin" || @request.auth.role = "manager"
Create: (only via AI Engine backend — API key auth)
Update: (locked — никто, даже суперадмин через приложение)
Delete: (locked)
```

### `documents` (Base Collection)

```
id              — auto
tenant          — relation → tenants
uploaded_by     — relation → users
title           — string
file            — file
content_text    — text (extracted text for RAG indexing)
category        — select: ["general", "policy", "technical", "hr", "finance"]
access_level    — select: ["all", "managers", "admins"]
indexed         — bool, default: false
created         — auto
```

**API Rules:**
```
List:   @request.auth.id != "" && tenant = @request.auth.tenant &&
        (access_level = "all" ||
         (access_level = "managers" && (@request.auth.role = "manager" || @request.auth.role = "admin")) ||
         (access_level = "admins" && @request.auth.role = "admin"))
Create: @request.auth.role = "admin" || @request.auth.role = "manager"
Delete: @request.auth.role = "admin"
```

---

## 8. AI Engine: Python-сервер

### Структура проекта

```
ai-engine/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Pydantic Settings
│   ├── security/
│   │   ├── dlp.py           # Presidio DLP Service
│   │   ├── guardrails.py    # Prompt injection + topic filter
│   │   └── audit.py         # Audit logger → PocketBase
│   ├── llm/
│   │   ├── router.py        # LiteLLM routing
│   │   └── rag.py           # ChromaDB RAG service
│   ├── context/
│   │   ├── business.py      # Business Context (PAI: TELOS)
│   │   └── memory.py        # Memory System (PAI: Memory)
│   ├── skills/
│   │   └── router.py        # Skill Router (PAI: Skills)
│   ├── services/
│   │   └── protocol_generator.py  # MyMeet → LLM → протокол → Б24
│   ├── integrations/
│   │   ├── base.py          # IntegrationPlugin ABC
│   │   ├── bitrix24.py      # Битрикс24 CRM/задачи/диск
│   │   ├── mymeet.py        # MyMeet транскрибация
│   │   ├── onec.py          # 1С бухгалтерия (OData)
│   │   ├── yandex360.py     # Яндекс почта/диск/календарь
│   │   └── telegram_bot.py  # Telegram Bot (aiogram)
│   ├── hooks/
│   │   └── meeting_processor.py  # Cron: новые совещания → протоколы
│   └── pb_client.py         # PocketBase Python client wrapper
├── Dockerfile
├── pyproject.toml
└── tests/
    ├── test_dlp.py
    ├── test_protocol_generator.py
    └── fixtures/             # Тестовые транскрипты MyMeet
```

### Ключевые модули

**`config.py`** — три режима работы:

```python
from pydantic_settings import BaseSettings
from enum import Enum

class DeployMode(str, Enum):
    CLOUD = "cloud"         # LLM в облаке, данные локально
    HYBRID = "hybrid"       # DLP маскирует PII → облачная LLM
    LOCAL = "local"         # Всё локально (air-gapped)

class Settings(BaseSettings):
    deploy_mode: DeployMode = DeployMode.HYBRID
    pocketbase_url: str = "http://pocketbase:8090"
    pocketbase_admin_email: str
    pocketbase_admin_password: str

    # LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    ollama_url: str = "http://ollama:11434"
    default_model: str = "gpt-4o-mini"

    # DLP
    dlp_enabled: bool = True
    dlp_languages: list[str] = ["en", "ru"]
    dlp_block_threshold: float = 0.85

    # RAG
    chroma_persist_dir: str = "/data/chroma"
    embedding_model: str = "intfloat/multilingual-e5-base"

    class Config:
        env_file = ".env"
```

**`security/dlp.py`** — DLP-сервис с поддержкой русского языка:

```python
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from dataclasses import dataclass

@dataclass
class DLPResult:
    original: str
    sanitized: str
    entities: list[dict]    # [{type: "PHONE", score: 0.95}]
    was_modified: bool
    risk_score: float       # 0.0 — 1.0

class DLPService:
    def __init__(self, languages: list[str] = ["en", "ru"]):
        self.analyzer = AnalyzerEngine()
        self.anonymizer = AnonymizerEngine()
        self._add_russian_recognizers()

    def _add_russian_recognizers(self):
        """Добавляем детекторы для российских PII."""
        # ИНН (10 или 12 цифр)
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="RU_INN",
                supported_language="ru",
                patterns=[Pattern("INN", r"\b\d{10}(\d{2})?\b", 0.6)],
                context=["инн", "ИНН", "налогов", "идентификац"]
            )
        )
        # Российский телефон
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="RU_PHONE",
                supported_language="ru",
                patterns=[
                    Pattern("RU_PHONE", r"\+?7[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}", 0.7)
                ],
                context=["телефон", "звонить", "номер", "моб"]
            )
        )
        # СНИЛС
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="RU_SNILS",
                supported_language="ru",
                patterns=[Pattern("SNILS", r"\b\d{3}[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{2}\b", 0.6)],
                context=["снилс", "СНИЛС", "пенсион", "страхов"]
            )
        )

    def scan(self, text: str, language: str = "ru") -> DLPResult:
        results = self.analyzer.analyze(
            text=text,
            language=language,
            entities=[
                "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER",
                "CREDIT_CARD", "IBAN_CODE", "IP_ADDRESS",
                "RU_INN", "RU_PHONE", "RU_SNILS"
            ]
        )
        if not results:
            return DLPResult(text, text, [], False, 0.0)

        anonymized = self.anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators={
                "DEFAULT": OperatorConfig("replace", {"new_value": "<СКРЫТО>"}),
                "PERSON": OperatorConfig("replace", {"new_value": "<ИМЯ>"}),
                "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "<EMAIL>"}),
            }
        )
        entities = [{"type": r.entity_type, "score": round(r.score, 2)} for r in results]
        risk = max(r.score for r in results)
        return DLPResult(text, anonymized.text, entities, True, risk)
```

**`security/guardrails.py`** — простая защита от prompt injection (без NeMo для упрощения):

```python
import re

class GuardrailsService:
    """Лёгкие guardrails без NeMo — для MVP достаточно."""

    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"you\s+are\s+now\s+",
        r"system\s*:\s*",
        r"<\|im_start\|>",
        r"ADMIN\s*OVERRIDE",
        r"забудь\s+(все\s+)?предыдущие\s+инструкции",
        r"ты\s+теперь\s+",
        r"новая\s+роль",
    ]

    BLOCKED_TOPICS = [
        r"(как|how to)\s+(взломать|hack|crack)",
        r"(пароль|password)\s+(от|for|к)\s+",
    ]

    def check_input(self, text: str) -> tuple[bool, str]:
        """Возвращает (is_safe, reason)."""
        text_lower = text.lower()
        for pattern in self.INJECTION_PATTERNS:
            if re.search(pattern, text_lower):
                return False, "prompt_injection_detected"
        for pattern in self.BLOCKED_TOPICS:
            if re.search(pattern, text_lower):
                return False, "blocked_topic"
        return True, "ok"
```

**`llm/router.py`** — маршрутизация LLM-запросов:

```python
from litellm import acompletion
from app.config import Settings, DeployMode

class LLMRouter:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def complete(self, messages: list[dict], model: str = None) -> dict:
        model = model or self.settings.default_model

        if self.settings.deploy_mode == DeployMode.LOCAL:
            # Air-gapped: только Ollama
            model = f"ollama/{model}" if not model.startswith("ollama/") else model
            response = await acompletion(
                model=model,
                messages=messages,
                api_base=self.settings.ollama_url
            )
        else:
            # Cloud или Hybrid (DLP уже маскировал данные)
            response = await acompletion(model=model, messages=messages)

        return {
            "content": response.choices[0].message.content,
            "model": response.model,
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
        }
```

**`main.py`** — основной сервер:

```python
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from app.config import Settings
from app.security.dlp import DLPService
from app.security.guardrails import GuardrailsService
from app.security.audit import AuditLogger
from app.llm.router import LLMRouter
from app.llm.rag import RAGService
from app.pb_client import PocketBaseClient
import hashlib

app = FastAPI(title="Secure AI Assistant Engine")
settings = Settings()

dlp = DLPService(languages=settings.dlp_languages)
guardrails = GuardrailsService()
llm_router = LLMRouter(settings)
rag = RAGService(settings)
pb = PocketBaseClient(settings)
audit = AuditLogger(pb)

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    model: str | None = None
    use_rag: bool = True

class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    pii_detected: bool
    model_used: str

async def verify_user(authorization: str = Header(...)):
    """Проверяет PocketBase JWT-токен пользователя."""
    token = authorization.replace("Bearer ", "")
    user = await pb.auth_refresh(token)
    if not user:
        raise HTTPException(401, "Invalid token")
    return user

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user=Depends(verify_user)):
    tenant_id = user["tenant"]

    # 1. DLP: сканируем вход
    dlp_result = dlp.scan(req.message)

    # 2. Guardrails: проверяем на injection
    is_safe, reason = guardrails.check_input(req.message)
    if not is_safe:
        await audit.log(tenant_id, user["id"], "blocked", {
            "reason": reason, "message_hash": hashlib.sha256(req.message.encode()).hexdigest()
        })
        raise HTTPException(400, f"Запрос заблокирован: {reason}")

    # 3. Определяем текст для LLM
    llm_input = dlp_result.sanitized if settings.deploy_mode.value == "hybrid" else req.message

    # 4. RAG: ищем контекст в knowledge base tenant'а
    context = ""
    if req.use_rag:
        context = await rag.search(tenant_id, llm_input, user["role"])

    # 5. Формируем промпт
    system_prompt = f"""Ты — корпоративный AI-ассистент компании.
Отвечай точно, профессионально и по делу.
{"Контекст из базы знаний:\n" + context if context else ""}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": llm_input}
    ]

    # 6. LLM-запрос
    result = await llm_router.complete(messages, req.model)

    # 7. DLP: сканируем выход
    output_dlp = dlp.scan(result["content"])

    # 8. Аудит
    await audit.log(tenant_id, user["id"], "chat", {
        "model": result["model"],
        "tokens_in": result["tokens_in"],
        "tokens_out": result["tokens_out"],
        "pii_input": dlp_result.was_modified,
        "pii_output": output_dlp.was_modified,
        "pii_entities": dlp_result.entities + output_dlp.entities,
        "dlp_action": "redacted" if dlp_result.was_modified else "passed",
    })

    # 9. Сохраняем в PocketBase
    conv_id = await pb.save_conversation(
        user_id=user["id"],
        tenant_id=tenant_id,
        conversation_id=req.conversation_id,
        user_message=req.message,  # оригинал хранится локально
        assistant_reply=output_dlp.sanitized,
        model=result["model"],
        tokens_in=result["tokens_in"],
        tokens_out=result["tokens_out"],
        pii_detected=dlp_result.was_modified or output_dlp.was_modified,
    )

    return ChatResponse(
        reply=output_dlp.sanitized,
        conversation_id=conv_id,
        pii_detected=dlp_result.was_modified,
        model_used=result["model"]
    )

@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": settings.deploy_mode.value}
```

---

## 9. Деплой: один файл docker-compose.yml

### Стандартный (cloud/hybrid)

```yaml
version: "3.8"

services:
  pocketbase:
    image: ghcr.io/muchobien/pocketbase:latest
    volumes:
      - pb_data:/pb/pb_data
      - ./pb_migrations:/pb/pb_migrations
    ports:
      - "8090:8090"
    restart: unless-stopped

  ai-engine:
    build: ./ai-engine
    environment:
      - DEPLOY_MODE=hybrid
      - POCKETBASE_URL=http://pocketbase:8090
      - POCKETBASE_ADMIN_EMAIL=${PB_ADMIN_EMAIL}
      - POCKETBASE_ADMIN_PASSWORD=${PB_ADMIN_PASSWORD}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DEFAULT_MODEL=gpt-4o-mini
    volumes:
      - chroma_data:/data/chroma
    ports:
      - "8000:8000"
    depends_on:
      - pocketbase
    restart: unless-stopped

volumes:
  pb_data:
  chroma_data:
```

### Air-gapped (полностью локальный)

```yaml
version: "3.8"

services:
  pocketbase:
    image: ghcr.io/muchobien/pocketbase:latest
    volumes:
      - pb_data:/pb/pb_data
    networks:
      - internal
    restart: unless-stopped

  ai-engine:
    build: ./ai-engine
    environment:
      - DEPLOY_MODE=local
      - POCKETBASE_URL=http://pocketbase:8090
      - OLLAMA_URL=http://ollama:11434
      - DEFAULT_MODEL=llama3.1:8b
    volumes:
      - chroma_data:/data/chroma
    networks:
      - internal
    restart: unless-stopped

  ollama:
    image: ollama/ollama:latest
    volumes:
      - ollama_models:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    networks:
      - internal
    restart: unless-stopped

  # Единая точка входа с TLS
  caddy:
    image: caddy:2-alpine
    ports:
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
    networks:
      - internal
    restart: unless-stopped

networks:
  internal:
    driver: bridge

volumes:
  pb_data:
  chroma_data:
  ollama_models:
```

### Установка для клиента

```bash
#!/bin/bash
# install.sh — запуск в одну команду
echo "╔══════════════════════════════════════╗"
echo "║   Secure AI Assistant — Установка    ║"
echo "╚══════════════════════════════════════╝"

# 1. Проверки
command -v docker >/dev/null 2>&1 || { echo "❌ Docker не установлен"; exit 1; }
command -v docker compose >/dev/null 2>&1 || { echo "❌ Docker Compose не установлен"; exit 1; }

# 2. Копируем .env из шаблона
if [ ! -f .env ]; then
    cp .env.example .env
    echo "📝 Откройте .env и укажите API-ключи"
    echo "   Для air-gapped режима ключи не нужны"
    read -p "Нажмите Enter после настройки .env..."
fi

# 3. Выбор режима
echo ""
echo "Выберите режим работы:"
echo "  1) Cloud  — LLM в облаке, данные локально"
echo "  2) Hybrid — PII маскируются перед отправкой в облако"
echo "  3) Local  — всё локально (нужна GPU)"
read -p "Режим [1/2/3]: " mode

case $mode in
    1) sed -i 's/DEPLOY_MODE=.*/DEPLOY_MODE=cloud/' .env ;;
    2) sed -i 's/DEPLOY_MODE=.*/DEPLOY_MODE=hybrid/' .env ;;
    3) sed -i 's/DEPLOY_MODE=.*/DEPLOY_MODE=local/' .env
       docker compose -f docker-compose.airgapped.yml up -d
       echo "⏳ Загрузка модели Llama 3.1..."
       docker exec ollama ollama pull llama3.1:8b
       echo "✅ Готово! Откройте https://localhost"
       exit 0 ;;
esac

# 4. Запуск
docker compose up -d
echo ""
echo "✅ Ассистент запущен!"
echo "   🌐 Web UI:    http://localhost:3000"
echo "   🔧 Admin:     http://localhost:8090/_/"
echo "   📡 API:       http://localhost:8000/docs"
```

---

## 10. Зависимости Python (минимальные)

```toml
[project]
name = "secure-ai-assistant"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    # API
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic-settings>=2.5.0",

    # PocketBase client
    "pocketbase>=0.15.0",

    # DLP
    "presidio-analyzer>=2.2.361",
    "presidio-anonymizer>=2.2.361",

    # LLM
    "litellm>=1.57.0",

    # RAG
    "chromadb>=0.5.0",
    "sentence-transformers>=3.0.0",

    # Интеграции (РФ рынок)
    "fast-bitrix24>=3.0.0",       # Битрикс24 REST API (async, batching)
    "aiogram>=3.10.0",            # Telegram Bot
    "cryptography>=43.0.0",       # Fernet-шифрование credentials интеграций
    "python-docx>=1.1.0",         # Генерация DOCX-протоколов (MyMeet pipeline)

    # Utilities
    "httpx>=0.27.0",
    "structlog>=24.1.0",
]
```

**Всего ~13 зависимостей** вместо ~25 в enterprise-варианте. Нет Alembic, нет SQLAlchemy, нет Redis, нет PyCasbin, нет asyncpg.

---

## 11. Roadmap: 12 недель, 6 спринтов

### Sprint 1 (Недели 1–2): Фундамент + Business Context (PAI: TELOS)

**Результат:** PocketBase + AI Engine общаются, пользователи могут логиниться и отправлять запросы к LLM. Ассистент знает контекст компании.

Задачи:

- Настроить PocketBase: создать collections (users, tenants, conversations, audit_logs, documents, **business_context**, **memory_signals**) через миграции (`pb_migrations/`)
- Реализовать `pb_client.py` — обёртка над PocketBase Python SDK для аутентификации, CRUD-операций
- Создать FastAPI-сервер (`main.py`) с эндпоинтом `/api/chat`
- **Реализовать Business Context System (адаптация PAI TELOS):** collection `business_context` с 8 типами контекста (mission, goals, team, products, processes, challenges, vocabulary, tone). При каждом запросе релевантный контекст подгружается в system prompt
- Подключить LiteLLM — маршрутизация к OpenAI/Anthropic
- **Реализовать базовый Skill Router:** классификация запроса → выбор навыка (knowledge_qa, document_draft, data_analysis, meeting_prep, daily_digest)
- Docker Compose для 2 контейнеров
- Базовый health check и structured logging (structlog)

**Критерий готовности:** `curl -X POST /api/chat` возвращает ответ от LLM, сессия сохраняется в PocketBase.

---

### Sprint 2 (Недели 3–4): DLP и аудит

**Результат:** все запросы проходят через DLP-фильтр, действия логируются в append-only аудит.

Задачи:

- Интегрировать Presidio: `DLPService` со сканированием на входе и выходе
- Добавить русскоязычные recognizers (ИНН, СНИЛС, телефоны, паспорт)
- Реализовать `AuditLogger`: каждый запрос записывается в `audit_logs` с hash-цепочкой (SHA-256 от предыдущей записи)
- Добавить guardrails: prompt injection detection (regex-based для MVP)
- Режим `hybrid`: Presidio маскирует PII перед отправкой в облако, LLM получает `<ИМЯ>`, `<EMAIL>`, `<СКРЫТО>`
- Настроить PocketBase API Rules — audit_logs доступны только admin/manager на чтение

**Критерий готовности:** отправка сообщения `Позвони Иванову на +7-999-123-45-67` — номер маскируется, в аудите запись с `pii_detected: true`.

---

### Sprint 3 (Недели 5–6): RAG, knowledge base, Memory System и MyMeet (PAI: Memory + Skills)

**Результат:** ассистент отвечает на основе загруженных документов компании с учётом прав доступа. Система учится на обратной связи. Автоматическая генерация протоколов совещаний из MyMeet.

Задачи:

- Развернуть ChromaDB в embedded-режиме с persistent storage
- Реализовать `RAGService`: индексация документов из `documents` collection, поиск по tenant
- Текстовая экстракция: PDF (PyMuPDF), DOCX (python-docx), TXT/MD — через PocketBase file storage
- Per-tenant collections в ChromaDB: `tenant_{id}` — физическая изоляция данных
- Access-level фильтрация: user видит только `general`, manager — `general` + `managers`, admin — всё
- Эндпоинт `/api/documents/upload` — загрузка + автоматическая индексация
- Chunking: recursive character splitting, 500 tokens, overlap 50
- **Реализовать Memory System (адаптация PAI Memory):**
  - Collection `memory_signals` в PocketBase: ratings, follow-ups, edits
  - `MemoryService` с 3 уровнями (hot/warm/cold)
  - Capture signals после каждого взаимодействия
  - Warm memory: если пользователь часто спрашивает про X — давать более детальные ответы по X
  - Thumbs up/down в UI → запись в memory_signals → обучение
- **Реализовать MyMeet интеграцию (флагманский кейс):**
  - `MyMeetIntegration` — клиент API (get_recent_meetings, get_report, get_status, download)
  - `ProtocolGenerator` — полный pipeline: транскрипт → LLM (speaker mapping + term correction + protocol + tasks) → DOCX → Б24
  - PocketBase collections: `meetings_protocols`, `meeting_sites`, `employees_cache`, `term_dictionary`
  - Cron hook `check_new_meetings` (каждые 4 часа)
  - Матчинг площадок по ключевым словам в названии встречи
  - **Битрикс24 интеграция для протоколов:** `disk.folder.uploadfile` (DOCX), `tasks.task.add` (задачи), `im.message.add` (уведомление)

**Критерий готовности:** загружаем PDF с регламентом компании → спрашиваем «Каков порядок согласования отпусков?» → ответ на основе документа. Тестовое совещание MyMeet → markdown-протокол + DOCX в Б24 + задачи + уведомление в чат площадки. Thumbs down → запись в memory_signals.

---

### Sprint 4 (Недели 7–8): Web UI, Telegram Bot и Hook System (PAI: Hooks)

**Результат:** пользователи работают через web-интерфейс или Telegram. Бизнес-автоматизация через хуки.

Задачи:

- Web UI: React SPA (или Svelte для простоты), подключение к PocketBase SDK (JS)
  - Страница логина (email + password)
  - Чат-интерфейс с историей
  - **Thumbs up/down кнопки** (→ memory_signals для PAI Memory)
  - Загрузка документов (для admin/manager)
  - Просмотр аудит-логов (для admin/manager)
  - **Редактор Business Context** (для admin): mission, goals, team, products...
- Telegram Bot: `aiogram` (Python)
  - Привязка Telegram-аккаунта к пользователю в PocketBase
  - Чат с DLP-пайплайном
  - Команды: `/start`, `/history`, `/mode` (переключение модели)
- Web UI отдаётся через PocketBase static hosting (`pb_public/`)
- **Реализовать Hook System (адаптация PAI Hooks):**
  - `on_document_upload` → автоиндексация в RAG
  - `on_daily_schedule` → утренний дайджест для руководителей в Telegram
  - `on_pii_detected` → alert администратору
  - `on_low_rating` → лог в memory_signals для анализа

**Критерий готовности:** руководитель может залогиниться в web UI, задать вопрос, увидеть историю. Может задать тот же вопрос через Telegram.

---

### Sprint 5 (Недели 9–10): On-premise и air-gapped режим

**Результат:** продукт устанавливается на сервер клиента без доступа к интернету.

Задачи:

- Docker Compose для air-gapped: PocketBase + AI Engine + Ollama
- Скрипт сборки offline-пакета: все Docker images + модель Llama 3.1 8B (или 70B для мощных серверов) + embedding model
  - `build-offline-package.sh` → `secure-assistant-offline.tar.gz` (~15 GB)
- Caddy как reverse proxy с self-signed TLS (или Let's Encrypt для сервера с интернетом)
- Тестирование на чистой Ubuntu 22.04/24.04: установка из offline-пакета за 15 минут
- `install.sh` — интерактивный установщик (выбор режима, генерация secrets)
- Backup/restore скрипт: `pb_data/` + `chroma_data/` → единый архив

**Критерий готовности:** берём чистый сервер без интернета с GPU, копируем архив, запускаем `install.sh` — через 15 минут работающий ассистент.

---

### Sprint 6 (Недели 11–12): Hardening, тестирование, документация

**Результат:** production-ready продукт с документацией для клиентов.

Задачи:

- **Security hardening:**
  - Rate limiting (middleware в FastAPI: 60 запросов/мин на пользователя)
  - Input validation: максимальная длина промпта, sanitization
  - CORS, security headers
  - Container scanning (Trivy)
  - Dependency audit (`pip-audit`)

- **Тестирование:**
  - Unit tests: DLP, guardrails, RAG (pytest, ≥70% coverage)
  - Integration tests: полный pipeline — вход → DLP → LLM → DLP → аудит
  - DLP tests: корпус из 200+ примеров PII на русском и английском
  - Red-teaming: 50+ prompt injection попыток

- **Документация для клиентов:**
  - Quick Start Guide (1 страница — от скачивания до работающего ассистента)
  - Admin Guide (управление пользователями, загрузка документов, аудит)
  - Security Whitepaper (архитектура, DLP, режимы деплоя — для CTO/CISO клиента)
  - Compliance Mapping: соответствие 152-ФЗ / GDPR

- **Демо-стенд** для показа клиентам

**Критерий готовности:** клиент получает архив, читает Quick Start на 1 страницу, устанавливает за 15 минут, добавляет сотрудников, загружает документы, работает.

---

## 12. Что предложить клиентам: три тарифа

| | Starter | Business | Enterprise |
|---|---------|----------|------------|
| **Для кого** | до 50 человек | 50–500 человек | 500+ человек |
| **Режим** | Cloud | Hybrid | On-premise |
| **LLM** | GPT-4o-mini | GPT-4o / Claude | Локальная (Llama 3.1) |
| **DLP** | Базовый | Полный + custom | Полный + custom |
| **RAG** | 1 GB документов | 10 GB | Без лимита |
| **Аудит** | 30 дней | 1 год | Бессрочный |
| **Поддержка** | Email | Telegram + email | Выделенный инженер |
| **Установка** | SaaS (вы хостите) | Сервер клиента | Сервер клиента + GPU |

---

## 13. Сравнение: было → стало

| Аспект | Предыдущий план (enterprise) | Текущий план (PocketBase) |
|--------|------------------------------|--------------------------|
| Контейнеров в Docker Compose | 7–9 | 2–3 |
| Зависимостей Python | ~25 | ~13 |
| Время установки | 30–60 мин | 10–15 мин |
| Нужен DevOps | Да | Нет |
| Нужен DBA | Да (PostgreSQL) | Нет (SQLite) |
| Admin-панель | Разрабатывать | Готова из коробки |
| Auth + RBAC | PyCasbin + Keycloak | Встроено в PocketBase |
| Масштаб | 500+ пользователей | До 500 пользователей |
| Стоимость внедрения | Высокая | Средняя |
| Время до MVP | 12 недель (напряжённо) | 12 недель (реально) |

---

## 14. Риски и митигации

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| PocketBase не выдержит 500 concurrent users | Низкая (SQLite: ~5000 RPS на чтение) | Write-heavy операции (аудит) — batching, горизонтальное масштабирование через реплики |
| Presidio плохо работает с русским языком | Средняя | Custom recognizers + тестовый корпус из 200+ примеров, fallback на regex |
| ChromaDB теряет данные | Низкая | Persistent storage + ежедневный backup скрипт |
| Ollama медленно генерирует на слабом GPU | Высокая | Минимум RTX 3090/4090 или A100 для air-gapped; для остальных — hybrid-режим |
| Клиенты не смогут установить самостоятельно | Средняя | install.sh с проверками + Quick Start Guide + видео-инструкция (5 мин) |

---

## Вывод

Этот продукт стоит на трёх опорах:

**Ценность для бизнеса** — 6 конкретных сценариев, от «второго мозга руководителя» до «безопасного стратегического ассистента», каждый с измеримым ROI. Это не абстрактный «AI-помощник», а решение конкретных болей: информационная перегрузка, рутинные вопросы сотрудников, разрозненные данные, ручная подготовка документов.

**Архитектура PAI** — принципы Даниэля Мисслера (TELOS, Memory, Skills, Hooks) переносят продукт из категории «ещё один чат-бот» в категорию «система, которая знает вашу компанию и становится умнее с каждым днём». Business Context делает ответы релевантными, Memory System обеспечивает обучение, Skill Router направляет запросы в правильный pipeline, а Hook System автоматизирует рутину.

**Безопасность как УТП** — DLP-pipeline (Presidio), контроль доступа (PocketBase RBAC), иммутабельный аудит, три режима деплоя (cloud / hybrid / air-gapped). То, что OpenClaw архитектурно не может предложить.

Всё это — на простом стеке: 2 Docker-контейнера (PocketBase + Python AI Engine), установка за 10–15 минут, без DevOps и DBA.

**Следующий шаг:** реализация Sprint 1 — PocketBase + FastAPI + LiteLLM + Business Context за 2 недели.
