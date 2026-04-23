<script setup lang="ts">
import { ref } from 'vue'
import { library, ApiError } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'

type BbtMatch = { citekey: string; external_citekey: string; strategy: string; title: string }
type BbtUnmatched = { bbt_citekey: string; title: string; first_author_lastname: string; year: number | null; doi: string | null }
type BbtAmbiguous = { bbt_citekey: string; title: string; candidates: string[] }
type BbtReport = { matched: BbtMatch[]; unmatched: BbtUnmatched[]; ambiguous: BbtAmbiguous[] }

const uploading = ref(false)
const error = ref<string>('')
const report = ref<BbtReport | null>(null)
const dragOver = ref(false)

async function handleFile(file: File) {
  if (!file.name.toLowerCase().endsWith('.json')) {
    error.value = 'Ожидается .json файл (экспорт Better BibTeX)'
    return
  }
  error.value = ''
  uploading.value = true
  try {
    report.value = await library.importBbt(file)
  } catch (e) {
    report.value = null
    error.value = e instanceof ApiError ? e.message : 'Ошибка загрузки'
  } finally {
    uploading.value = false
  }
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  const f = e.dataTransfer?.files[0]
  if (f) handleFile(f)
}

function onFileInput(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) handleFile(f)
}
</script>

<template>
  <AppLayout>
    <div class="p-6 max-w-[780px] mx-auto">
      <h1 class="text-[22px] font-semibold text-[#1a1a2e] mb-5">Настройки проекта</h1>

      <!-- Section: BBT import -->
      <section class="bg-white border border-[#e8e5df] rounded-xl p-5 mb-5">
        <h2 class="text-[15px] font-semibold text-[#1a1a2e] mb-1">Импорт ключей цитирования из Zotero</h2>
        <p class="text-[13px] text-[#6b6b8a] leading-relaxed mb-4">
          Загрузите JSON-экспорт Better BibTeX, чтобы Klemma использовала ваши локальные
          citekey'и (например, <span class="font-mono text-[12px]">voronina2023</span>)
          в генерируемых предложениях и ссылках вместо своих внутренних ключей.
          Pandoc + Biber с вашим локальным <span class="font-mono text-[12px]">.bib</span>
          будет собираться без ошибок.
        </p>

        <!-- Drop zone -->
        <div
          class="border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors"
          :class="dragOver ? 'border-[#0d7377] bg-[#e6f3f3]' : 'border-[#e8e5df] bg-[#fafaf8] hover:border-[#d4d0ca]'"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop="onDrop"
          @click="($refs.fileInput as HTMLInputElement).click()"
        >
          <input
            ref="fileInput"
            type="file"
            accept=".json,application/json"
            class="hidden"
            @change="onFileInput"
          />
          <div class="text-[#6b6b8a] text-[13px] leading-relaxed">
            <template v-if="uploading">
              <div class="inline-block h-4 w-4 animate-spin rounded-full border-2 border-[#0d7377] border-t-transparent mb-1"></div>
              <div>Сопоставляем записи…</div>
            </template>
            <template v-else>
              Перетащите <span class="font-mono text-[12px]">library.json</span>
              или нажмите для выбора
            </template>
          </div>
        </div>

        <div v-if="error" class="mt-3 rounded-md border border-[#c62828] bg-[#fff0f0] px-3 py-2 text-[13px] text-[#c62828]">
          {{ error }}
        </div>

        <!-- Report -->
        <div v-if="report" class="mt-5 space-y-4">
          <div class="flex gap-3 text-[13px]">
            <div class="flex-1 text-center py-2 rounded-md bg-[#dcfce7] text-[#15803d]">
              <div class="text-[22px] font-semibold">{{ report.matched.length }}</div>
              <div>совпало</div>
            </div>
            <div class="flex-1 text-center py-2 rounded-md bg-[#fef9c3] text-[#a16207]">
              <div class="text-[22px] font-semibold">{{ report.ambiguous.length }}</div>
              <div>неоднозначно</div>
            </div>
            <div class="flex-1 text-center py-2 rounded-md bg-[#f0ede8] text-[#6b6b8a]">
              <div class="text-[22px] font-semibold">{{ report.unmatched.length }}</div>
              <div>не найдено</div>
            </div>
          </div>

          <details v-if="report.matched.length" class="border border-[#e8e5df] rounded-md">
            <summary class="px-3 py-2 text-[13px] font-semibold cursor-pointer hover:bg-[#f0ede8]">
              Совпало ({{ report.matched.length }})
            </summary>
            <ul class="px-3 py-2 text-[13px] space-y-1 max-h-[220px] overflow-auto">
              <li v-for="(m, i) in report.matched" :key="`${m.citekey}|${m.external_citekey}|${i}`" class="flex justify-between gap-3">
                <span class="truncate">{{ m.title || m.citekey }}</span>
                <span class="text-[12px] text-[#6b6b8a] shrink-0">
                  <span class="font-mono">{{ m.citekey }}</span>
                  <span class="mx-1">→</span>
                  <span class="font-mono text-[#0d7377]">{{ m.external_citekey }}</span>
                  <span class="ml-1.5 text-[11px]">({{ m.strategy }})</span>
                </span>
              </li>
            </ul>
          </details>

          <details v-if="report.ambiguous.length" class="border border-[#fbbf24] rounded-md">
            <summary class="px-3 py-2 text-[13px] font-semibold cursor-pointer hover:bg-[#fef9c3] text-[#a16207]">
              Неоднозначно ({{ report.ambiguous.length }})
            </summary>
            <ul class="px-3 py-2 text-[13px] space-y-2 max-h-[220px] overflow-auto">
              <li v-for="(a, i) in report.ambiguous" :key="`${a.bbt_citekey}|${i}`">
                <span class="font-mono text-[12px]">{{ a.bbt_citekey }}</span>
                — «{{ a.title }}»
                <div class="text-[12px] text-[#6b6b8a] ml-4">
                  Совпадает с: {{ a.candidates.join(', ') }}
                </div>
              </li>
            </ul>
          </details>

          <details v-if="report.unmatched.length" class="border border-[#e8e5df] rounded-md">
            <summary class="px-3 py-2 text-[13px] font-semibold cursor-pointer hover:bg-[#f0ede8]">
              Не найдено ({{ report.unmatched.length }})
            </summary>
            <ul class="px-3 py-2 text-[13px] space-y-1 max-h-[220px] overflow-auto">
              <li v-for="(u, i) in report.unmatched" :key="`${u.bbt_citekey}|${i}`" class="flex justify-between gap-3">
                <span class="truncate">
                  {{ u.title || '(без заголовка)' }}
                  <span class="text-[12px] text-[#6b6b8a]">
                    {{ u.first_author_lastname }}<template v-if="u.year"> {{ u.year }}</template>
                  </span>
                </span>
                <span class="font-mono text-[12px] text-[#6b6b8a] shrink-0">{{ u.bbt_citekey }}</span>
              </li>
            </ul>
          </details>

          <p class="text-[12px] text-[#6b6b8a] leading-relaxed">
            Следующие сгенерированные предложения и пересборки черновиков будут использовать
            ваши citekey'и. Источники, которых нет в проекте, можно пока проигнорировать —
            они не создаются автоматически.
          </p>
        </div>
      </section>
    </div>
  </AppLayout>
</template>
