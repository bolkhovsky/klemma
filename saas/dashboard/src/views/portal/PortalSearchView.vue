<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { meetings as api, type MeetingsSearch, type SearchResultItem } from '@/api/client'
import { scoreStr, tagLabel, tagTone, toneInk, toneBg } from './helpers'
import { useSiteFilter } from './useSiteFilter'

const route = useRoute()
const { selected, siteParam, loaded, load } = useSiteFilter()

/** Deep link to the source protocol on the meetings view (expands + highlights it). */
function protocolLink(citekey: string): string {
  return `/${route.params.projectId}/portal/meetings?open=${encodeURIComponent(citekey)}`
}

const query = ref('дефицит труб')
const loading = ref(false)
const searched = ref(false)
const data = ref<MeetingsSearch | null>(null)
const keywordOnly = ref(false)

const words = computed(() =>
  query.value.toLowerCase().split(/\W+/).filter((w) => w.length > 2),
)
function isKeywordHit(r: SearchResultItem): boolean {
  const t = r.quote.toLowerCase()
  return words.value.some((w) => t.includes(w))
}
const results = computed<SearchResultItem[]>(() => {
  const all = data.value?.results || []
  return keywordOnly.value ? all.filter(isKeywordHit) : all
})
const missed = computed(() =>
  data.value ? data.value.semantic_count - data.value.keyword_count : 0,
)

// Guard against out-of-order responses (site switch while a search is in flight).
let seq = 0
async function run() {
  if (query.value.trim().length < 2) return
  const my = ++seq
  loading.value = true
  keywordOnly.value = false
  try {
    const res = await api.search(query.value.trim(), siteParam.value)
    if (my !== seq) return
    data.value = res
    searched.value = true
  } finally {
    if (my === seq) loading.value = false
  }
}

// Single watch source: initial search runs when the site registry is loaded,
// re-run the current query on site change.
watch(
  [loaded, selected],
  ([ok]) => {
    if (ok) run()
  },
  { immediate: true },
)

onMounted(() => {
  load()
})
</script>

