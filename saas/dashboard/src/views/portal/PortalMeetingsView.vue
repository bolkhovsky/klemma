<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { meetings as api, type MeetingItem, type MeetingsList } from '@/api/client'
import {
  dayOf, monOf, typeBg, typeInk, toneInk, toneBg, avatarBg, avatarFg, dueColor,
} from './helpers'
import { useSiteFilter } from './useSiteFilter'

const { selected, siteParam, siteName, loaded, load } = useSiteFilter()

const loading = ref(true)
const error = ref('')
const data = ref<MeetingsList>({ meetings: [], stats: { meetings: 0, tasks: 0, escalations: 0 } })
const openId = ref<string | null>(null)
const typeFilter = ref('Все типы')
const days = ref(14)
const periodOpen = ref(false)
const MODEL = 'Claude Haiku 4.5'

const TYPES = ['Все типы', 'ОМС', 'Scrum', 'Продажи']
const PERIOD_OPTIONS = [7, 14, 30, 90]

const filtered = computed<MeetingItem[]>(() =>
  typeFilter.value === 'Все типы'
    ? data.value.meetings
    : data.value.meetings.filter((m) => m.type === typeFilter.value),
)

function toggle(id: string) {
  openId.value = openId.value === id ? null : id
}

function setPeriod(d: number) {
  days.value = d
  periodOpen.value = false
}

// Guard against out-of-order responses on rapid site/period switching.
let seq = 0
async function fetchData() {
  const my = ++seq
  loading.value = true
  error.value = ''
  try {
    const res = await api.list({ site: siteParam.value, days: days.value })
    if (my !== seq) return
    data.value = res
    const first = res.meetings[0]
    openId.value = first ? first.id : null
  } catch (e: any) {
    if (my !== seq) return
    error.value = e?.message || 'Ошибка загрузки'
  } finally {
    if (my === seq) loading.value = false
  }
}

// Single watch source: fires once when the site registry finishes loading,
// then on every site/period change — no separate onMounted fetch.
watch(
  [loaded, selected, days],
  ([ok]) => {
    if (ok) fetchData()
  },
  { immediate: true },
)

onMounted(() => {
  load()
})
</script>

