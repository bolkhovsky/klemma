<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { meetings as api, type AskAnswer } from '@/api/client'
import { humanizeModel } from '@/utils/model'
import { useSiteFilter } from './useSiteFilter'

const { selected, siteParam, siteName, loaded, load } = useSiteFilter()

const query = ref('Что происходит с контрактом по Турции и кто отвечает?')
const asked = ref('')
const loading = ref(false)
const data = ref<AskAnswer | null>(null)

const modelLabel = computed(() => (data.value ? humanizeModel(data.value.model) : ''))

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// Minimal, safe markdown → HTML for the answer (escape first, then format).
const answerHtml = computed(() => {
  const text = data.value?.answer || ''
  if (!text) return ''
  const lines = esc(text).split('\n')
  const out: string[] = []
  for (let raw of lines) {
    const line = raw.trim()
    if (!line) { out.push('<div style="height:8px"></div>'); continue }
    let html = line
      .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
      .replace(/\[(\d+)\]/g, '<sup class="lr-cite">$1</sup>')
    if (line.startsWith('## ')) {
      html = `<div style="font-size:15px;font-weight:600;color:var(--ink);margin:6px 0 2px">${html.slice(3)}</div>`
    } else if (line.startsWith('- ') || line.startsWith('• ')) {
      html = `<div style="display:flex;gap:8px"><span style="color:var(--accent)">•</span><span>${html.slice(2)}</span></div>`
    } else {
      html = `<p style="margin:0 0 6px;font-size:15px;line-height:1.62;color:var(--ink-light)">${html}</p>`
    }
    out.push(html)
  }
  return out.join('')
})

// Guard against out-of-order responses (site switch while an answer is in flight).
let seq = 0
async function ask(q?: string) {
  const text = (q ?? query.value).trim()
  if (!text) return
  const my = ++seq
  query.value = text
  asked.value = text
  loading.value = true
  data.value = null
  try {
    const res = await api.ask(text, siteParam.value)
    if (my !== seq) return
    data.value = res
  } finally {
    if (my === seq) loading.value = false
  }
}

// Single watch source: first question fires when the site registry is loaded;
// on site change, re-ask the last asked question within the new scope.
watch(
  [loaded, selected],
  ([ok]) => {
    if (ok) ask(asked.value || undefined)
  },
  { immediate: true },
)

onMounted(() => {
  load()
})
</script>

<template>
  <div class="lr-fade" style="max-width:860px;margin:0 auto;padding:32px 40px 60px;display:flex;flex-direction:column;gap:18px;width:100%">
    <div>
      <h1 style="font-family:var(--font-display);font-size:24px;font-weight:700;color:var(--ink);letter-spacing:-0.01em;margin:0">Вопрос</h1>
      <p style="margin:5px 0 0;font-size:14px;color:var(--ink-muted)">Диалог по всей истории совещаний. Каждый ответ — со ссылками на источники.</p>
    </div>

    <div v-if="asked" style="align-self:flex-end;max-width:80%;background:var(--accent);color:#fff;padding:11px 16px;border-radius:12px 12px 4px 12px;font-size:15px;line-height:1.5">{{ asked }}</div>

    <div v-if="loading" style="display:flex;align-items:center;gap:10px;color:var(--ink-muted);font-size:14px">
      <span class="lr-spin" style="width:18px;height:18px;border-radius:50%;border:2px solid var(--accent);border-top-color:transparent;display:inline-block"></span>
      Анализирую протоколы…
    </div>

    <div v-else-if="data" style="border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:22px 24px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
        <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted)">Ответ</span>
        <span style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px 3px 8px;border-radius:var(--radius-full);background:var(--violet-pale);color:var(--violet-deep);font-family:var(--font-mono);font-size:12px;font-weight:500;white-space:nowrap"><span style="color:var(--violet)">✦</span>сгенерировано · {{ modelLabel }}</span>
      </div>

      <div v-if="answerHtml" v-html="answerHtml"></div>
      <p v-else style="margin:0;font-size:15px;color:var(--ink-muted)">Не удалось сгенерировать ответ. Источники ниже.</p>

      <div v-if="data.sources.length" style="margin-top:18px;padding-top:16px;border-top:1px solid var(--rule-light)">
        <div style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted);margin-bottom:10px">Источники · {{ data.sources.length }}</div>
        <div style="display:flex;flex-direction:column;gap:8px">
          <div
            v-for="s in data.sources"
            :key="s.n"
            class="lr-card-hover"
            style="border:1px solid var(--rule);border-radius:var(--radius-sm);background:var(--paper-white);padding:12px 14px;display:flex;gap:12px"
          >
            <span style="font-family:var(--font-mono);font-size:13px;font-weight:600;color:var(--accent);flex-shrink:0;width:20px">{{ s.n }}</span>
            <div style="flex:1;min-width:0">
              <p style="font-family:var(--font-body-serif);font-style:italic;font-size:14px;line-height:1.5;color:var(--ink);margin:0">«{{ s.quote }}»</p>
              <div style="display:flex;align-items:center;gap:10px;margin-top:7px;flex-wrap:wrap">
                <span class="lr-link" style="font-family:var(--font-mono);font-size:12px;white-space:nowrap">{{ s.meeting }} · {{ s.date }} · {{ s.time }} ↗</span>
                <span v-if="s.speaker" style="color:var(--rule)">·</span>
                <span v-if="s.speaker" style="font-size:12px;color:var(--ink-muted)">{{ s.speaker }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="data && data.followups.length">
      <div style="font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-muted);margin-bottom:9px">Уточнить</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        <span v-for="(f, i) in data.followups" :key="i" @click="ask(f)" class="lr-card-hover" style="display:inline-flex;padding:7px 13px;border:1px solid var(--rule);border-radius:var(--radius-full);background:var(--paper-white);font-size:13px;color:var(--ink-light);cursor:pointer">{{ f }}</span>
      </div>
    </div>

    <form @submit.prevent="ask()" style="display:flex;gap:8px;border:1px solid var(--rule);border-radius:var(--radius-xl);background:var(--paper-white);padding:8px 8px 8px 16px;align-items:center;margin-top:4px">
      <input v-model="query" placeholder="Спросите по всей истории совещаний…" style="flex:1;border:none;background:transparent;font:inherit;font-size:15px;color:var(--ink)" />
      <button type="submit" :disabled="loading" style="padding:9px 18px;background:var(--accent);color:#fff;border:1px solid var(--accent);border-radius:var(--radius-sm);font:inherit;font-size:14px;font-weight:500;cursor:pointer;flex-shrink:0">Спросить</button>
    </form>
    <div style="font-size:12px;color:var(--ink-faint);padding:0 4px;margin-top:-10px">Поиск по: {{ siteName }}</div>
  </div>
</template>
