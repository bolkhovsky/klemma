<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, RouterLink } from 'vue-router'
import { analyze } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'

const router = useRouter()
const status = ref<{
  sources: { total: number; completed: number; pending: number; failed: number }
  coverage: { section: string; source_count: number }[]
  total_fragments: number
} | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    status.value = await analyze.status()
  } catch {
    router.push('/login')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <AppLayout>
    <div v-if="loading" class="py-12 text-center text-gray-400">Загрузка...</div>

    <div v-else-if="status" class="space-y-8">
      <!-- Stats cards -->
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <div class="rounded-lg border border-gray-200 bg-white p-5">
          <div class="text-2xl font-bold text-gray-900">{{ status.sources.total }}</div>
          <div class="text-sm text-gray-500">Источников</div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-5">
          <div class="text-2xl font-bold text-green-600">{{ status.sources.completed }}</div>
          <div class="text-sm text-gray-500">Обработано</div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-5">
          <div class="text-2xl font-bold text-yellow-600">{{ status.sources.pending }}</div>
          <div class="text-sm text-gray-500">В очереди</div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-5">
          <div class="text-2xl font-bold text-indigo-600">{{ status.total_fragments }}</div>
          <div class="text-sm text-gray-500">Фрагментов</div>
        </div>
      </div>

      <!-- Coverage -->
      <div class="rounded-lg border border-gray-200 bg-white p-6">
        <h2 class="text-lg font-semibold text-gray-900">Покрытие по разделам</h2>
        <div v-if="status.coverage.length === 0" class="mt-4 text-sm text-gray-400">
          Нет назначенных разделов. Добавьте источники и назначьте их разделам.
        </div>
        <div v-else class="mt-4 space-y-2">
          <div
            v-for="item in status.coverage"
            :key="item.section"
            class="flex items-center justify-between rounded-md bg-gray-50 px-4 py-2"
          >
            <span class="text-sm font-medium text-gray-700">{{ item.section }}</span>
            <span class="text-sm text-gray-500">{{ item.source_count }} источников</span>
          </div>
        </div>
      </div>

      <!-- Empty state CTA -->
      <div
        v-if="status.sources.total === 0"
        class="rounded-lg border-2 border-dashed border-gray-300 p-12 text-center"
      >
        <div class="text-4xl">📄</div>
        <h3 class="mt-3 text-lg font-semibold text-gray-900">Начните работу</h3>
        <p class="mt-1 text-sm text-gray-500">
          Добавьте первый источник, чтобы увидеть покрытие и фрагменты.
        </p>
        <RouterLink
          to="/library"
          class="mt-4 inline-block rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500"
        >
          Перейти в библиотеку
        </RouterLink>
      </div>
    </div>
  </AppLayout>
</template>
