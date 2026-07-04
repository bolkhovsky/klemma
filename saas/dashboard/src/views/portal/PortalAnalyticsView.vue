<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { meetings as api, type AnalyticsReport } from '@/api/client'
import { humanizeModel } from '@/utils/model'
import { toneInk, toneBg } from './helpers'
import { useSiteFilter } from './useSiteFilter'

const { selected, siteParam, loaded, load } = useSiteFilter()

const days = ref(90)
const loading = ref(true)
const error = ref('')
const report = ref<AnalyticsReport | null>(null)

const PERIODS = [
  { days: 30, label: '30 дней' },
  { days: 90, label: '3 месяца' },
  { days: 180, label: 'полгода' },
]

const title = computed(() =>
  selected.value === 'all' ? 'Аналитика — вся компания' : 'Аналитика площадки',
)
const modelLabel = computed(() => humanizeModel(report.value?.model))
const isEmpty = computed(
  () =>
    !!report.value &&
    (report.value.meetings_analyzed < 3 || report.value.detail === 'Недостаточно данных за период'),
)

// Guard against out-of-order responses when the user switches site/period
// while a (potentially 30–60s) generation request is still in flight.
let seq = 0
async function fetchReport(refresh = false) {
  const my = ++seq
  loading.value = true
  error.value = ''
  try {
    const r = await api.analytics({
      site: siteParam.value ?? '',
      days: days.value,
      ...(refresh ? { refresh: true } : {}),
    })
    if (my !== seq) return
    report.value = r
  } catch (e: any) {
    if (my !== seq) return
    report.value = null
    error.value = e?.message || 'Не удалось загрузить аналитику'
  } finally {
    if (my === seq) loading.value = false
  }
}

// Single watch source: fires once when sites finish loading (loaded flips),
// then on every site/period change. No separate onMounted fetch → no double fetch.
watch(
  [loaded, selected, days],
  ([ok]) => {
    if (ok) fetchReport()
  },
  { immediate: true },
)

onMounted(() => {
  load()
})

// ── Topic status / KPI trend / pattern severity presentation maps ─────────
const TOPIC_STATUS: Record<string, { label: string; ink: string; bg: string }> = {
  developing: { label: 'обсуждается', ink: 'var(--accent-deep)', bg: 'var(--accent-pale)' },
  stalled: { label: 'буксует', ink: 'var(--warn)', bg: 'var(--warn-bg)' },
  resolved: { label: 'решено', ink: 'var(--ok)', bg: 'var(--ok-bg)' },
  recurring_problem: { label: 'повторяющаяся проблема', ink: 'var(--err)', bg: 'var(--err-bg)' },
}
function topicStatus(s: string) {
  return TOPIC_STATUS[s] ?? { label: s, ink: 'var(--ink-muted)', bg: 'var(--rule-light)' }
}

// Arrows only — semantics (what counts as improvement) come from the model.
const KPI_TREND: Record<string, { arrow: string; color: string }> = {
  improving: { arrow: '↑', color: 'var(--ok)' },
  degrading: { arrow: '↓', color: 'var(--err)' },
  flat: { arrow: '→', color: 'var(--ink-muted)' },
  unclear: { arrow: '~', color: 'var(--ink-muted)' },
}
function kpiTrend(t: string) {
  return KPI_TREND[t] ?? KPI_TREND.unclear!
}

const SEVERITY: Record<string, { label: string; tone: string }> = {
  high: { label: 'высокий', tone: 'err' },
  medium: { label: 'средний', tone: 'warn' },
  low: { label: 'низкий', tone: 'mute' },
}
function severity(s: string) {
  return SEVERITY[s] ?? { label: s, tone: 'mute' }
}

