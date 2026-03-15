<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { analyze, auth } from '@/api/client'

const router = useRouter()
const user = ref<{ email: string; name: string } | null>(null)
const status = ref<{
  sources: { total: number; completed: number; pending: number; failed: number }
  coverage: { section: string; source_count: number }[]
  total_fragments: number
} | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    const [userData, statusData] = await Promise.all([auth.me(), analyze.status()])
    user.value = userData
    status.value = statusData
  } catch {
    router.push('/login')
  } finally {
    loading.value = false
  }
})

function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  router.push('/login')
}
</script>

<template>
  <div class="min-h-screen bg-gray-50">
    <!-- Header -->
    <header class="border-b border-gray-200 bg-white px-6 py-4">
      <div class="mx-auto flex max-w-5xl items-center justify-between">
        <h1 class="text-lg font-semibold text-gray-900">CiteQ</h1>
        <div class="flex items-center gap-4">
          <span v-if="user" class="text-sm text-gray-500">{{ user.email }}</span>
          <button
            @click="logout"
            class="text-sm text-gray-400 hover:text-gray-600"
          >
            Выйти
          </button>
        </div>
      </div>
    </header>

    <!-- Content -->
    <main class="mx-auto max-w-5xl px-6 py-8">
      <div v-if="loading" class="text-center text-gray-400">Загрузка...</div>

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
        </div>
      </div>
    </main>
  </div>
</template>
