<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import '@/assets/portal.css'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => String(route.params.projectId || ''))
const dropOpen = ref(false)

const nav = [
  { name: 'portal-meetings', label: 'Совещания', seg: 'meetings' },
  { name: 'portal-tasks', label: 'Задачи', seg: 'tasks' },
  { name: 'portal-search', label: 'Поиск', seg: 'search' },
  { name: 'portal-question', label: 'Вопрос', seg: 'question' },
]

function go(seg: string) {
  router.push(`/${projectId.value}/portal/${seg}`)
  dropOpen.value = false
}
function isActive(name: string) {
  return route.name === name
}
</script>

<template>
  <div class="portal-root" style="display:flex;min-height:100vh;background:var(--paper)">
    <aside
      style="width:236px;flex-shrink:0;background:var(--paper-2);border-right:1px solid var(--rule);display:flex;flex-direction:column;padding:20px 14px;height:100vh;position:sticky;top:0;align-self:flex-start"
    >
      <div style="padding:4px 8px 22px">
        <span style="font-family:var(--font-display);font-weight:700;font-size:22px;letter-spacing:-0.01em;color:var(--ink)">Клемма</span>
        <span style="display:block;margin-top:3px;font-family:var(--font-mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint)">портал совещаний</span>
      </div>

      <div style="position:relative;margin:0 0 20px">
        <button
          @click="dropOpen = !dropOpen"
          style="width:100%;padding:8px 10px;background:var(--paper-white);border:1px solid var(--rule);border-radius:var(--radius-sm);display:flex;align-items:center;justify-content:space-between;cursor:pointer;text-align:left;font:inherit"
        >
          <span style="display:flex;flex-direction:column;line-height:1.25;min-width:0">
            <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-faint)">Область</span>
            <span style="font-size:13px;font-weight:500;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Бонум — вся компания</span>
          </span>
          <span style="color:var(--ink-muted);display:inline-flex">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5l3 3 3-3" /></svg>
          </span>
        </button>
        <div
          v-if="dropOpen"
          style="position:absolute;top:calc(100% + 6px);left:0;right:0;background:var(--paper-white);border:1px solid var(--rule);border-radius:6px;box-shadow:var(--shadow-pop);padding:6px;z-index:40"
        >
          <div style="padding:6px 8px 4px;font-family:var(--font-mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-faint)">Область данных</div>
          <button style="width:100%;text-align:left;padding:7px 8px;background:var(--accent-tint);color:var(--accent);border:none;border-radius:4px;font:inherit;font-size:13px;font-weight:500;cursor:pointer">Бонум — вся компания</button>
          <button style="width:100%;text-align:left;padding:7px 8px;background:transparent;color:var(--ink-light);border:none;border-radius:4px;font:inherit;font-size:13px;cursor:pointer">Площадка Челябинск</button>
          <button style="width:100%;text-align:left;padding:7px 8px;background:transparent;color:var(--ink-light);border:none;border-radius:4px;font:inherit;font-size:13px;cursor:pointer">Площадка Тольятти</button>
        </div>
      </div>

      <div style="font-family:var(--font-mono);font-size:11px;color:var(--ink-muted);letter-spacing:.14em;text-transform:uppercase;padding:8px 10px 6px">Разделы</div>

      <button
        v-for="item in nav"
        :key="item.name"
        class="lr-nav"
        :data-active="isActive(item.name)"
        @click="go(item.seg)"
        style="display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:5px;font:inherit;font-size:13px;color:var(--ink-light);background:transparent;border:none;cursor:pointer;text-align:left;margin-bottom:2px"
      >
        <span style="width:14px;display:inline-flex;flex-shrink:0;color:var(--ink-muted)">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
            <rect x="1.5" y="2.6" width="11" height="9.9" rx="1.4" /><path d="M1.5 5.2h11M4.3 1.4v2.2M9.7 1.4v2.2" />
          </svg>
        </span>
        <span>{{ item.label }}</span>
      </button>

      <div style="flex:1"></div>

      <div style="margin-top:auto;padding-top:16px;border-top:1px solid var(--rule)">
        <div style="padding:8px 8px 4px;font-size:12px;color:var(--ink-muted)">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px">
            <span>Запросы к ИИ</span>
            <span><span style="font-family:var(--font-mono);color:var(--ink-light);font-weight:500">847</span><span style="color:var(--ink-faint)"> / 1000</span></span>
          </div>
          <div style="height:3px;background:var(--paper-3);border-radius:2px;overflow:hidden"><div style="height:100%;width:85%;background:var(--accent)"></div></div>
          <div style="font-size:12px;color:var(--ink-faint);margin-top:5px">в этом месяце · обновится 1-го</div>
        </div>
        <div style="display:flex;align-items:center;gap:10px;padding:10px 8px 4px">
          <span style="width:28px;height:28px;border-radius:50%;background:var(--accent-tint);color:var(--accent);display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;flex-shrink:0">ИБ</span>
          <span style="flex:1;display:flex;align-items:center;gap:6px;min-width:0">
            <span style="font-size:13px;font-weight:500;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Бонум Демо</span>
            <span style="padding:1px 6px;background:var(--picked-tint);color:var(--picked);border-radius:3px;font-size:11px;font-weight:600">COO</span>
          </span>
        </div>
      </div>
    </aside>

    <main style="flex:1;min-width:0;display:flex;flex-direction:column">
      <RouterView />
    </main>
  </div>
</template>