// ── Weekly trend chart (hand-rolled SVG, grouped bars: tasks + escalations) ──
interface ChartBar {
  key: string
  label: string
  cx: number
  tasks: { x: number; y: number; h: number; v: number }
  esc: { x: number; y: number; h: number; v: number }
}
const chart = computed(() => {
  const weeks = report.value?.metrics.weeks ?? []
  const n = weeks.length
  if (!n) return null
  const groupW = Math.max(56, Math.floor(920 / n))
  const padL = 36
  const padR = 10
  const width = padL + n * groupW + padR
  const plotTop = 12
  const plotBottom = 128
  const plotH = plotBottom - plotTop
  const height = 158
  const maxVal = Math.max(1, ...weeks.map((w) => Math.max(w.tasks, w.escalations)))
  const barW = Math.min(16, Math.max(7, Math.floor(groupW * 0.22)))
  const labelEvery = n <= 8 ? 1 : n <= 16 ? 2 : 3
  const barH = (v: number) => (v > 0 ? Math.max(2, Math.round((v / maxVal) * plotH)) : 0)
  const bars: ChartBar[] = weeks.map((w, i) => {
    const cx = padL + i * groupW + groupW / 2
    const tH = barH(w.tasks)
    const eH = barH(w.escalations)
    return {
      key: w.week,
      label: i % labelEvery === 0 ? w.label : '',
      cx,
      tasks: { x: cx - barW - 1.5, y: plotBottom - tH, h: tH, v: w.tasks },
      esc: { x: cx + 1.5, y: plotBottom - eH, h: eH, v: w.escalations },
    }
  })
  const gridVals = [...new Set([maxVal, Math.round(maxVal / 2)])].filter((v) => v > 0)
  const grid = gridVals.map((v) => ({ v, y: plotBottom - (v / maxVal) * plotH }))
  return { width, height, bars, grid, barW, padL, plotBottom }
})
</script>

