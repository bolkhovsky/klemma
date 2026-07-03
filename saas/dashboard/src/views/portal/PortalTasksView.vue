<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { meetings as api, type TasksBoard } from '@/api/client'
import { statColor, typeBg, typeInk } from './helpers'

const loading = ref(true)
const error = ref('')
const data = ref<TasksBoard | null>(null)
const MODEL = 'Claude Haiku 4.5'

onMounted(async () => {
  try {
    data.value = await api.tasks()
  } catch (e: any) {
    error.value = e?.message || 'Ошибка загрузки'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="lr-fade" style="max-width:1080px;margin:0 auto;padding:32px 40px 80px;display:flex;flex-direction:column;gap:24px;width:100%">
    <div>
      <h1 style="font-family:var(--font-display);font-size:24px;font-weight:700;color:var(--ink);letter-spacing:-0.01em;margin:0">Задачи</h1>
      <p style="margin:5px 0 0;font-size:14px;color:var(--ink-muted)">Агрегатный срез: тренды, зависшее и эскалации — без дублирования операционного борда</p>
    </div>

    <div v-if="loading" style="display:flex;justify-content:center;padding:40px">
      <span class="lr-spin" style="width:22px;height:22px;border-radius:50%;border:2px solid var(--accent);border-top-color:transparent;display:inline-block"></span>
    </div>
    <div v-else-if="error" style="padding:18px;border:1px solid var(--err);background:var(--err-bg);border-radius:var(--radius-xl);color:var(--err);font-size:14px">{{ error }}</div>

    <template v-else-if="data">
      <!-- Stat cards -->
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px">
        <div v-for="(st, i) in data.stats" :key="i" style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:16px 18px">
          <div :style="{ fontFamily:'var(--font-mono)', fontSize:'28px', fontWeight:'600', color: statColor(st.tone), lineHeight:1 }">{{ st.n }}</div>
          <div style="font-size:13px;color:var(--ink-muted);margin-top:6px">{{ st.label }}</div>
        </div>
      </div>

      <!-- Recurring themes -->
      <div style="display:flex;flex-direction:column;gap:14px">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted);flex-shrink:0">Повторяющиеся темы</span>
          <span style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px 3px 8px;border-radius:var(--radius-full);background:var(--violet-pale);color:var(--violet-deep);font-family:var(--font-mono);font-size:12px;font-weight:500;white-space:nowrap"><span style="color:var(--violet)">✦</span>выявлено ИИ · {{ MODEL }}</span>
          <span style="flex:1"></span>
          <span style="font-size:13px;color:var(--ink-muted)">Темы, всплывавшие в нескольких совещаниях</span>
        </div>
        <div v-if="!data.themes.length" style="padding:24px;text-align:center;border:1px dashed var(--rule);border-radius:var(--radius-xl);color:var(--ink-muted);font-size:14px">Повторяющихся тем пока не обнаружено.</div>
        <div v-else style="display:flex;flex-direction:column;gap:12px">
          <div
            v-for="(th, i) in data.themes"
            :key="i"
            :style="{ border: th.escalated ? '1px solid var(--err)' : '1px solid var(--rule)', borderRadius:'var(--radius-xl)', background:'var(--paper-white)', padding:'18px 20px' }"
          >
            <div style="display:flex;align-items:flex-start;gap:12px;justify-content:space-between">
              <div style="min-width:0">
                <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:8px">
                  <span style="font-size:16px;font-weight:600;color:var(--ink);line-height:1.3">{{ th.title }}</span>
                  <span v-if="th.escalated" style="display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:var(--radius-full);font-size:12px;font-weight:600;background:var(--err-bg);color:var(--err);white-space:nowrap">⚠ эскалация</span>
                  <span style="display:inline-flex;padding:2px 9px;border-radius:var(--radius-full);font-size:12px;font-weight:500;white-space:nowrap;background:var(--accent-pale);color:var(--accent-deep)">↑ {{ th.count }} совещаний</span>
                </div>
                <p style="margin:0;font-size:14px;line-height:1.55;color:var(--ink-light);max-width:720px">Тема обсуждалась на {{ th.count }} совещаниях — повод проверить, не буксует ли вопрос между площадками.</p>
              </div>
              <div style="text-align:right;flex-shrink:0">
                <div style="font-family:var(--font-mono);font-size:26px;font-weight:600;color:var(--ink);line-height:1">{{ th.count }}</div>
                <div style="font-size:12px;color:var(--ink-muted);margin-top:3px">совещаний</div>
              </div>
            </div>
            <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--rule-light)">
              <div style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:9px">Источники · {{ th.count }} совещаний</div>
              <div style="display:flex;flex-wrap:wrap;gap:8px">
                <span
                  v-for="(m, j) in th.meetings"
                  :key="j"
                  class="lr-card-hover"
                  style="display:inline-flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid var(--rule);border-radius:var(--radius-sm);background:var(--paper-white);white-space:nowrap"
                >
                  <span style="font-family:var(--font-mono);font-size:12px;color:var(--ink);font-weight:500">{{ m.date }}</span>
                  <span :style="{ display:'inline-flex', padding:'1px 7px', borderRadius:'var(--radius-full)', fontSize:'11px', fontWeight:'500', background: typeBg(m.type), color: typeInk(m.type) }">{{ m.type }}</span>
                  <span style="font-size:12px;color:var(--ink-muted)">{{ m.site }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Overdue charts -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
        <div style="display:flex;flex-direction:column;gap:12px">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Просрочено по исполнителю</span>
          <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:8px 18px 12px">
            <div v-if="!data.overdue_persons.length" style="padding:12px 0;font-size:13px;color:var(--ink-muted)">Просрочек нет.</div>
            <div v-for="(r, i) in data.overdue_persons" :key="i" style="display:flex;align-items:center;gap:12px;padding:9px 0">
              <span style="font-size:14px;color:var(--ink);width:188px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ r.name }}</span>
              <span style="flex:1;height:8px;border-radius:var(--radius-full);background:var(--rule-light);overflow:hidden"><span :style="{ display:'block', height:'100%', background:'var(--cta)', width: r.pct }"></span></span>
              <span style="font-family:var(--font-mono);font-size:14px;color:var(--ink);width:22px;text-align:right;flex-shrink:0">{{ r.n }}</span>
            </div>
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:12px">
          <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Просрочено по площадке</span>
          <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:8px 18px 12px">
            <div v-if="!data.overdue_sites.length" style="padding:12px 0;font-size:13px;color:var(--ink-muted)">Просрочек нет.</div>
            <div v-for="(r, i) in data.overdue_sites" :key="i" style="display:flex;align-items:center;gap:12px;padding:9px 0">
              <span style="font-size:14px;color:var(--ink);width:140px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ r.name }}</span>
              <span style="flex:1;height:8px;border-radius:var(--radius-full);background:var(--rule-light);overflow:hidden"><span :style="{ display:'block', height:'100%', background:'var(--amber)', width: r.pct }"></span></span>
              <span style="font-family:var(--font-mono);font-size:14px;color:var(--ink);width:22px;text-align:right;flex-shrink:0">{{ r.n }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Escalations -->
      <div style="display:flex;flex-direction:column;gap:12px">
        <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Открытые эскалации</span>
        <div style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);overflow:hidden">
          <div v-if="!data.escalations.length" style="padding:14px 18px;font-size:13px;color:var(--ink-muted)">Открытых эскалаций нет.</div>
          <div v-for="(e, i) in data.escalations" :key="i" style="display:flex;align-items:center;gap:14px;padding:14px 18px;border-top:1px solid var(--rule-light)">
            <span style="width:8px;height:8px;border-radius:50%;background:var(--err);flex-shrink:0"></span>
            <span style="flex:1;min-width:0;font-size:14px;font-weight:500;color:var(--ink)">{{ e.title }}</span>
            <span v-if="e.owner" style="font-size:13px;color:var(--ink-muted)">{{ e.owner }}</span>
            <span style="display:inline-flex;padding:2px 9px;border-radius:var(--radius-full);font-size:12px;font-weight:500;background:var(--rule-light);color:var(--ink-muted)">{{ e.site }}</span>
            <span style="font-family:var(--font-mono);font-size:12px;color:var(--err);width:110px;text-align:right;flex-shrink:0">{{ e.age }}</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