<template>
  <div class="lr-fade" style="max-width:980px;margin:0 auto;padding:32px 40px 80px;display:flex;flex-direction:column;gap:18px;width:100%">
    <div>
      <h1 style="font-family:var(--font-display);font-size:24px;font-weight:700;color:var(--ink);letter-spacing:-0.01em;margin:0">Поиск</h1>
      <p style="margin:5px 0 0;font-size:14px;color:var(--ink-muted)">Смысловой поиск по всей истории совещаний — находит по сути, а не по словам</p>
    </div>

    <form @submit.prevent="run" style="display:flex;gap:10px;border:1px solid var(--accent);box-shadow:var(--ring-accent);border-radius:var(--radius-xl);background:var(--paper-white);padding:10px 10px 10px 16px;align-items:center">
      <span style="color:var(--ink-muted);display:inline-flex;flex-shrink:0">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="7.5" cy="7.5" r="5" /><path d="M11.2 11.2l4 4" /></svg>
      </span>
      <input v-model="query" placeholder="Найдите по смыслу…" style="flex:1;border:none;background:transparent;font:inherit;font-size:16px;color:var(--ink)" />
      <span style="font-family:var(--font-mono);font-size:12px;color:var(--ink-faint);flex-shrink:0">смысловой режим</span>
      <button type="submit" style="padding:8px 18px;background:var(--accent);color:#fff;border:1px solid var(--accent);border-radius:var(--radius-sm);font:inherit;font-size:14px;font-weight:500;cursor:pointer;flex-shrink:0">Искать</button>
    </form>

    <div v-if="loading" style="display:flex;justify-content:center;padding:30px">
      <span class="lr-spin" style="width:22px;height:22px;border-radius:50%;border:2px solid var(--accent);border-top-color:transparent;display:inline-block"></span>
    </div>

    <template v-else-if="searched && data">
      <!-- semantic vs keyword banner -->
      <div v-if="data.semantic_count" style="border:1px solid var(--amber-light);background:var(--amber-pale);border-radius:var(--radius-xl);padding:16px 20px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:22px">
          <div style="text-align:center"><div style="font-family:var(--font-mono);font-size:26px;font-weight:600;color:var(--accent);line-height:1">{{ data.semantic_count }}</div><div style="font-size:12px;color:var(--amber-deep);margin-top:3px">по смыслу</div></div>
          <div style="font-size:22px;color:var(--amber-light)">vs</div>
          <div style="text-align:center"><div style="font-family:var(--font-mono);font-size:26px;font-weight:600;color:var(--ink-muted);line-height:1">{{ data.keyword_count }}</div><div style="font-size:12px;color:var(--amber-deep);margin-top:3px">по словам</div></div>
          <div style="max-width:420px;font-size:14px;line-height:1.5;color:var(--amber-deep)">Поиск по ключевым словам «{{ data.query }}» нашёл бы только <b>{{ data.keyword_count }} из {{ data.semantic_count }}</b>. Ещё <b>{{ missed }}</b> релевантных фрагмента — без точного совпадения слов; их видит только смысловой слой.</div>
        </div>
        <button @click="keywordOnly = !keywordOnly" style="padding:8px 14px;background:var(--paper-white);color:var(--amber-deep);border:1px solid var(--amber-light);border-radius:var(--radius-sm);font:inherit;font-size:13px;font-weight:500;cursor:pointer;white-space:nowrap;flex-shrink:0">{{ keywordOnly ? 'Показать все по смыслу' : 'Показать только по словам' }}</button>
      </div>

      <div v-if="!results.length" style="padding:30px;text-align:center;border:1px dashed var(--rule);border-radius:var(--radius-xl);color:var(--ink-muted);font-size:14px">Ничего не найдено.</div>

      <div style="display:flex;flex-direction:column;gap:10px">
        <div
          v-for="(r, i) in results"
          :key="i"
          class="lr-fade"
          :style="{
            position:'relative', borderRadius:'var(--radius-xl)', background:'var(--paper-white)',
            padding:'16px 18px 14px 22px',
            border: keywordOnly && !isKeywordHit(r) ? '1px solid var(--rule)' : '1px solid var(--rule)',
            opacity: keywordOnly && !isKeywordHit(r) ? 0.4 : 1,
          }"
        >
          <span :style="{ position:'absolute', left:0, top:0, bottom:0, width:'3px', background: toneInk(tagTone(r.tag)), borderRadius:'var(--radius-xl) 0 0 var(--radius-xl)' }"></span>
          <div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start">
            <p style="font-family:var(--font-body-serif);font-style:italic;font-size:16px;line-height:1.55;color:var(--ink);margin:0">«{{ r.quote }}»</p>
            <span style="font-family:var(--font-mono);font-size:13px;color:var(--ink-muted);background:var(--rule-light);padding:2px 8px;border-radius:var(--radius-full);flex-shrink:0">{{ scoreStr(r.score) }}</span>
          </div>
          <div style="display:flex;align-items:center;gap:12px;margin-top:10px;flex-wrap:wrap">
            <span v-if="r.speaker" style="font-size:13px;color:var(--ink-light);font-weight:500">{{ r.speaker }}</span>
            <span v-if="r.speaker" style="color:var(--rule)">·</span>
            <span style="font-family:var(--font-mono);font-size:13px;color:var(--ink-muted);white-space:nowrap">{{ r.meeting }} · {{ r.time }}</span>
            <RouterLink v-if="r.citekey" :to="protocolLink(r.citekey)" class="lr-link" style="font-family:var(--font-mono);font-size:12px;white-space:nowrap">открыть протокол →</RouterLink>
            <span style="flex:1"></span>
            <span :style="{ display:'inline-flex', alignItems:'center', padding:'2px 9px', borderRadius:'var(--radius-full)', fontSize:'12px', fontWeight:'500', color: toneInk(tagTone(r.tag)), background: toneBg(tagTone(r.tag)) }">{{ tagLabel(r.tag) }}</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