<template>
  <div class="lr-fade" style="max-width:1080px;margin:0 auto;padding:32px 40px 80px;display:flex;flex-direction:column;gap:22px;width:100%">
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div>
        <h1 style="font-family:var(--font-display);font-size:24px;font-weight:700;color:var(--ink);letter-spacing:-0.01em;margin:0">Совещания</h1>
        <p style="margin:5px 0 0;font-size:14px;color:var(--ink-muted)">История протоколов · смысловой слой над совещаниями компании</p>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <span style="display:inline-flex;align-items:center;gap:8px;padding:7px 12px;background:var(--paper-white);border:1px solid var(--rule);border-radius:var(--radius-sm);font-size:13px;color:var(--ink-light);max-width:280px" :title="siteName">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint);flex-shrink:0">Площадка</span>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ siteName }}</span>
        </span>
        <div style="position:relative">
          <button @click="periodOpen = !periodOpen" style="display:inline-flex;align-items:center;gap:8px;padding:7px 12px;background:var(--paper-white);border:1px solid var(--rule);border-radius:var(--radius-sm);font:inherit;font-size:13px;color:var(--ink-light);cursor:pointer">
            <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint)">Период</span>{{ days }} дней
            <span style="color:var(--ink-muted);display:inline-flex">
              <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5l3 3 3-3" /></svg>
            </span>
          </button>
          <div
            v-if="periodOpen"
            style="position:absolute;top:calc(100% + 6px);right:0;min-width:128px;background:var(--paper-white);border:1px solid var(--rule);border-radius:6px;box-shadow:var(--shadow-pop);padding:6px;z-index:30"
          >
            <button
              v-for="p in PERIOD_OPTIONS"
              :key="p"
              @click="setPeriod(p)"
              :style="{
                width: '100%', textAlign: 'left', padding: '7px 8px', border: 'none', borderRadius: '4px',
                font: 'inherit', fontSize: '13px', cursor: 'pointer',
                background: days === p ? 'var(--accent-tint)' : 'transparent',
                color: days === p ? 'var(--accent)' : 'var(--ink-light)',
                fontWeight: days === p ? '500' : '400',
              }"
            >{{ p }} дней</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Stat bar -->
    <div style="display:flex;align-items:stretch;background:var(--paper-white);border:1px solid var(--rule);border-radius:var(--radius-xl);overflow:hidden">
      <div style="flex:1;padding:14px 20px;display:flex;flex-direction:column;gap:2px">
        <span style="font-family:var(--font-mono);font-size:22px;font-weight:600;color:var(--ink);line-height:1">{{ data.stats.meetings }}</span>
        <span style="font-size:12px;color:var(--ink-muted)">совещаний за период</span>
      </div>
      <div style="width:1px;background:var(--rule-light)"></div>
      <div style="flex:1;padding:14px 20px;display:flex;flex-direction:column;gap:2px">
        <span style="font-family:var(--font-mono);font-size:22px;font-weight:600;color:var(--ink);line-height:1">{{ data.stats.tasks }}</span>
        <span style="font-size:12px;color:var(--ink-muted)">задач извлечено</span>
      </div>
      <div style="width:1px;background:var(--rule-light)"></div>
      <div style="flex:1;padding:14px 20px;display:flex;flex-direction:column;gap:2px">
        <span style="font-family:var(--font-mono);font-size:22px;font-weight:600;color:var(--err);line-height:1">{{ data.stats.escalations }}</span>
        <span style="font-size:12px;color:var(--ink-muted)">эскалаций · требуют внимания</span>
      </div>
    </div>

    <!-- Type tabs -->
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div style="display:flex;gap:7px;align-items:center">
        <span
          v-for="t in TYPES"
          :key="t"
          @click="typeFilter = t"
          :style="{
            padding: '5px 12px', borderRadius: 'var(--radius-full)', fontSize: '13px', cursor: 'pointer',
            fontWeight: typeFilter === t ? '500' : '400',
            background: typeFilter === t ? 'var(--accent-tint)' : 'var(--paper-white)',
            color: typeFilter === t ? 'var(--accent)' : 'var(--ink-light)',
            border: typeFilter === t ? 'none' : '1px solid var(--rule)',
          }"
        >{{ t }}</span>
      </div>
      <span style="font-size:13px;color:var(--ink-muted)">Показано <span style="font-family:var(--font-mono);color:var(--ink-light)">{{ filtered.length }}</span> протоколов</span>
    </div>

    <!-- Loading / empty -->
    <div v-if="loading" style="display:flex;justify-content:center;padding:40px">
      <span class="lr-spin" style="width:22px;height:22px;border-radius:50%;border:2px solid var(--accent);border-top-color:transparent;display:inline-block"></span>
    </div>
    <div v-else-if="error" style="padding:18px;border:1px solid var(--err);background:var(--err-bg);border-radius:var(--radius-xl);color:var(--err);font-size:14px">{{ error }}</div>
    <div v-else-if="!filtered.length" style="padding:40px;text-align:center;border:1px dashed var(--rule);border-radius:var(--radius-xl);color:var(--ink-muted);font-size:14px">Нет протоколов для показа.</div>

    <!-- Meeting rows -->
    <div v-else style="display:flex;flex-direction:column;gap:10px">
      <div
        v-for="m in filtered"
        :key="m.id"
        style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);overflow:hidden"
      >
        <button
          class="lr-mrow"
          @click="toggle(m.id)"
          style="display:flex;align-items:center;gap:16px;width:100%;padding:14px 18px;background:transparent;border:none;cursor:pointer;text-align:left;font:inherit"
        >
          <div style="display:flex;flex-direction:column;align-items:center;width:40px;flex-shrink:0">
            <span style="font-family:var(--font-mono);font-size:20px;font-weight:600;color:var(--ink);line-height:1">{{ dayOf(m.date) }}</span>
            <span style="font-size:12px;color:var(--ink-muted)">{{ monOf(m.date) }}</span>
          </div>
          <div style="width:1px;height:36px;background:var(--rule);flex-shrink:0"></div>
          <div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0;width:124px">
            <span :style="{ display:'inline-flex', alignSelf:'flex-start', padding:'2px 9px', borderRadius:'var(--radius-full)', fontSize:'12px', fontWeight:'500', background: typeBg(m.type), color: typeInk(m.type) }">{{ m.type }}</span>
            <span style="font-family:var(--font-mono);font-size:12px;color:var(--ink-muted)">{{ m.site }} · {{ m.time }}</span>
          </div>
          <div style="flex:1;min-width:0">
            <span style="font-size:15px;font-weight:600;color:var(--ink);display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ m.title }}</span>
            <span style="font-size:13px;color:var(--ink-muted)">{{ m.tasks }} задач извлечено</span>
          </div>
          <div style="display:flex;align-items:center;flex-shrink:0;padding-left:6px">
            <span
              v-for="(sp, i) in m.speakers.slice(0, 4)"
              :key="i"
              :style="{ width:'26px', height:'26px', borderRadius:'50%', display:'inline-flex', alignItems:'center', justifyContent:'center', fontSize:'11px', fontWeight:'600', marginLeft:'-6px', border:'1.5px solid var(--paper-white)', background: avatarBg(i), color: avatarFg(i) }"
            >{{ sp }}</span>
          </div>
          <div style="display:flex;gap:6px;flex-shrink:0;width:170px;justify-content:flex-end">
            <span
              v-for="(ch, i) in m.chips"
              :key="i"
              :style="{ display:'inline-flex', alignItems:'center', padding:'2px 9px', borderRadius:'var(--radius-full)', fontSize:'12px', fontWeight:'500', whiteSpace:'nowrap', color: toneInk(ch.tone), background: toneBg(ch.tone) }"
            >{{ ch.label }}</span>
          </div>
          <span :style="{ display:'inline-flex', color:'var(--ink-muted)', flexShrink:0, transform: openId === m.id ? 'rotate(180deg)' : 'none', transition:'transform .2s' }">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" /></svg>
          </span>
        </button>

        <div v-if="openId === m.id" style="border-top:1px solid var(--rule-light);background:var(--paper);padding:18px 22px 20px 74px;display:flex;flex-direction:column;gap:18px">
          <div>
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
              <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Сводка совещания</span>
              <span style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px 3px 8px;border-radius:var(--radius-full);background:var(--violet-pale);color:var(--violet-deep);font-family:var(--font-mono);font-size:12px;font-weight:500;white-space:nowrap"><span style="color:var(--violet)">✦</span>сгенерировано · {{ MODEL }}</span>
            </div>
            <p style="margin:0;font-size:14px;line-height:1.6;color:var(--ink-light);max-width:760px">{{ m.summary }}</p>
          </div>
          <div v-if="m.decisions.length">
            <div style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted);margin-bottom:8px">Принятые решения</div>
            <div style="display:flex;flex-direction:column;gap:6px">
              <div v-for="(d, i) in m.decisions" :key="i" style="display:flex;align-items:flex-start;gap:8px;font-size:14px;color:var(--ink)"><span style="color:var(--ok);flex-shrink:0">✓</span><span>{{ d }}</span></div>
            </div>
          </div>
          <div v-if="m.task_list.length">
            <div style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted);margin-bottom:8px">Извлечённые задачи</div>
            <div style="border:1px solid var(--rule);border-radius:var(--radius-sm);background:var(--paper-white);overflow:hidden">
              <div v-for="(t, i) in m.task_list" :key="i" style="display:flex;align-items:center;gap:12px;padding:11px 14px;border-top:1px solid var(--rule-light)">
                <span :style="{ width:'7px', height:'7px', borderRadius:'50%', background: dueColor(t.overdue), flexShrink:0 }"></span>
                <span style="flex:1;min-width:0;font-size:14px;color:var(--ink)">{{ t.title }}</span>
                <span style="font-size:13px;color:var(--ink-muted);flex-shrink:0">{{ t.who }}</span>
                <span :style="{ fontFamily:'var(--font-mono)', fontSize:'12px', color: dueColor(t.overdue), flexShrink:0, width:'130px', textAlign:'right' }">{{ t.due }}</span>
                <span class="lr-link" style="font-family:var(--font-mono);font-size:12px;flex-shrink:0;white-space:nowrap;width:54px">{{ t.time }} ↗</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