<template>
  <div class="lr-fade" style="max-width:1080px;margin:0 auto;padding:32px 40px 80px;display:flex;flex-direction:column;gap:22px;width:100%">
    <!-- Header -->
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div>
        <h1 style="font-family:var(--font-display);font-size:24px;font-weight:700;color:var(--ink);letter-spacing:-0.01em;margin:0">{{ title }}</h1>
        <p style="margin:5px 0 0;font-size:14px;color:var(--ink-muted)">Сквозные темы, KPI и паттерны по протоколам за период</p>
      </div>
      <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">
        <span
          v-for="p in PERIODS"
          :key="p.days"
          @click="days = p.days"
          :style="{
            padding: '5px 12px', borderRadius: 'var(--radius-full)', fontSize: '13px', cursor: 'pointer',
            fontWeight: days === p.days ? '500' : '400',
            background: days === p.days ? 'var(--accent-tint)' : 'var(--paper-white)',
            color: days === p.days ? 'var(--accent)' : 'var(--ink-light)',
            border: days === p.days ? 'none' : '1px solid var(--rule)',
          }"
        >{{ p.label }}</span>
        <button
          :disabled="loading"
          @click="fetchReport(true)"
          :style="{
            display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '5px 12px',
            background: 'var(--paper-white)', border: '1px solid var(--rule)', borderRadius: 'var(--radius-full)',
            font: 'inherit', fontSize: '13px', color: 'var(--ink-muted)',
            cursor: loading ? 'default' : 'pointer', opacity: loading ? 0.55 : 1, marginLeft: '4px',
          }"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.5 6a4.5 4.5 0 1 1-1.3-3.2M10.5 1v2.2H8.3" /></svg>
          Обновить
        </button>
      </div>
    </div>

    <!-- Loading: generation can take 30–60s server-side -->
    <div v-if="loading" style="display:flex;flex-direction:column;gap:14px">
      <div style="display:flex;align-items:center;gap:14px;border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:18px 20px">
        <span class="lr-spin" style="width:22px;height:22px;border-radius:50%;border:2px solid var(--accent);border-top-color:transparent;display:inline-block;flex-shrink:0"></span>
        <div>
          <div style="font-size:14px;font-weight:500;color:var(--ink)">Анализирую протоколы за период…</div>
          <div style="font-size:13px;color:var(--ink-muted);margin-top:2px">это может занять до минуты — идёт сквозной анализ тем, KPI и паттернов</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px">
        <div v-for="i in 4" :key="i" style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:16px 18px">
          <div style="height:24px;width:52px;background:var(--paper-3);border-radius:4px"></div>
          <div style="height:12px;width:80%;background:var(--rule-light);border-radius:4px;margin-top:10px"></div>
        </div>
      </div>
      <div v-for="i in 2" :key="'sk' + i" style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:18px 20px">
        <div style="height:14px;width:38%;background:var(--paper-3);border-radius:4px"></div>
        <div style="height:12px;width:90%;background:var(--rule-light);border-radius:4px;margin-top:12px"></div>
        <div style="height:12px;width:74%;background:var(--rule-light);border-radius:4px;margin-top:8px"></div>
      </div>
    </div>

    <!-- Error -->
    <div v-else-if="error" style="padding:18px;border:1px solid var(--err);background:var(--err-bg);border-radius:var(--radius-xl);display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <span style="color:var(--err);font-size:14px">{{ error }}</span>
      <button @click="fetchReport()" style="padding:7px 14px;background:var(--paper-white);color:var(--err);border:1px solid var(--err);border-radius:var(--radius-sm);font:inherit;font-size:13px;font-weight:500;cursor:pointer;flex-shrink:0">Повторить</button>
    </div>

    <!-- Empty: not enough meetings in the window -->
    <div v-else-if="isEmpty" style="padding:48px 24px;text-align:center;border:1px dashed var(--rule);border-radius:var(--radius-xl);background:var(--paper-white)">
      <div style="font-size:16px;font-weight:600;color:var(--ink)">Недостаточно протоколов за выбранный период</div>
      <div style="font-size:14px;color:var(--ink-muted);margin-top:6px;line-height:1.55">Для сквозной аналитики нужно минимум 3 протокола.<br />Попробуйте более длинный период — «3 месяца» или «полгода».</div>
    </div>

    <template v-else-if="report">
      <!-- Partial-report notice (e.g. AI unavailable — metrics only) -->
      <div v-if="report.detail" style="border:1px solid var(--amber-light);background:var(--amber-pale);border-radius:var(--radius-xl);padding:12px 18px;font-size:14px;color:var(--amber-deep)">{{ report.detail }}</div>

      <!-- 1. Executive summary -->
      <div v-if="report.summary" style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:20px 24px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Сводка за период</span>
          <span v-if="report.model" style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px 3px 8px;border-radius:var(--radius-full);background:var(--violet-pale);color:var(--violet-deep);font-family:var(--font-mono);font-size:12px;font-weight:500;white-space:nowrap"><span style="color:var(--violet)">✦</span>сгенерировано · {{ modelLabel }}</span>
          <span style="flex:1"></span>
          <span style="font-size:13px;color:var(--ink-muted)">{{ report.meetings_analyzed }} протоколов · {{ report.window.from }} — {{ report.window.to }}<template v-if="report.truncated"> (старейшие протоколы не вошли в анализ)</template></span>
        </div>
        <p style="font-family:var(--font-body-serif);font-style:italic;font-size:16px;line-height:1.62;color:var(--ink);margin:0;max-width:820px">{{ report.summary }}</p>
      </div>

      <!-- 2. Metrics: totals + weekly trend -->
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px">
        <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:16px 18px">
          <div style="font-family:var(--font-mono);font-size:28px;font-weight:600;color:var(--ink);line-height:1">{{ report.metrics.totals.meetings }}</div>
          <div style="font-size:13px;color:var(--ink-muted);margin-top:6px">совещаний</div>
        </div>
        <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:16px 18px">
          <div style="font-family:var(--font-mono);font-size:28px;font-weight:600;color:var(--ink);line-height:1">{{ report.metrics.totals.tasks }}</div>
          <div style="font-size:13px;color:var(--ink-muted);margin-top:6px">задач</div>
        </div>
        <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:16px 18px">
          <div style="font-family:var(--font-mono);font-size:28px;font-weight:600;color:var(--err);line-height:1">{{ report.metrics.totals.escalations }}</div>
          <div style="font-size:13px;color:var(--ink-muted);margin-top:6px">эскалаций</div>
        </div>
        <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:16px 18px">
          <div style="font-family:var(--font-mono);font-size:28px;font-weight:600;color:var(--warn);line-height:1">{{ report.metrics.totals.overdue }}</div>
          <div style="font-size:13px;color:var(--ink-muted);margin-top:6px">просрочено</div>
        </div>
      </div>

      <div v-if="chart" style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:16px 20px 12px">
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:10px;flex-wrap:wrap">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Динамика по неделям</span>
          <span style="flex:1"></span>
          <span style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-muted)"><span style="width:10px;height:10px;border-radius:2px;background:var(--accent);display:inline-block"></span>задачи</span>
          <span style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-muted)"><span style="width:10px;height:10px;border-radius:2px;background:var(--err);display:inline-block"></span>эскалации</span>
        </div>
        <div style="overflow-x:auto">
          <svg :width="chart.width" :height="chart.height" :viewBox="`0 0 ${chart.width} ${chart.height}`">
            <line
              v-for="g in chart.grid"
              :key="'g' + g.v"
              :x1="chart.padL"
              :x2="chart.width - 8"
              :y1="g.y"
              :y2="g.y"
              style="stroke:var(--rule-light)"
              stroke-dasharray="3 4"
            />
            <text
              v-for="g in chart.grid"
              :key="'gt' + g.v"
              :x="chart.padL - 8"
              :y="g.y + 4"
              text-anchor="end"
              style="font-family:var(--font-mono);font-size:11px;fill:var(--ink-faint)"
            >{{ g.v }}</text>
            <line :x1="chart.padL" :x2="chart.width - 8" :y1="chart.plotBottom" :y2="chart.plotBottom" style="stroke:var(--rule)" />
            <g v-for="b in chart.bars" :key="b.key">
              <rect :x="b.tasks.x" :y="b.tasks.y" :width="chart.barW" :height="b.tasks.h" rx="1.5" style="fill:var(--accent)">
                <title>{{ b.key }}: задачи {{ b.tasks.v }}</title>
              </rect>
              <rect :x="b.esc.x" :y="b.esc.y" :width="chart.barW" :height="b.esc.h" rx="1.5" style="fill:var(--err)">
                <title>{{ b.key }}: эскалации {{ b.esc.v }}</title>
              </rect>
              <text
                v-if="b.label"
                :x="b.cx"
                :y="chart.plotBottom + 18"
                text-anchor="middle"
                style="font-family:var(--font-mono);font-size:11px;fill:var(--ink-muted)"
              >{{ b.label }}</text>
            </g>
          </svg>
        </div>
      </div>

      <!-- 3. Темы периода -->
      <div v-if="report.topics.length" style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Темы периода</span>
          <span v-if="report.model" style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px 3px 8px;border-radius:var(--radius-full);background:var(--violet-pale);color:var(--violet-deep);font-family:var(--font-mono);font-size:12px;font-weight:500;white-space:nowrap"><span style="color:var(--violet)">✦</span>выявлено ИИ · {{ modelLabel }}</span>
          <span style="flex:1"></span>
          <span style="font-size:13px;color:var(--ink-muted)">Что обсуждается из совещания в совещание и как развивается</span>
        </div>
        <div
          v-for="(t, ti) in report.topics"
          :key="ti"
          style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:18px 20px"
        >
          <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
            <span style="font-size:15px;font-weight:600;color:var(--ink);line-height:1.35">{{ t.title }}</span>
            <span :style="{ display:'inline-flex', padding:'2px 9px', borderRadius:'var(--radius-full)', fontSize:'12px', fontWeight:'500', whiteSpace:'nowrap', color: topicStatus(t.status).ink, background: topicStatus(t.status).bg }">{{ topicStatus(t.status).label }}</span>
            <span style="flex:1"></span>
            <span style="font-size:13px;color:var(--ink-muted);white-space:nowrap">{{ t.meetings }} совещаний · <span style="font-family:var(--font-mono);font-size:12px">{{ t.first_seen }} → {{ t.last_seen }}</span></span>
          </div>
          <div v-if="t.timeline.length" style="margin-top:14px;border-left:2px solid var(--rule);padding-left:16px;display:flex;flex-direction:column;gap:10px">
            <div v-for="(e, ei) in t.timeline" :key="ei" style="position:relative">
              <span style="position:absolute;left:-21px;top:5px;width:8px;height:8px;border-radius:50%;background:var(--accent);border:1.5px solid var(--paper-white)"></span>
              <div style="font-family:var(--font-mono);font-size:11px;color:var(--ink-faint)">{{ e.date }}</div>
              <div style="font-size:13px;color:var(--ink-light);line-height:1.5;margin-top:1px">{{ e.note }}</div>
            </div>
          </div>
          <div v-if="t.insight" style="margin-top:14px;border-left:3px solid var(--accent);background:var(--accent-tint);border-radius:0 var(--radius-sm) var(--radius-sm) 0;padding:10px 14px;font-size:13px;font-style:italic;line-height:1.55;color:var(--ink-light)"><span style="font-style:normal;font-weight:600;color:var(--accent-deep)">Инсайт: </span>{{ t.insight }}</div>
        </div>
      </div>

      <!-- 4. KPI и показатели -->
      <div v-if="report.kpis.length" style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">KPI и показатели</span>
          <span v-if="report.model" style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px 3px 8px;border-radius:var(--radius-full);background:var(--violet-pale);color:var(--violet-deep);font-family:var(--font-mono);font-size:12px;font-weight:500;white-space:nowrap"><span style="color:var(--violet)">✦</span>выявлено ИИ · {{ modelLabel }}</span>
        </div>
        <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);overflow:hidden">
          <div v-for="(k, ki) in report.kpis" :key="ki" :style="{ display:'flex', alignItems:'flex-start', gap:'14px', padding:'13px 18px', borderTop: ki ? '1px solid var(--rule-light)' : 'none' }">
            <span :style="{ fontFamily:'var(--font-mono)', fontSize:'16px', fontWeight:'600', lineHeight:'1.3', width:'20px', flexShrink:0, textAlign:'center', color: kpiTrend(k.trend).color }">{{ kpiTrend(k.trend).arrow }}</span>
            <div style="flex:1;min-width:0">
              <div style="font-size:14px;font-weight:500;color:var(--ink);line-height:1.4">{{ k.name }}</div>
              <div v-if="k.evidence" style="font-size:13px;color:var(--ink-muted);font-style:italic;line-height:1.5;margin-top:3px">«{{ k.evidence }}»</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 5. Паттерны управления -->
      <div v-if="report.patterns.length" style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Паттерны управления</span>
          <span v-if="report.model" style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px 3px 8px;border-radius:var(--radius-full);background:var(--violet-pale);color:var(--violet-deep);font-family:var(--font-mono);font-size:12px;font-weight:500;white-space:nowrap"><span style="color:var(--violet)">✦</span>выявлено ИИ · {{ modelLabel }}</span>
        </div>
        <div
          v-for="(p, pi) in report.patterns"
          :key="pi"
          style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:16px 20px"
        >
          <div style="display:flex;align-items:flex-start;gap:12px">
            <span :style="{ display:'inline-flex', padding:'2px 9px', borderRadius:'var(--radius-full)', fontSize:'12px', fontWeight:'500', whiteSpace:'nowrap', flexShrink:0, marginTop:'1px', color: toneInk(severity(p.severity).tone), background: toneBg(severity(p.severity).tone) }">{{ severity(p.severity).label }}</span>
            <span style="font-size:14px;color:var(--ink);line-height:1.55">{{ p.observation }}</span>
          </div>
          <div v-if="p.recommendation" style="margin-top:10px;background:var(--accent-tint);border-radius:var(--radius-sm);padding:9px 13px;font-size:13px;line-height:1.55;color:var(--ink-light)"><span style="font-weight:600;color:var(--accent-deep)">Рекомендация: </span>{{ p.recommendation }}</div>
        </div>
      </div>

      <!-- 6. Top assignees -->
      <div v-if="report.metrics.top_assignees.length" style="display:flex;flex-direction:column;gap:12px">
        <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Топ исполнителей по задачам</span>
        <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);overflow:hidden;max-width:560px">
          <div style="display:flex;align-items:center;gap:12px;padding:9px 18px;background:var(--paper);border-bottom:1px solid var(--rule-light)">
            <span style="flex:1;font-family:var(--font-mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint)">Исполнитель</span>
            <span style="width:64px;text-align:right;font-family:var(--font-mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint)">задач</span>
            <span style="width:96px;text-align:right;font-family:var(--font-mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint)">просрочено</span>
          </div>
          <div v-for="(a, ai) in report.metrics.top_assignees" :key="ai" :style="{ display:'flex', alignItems:'center', gap:'12px', padding:'10px 18px', borderTop: ai ? '1px solid var(--rule-light)' : 'none' }">
            <span style="flex:1;min-width:0;font-size:14px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ a.name }}</span>
            <span style="width:64px;text-align:right;font-family:var(--font-mono);font-size:14px;color:var(--ink)">{{ a.tasks }}</span>
            <span :style="{ width:'96px', textAlign:'right', fontFamily:'var(--font-mono)', fontSize:'14px', color: a.overdue > 0 ? 'var(--err)' : 'var(--ink-muted)' }">{{ a.overdue }}</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
