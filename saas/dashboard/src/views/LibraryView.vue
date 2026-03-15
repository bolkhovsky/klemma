<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { library, ApiError } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'

interface Source {
  citekey: string
  title: string
  authors: string
  year: number | null
  status: string
  doi: string
}

const sources = ref<Source[]>([])
const loading = ref(true)
const showAddForm = ref(false)
const deleteConfirm = ref<string | null>(null)

// Add form state
const form = ref({ citekey: '', title: '', authors: '', year: '', doi: '' })
const formError = ref('')
const formLoading = ref(false)

async function loadSources() {
  loading.value = true
  try {
    const data = await library.list()
    sources.value = data.sources
  } catch {
    sources.value = []
  } finally {
    loading.value = false
  }
}

async function addSource() {
  formError.value = ''
  formLoading.value = true
  try {
    await library.add({
      citekey: form.value.citekey,
      title: form.value.title,
      authors: form.value.authors || undefined,
      year: form.value.year ? parseInt(form.value.year) : undefined,
      doi: form.value.doi || undefined,
    })
    form.value = { citekey: '', title: '', authors: '', year: '', doi: '' }
    showAddForm.value = false
    await loadSources()
  } catch (e) {
    formError.value = e instanceof ApiError ? e.message : 'Ошибка добавления'
  } finally {
    formLoading.value = false
  }
}

async function deleteSource(citekey: string) {
  try {
    await library.remove(citekey)
    deleteConfirm.value = null
    await loadSources()
  } catch {
    // silently fail — source may already be deleted
  }
}

onMounted(loadSources)
</script>

<template>
  <AppLayout>
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-semibold text-gray-900">Библиотека</h1>
      <button
        @click="showAddForm = !showAddForm"
        class="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500"
      >
        {{ showAddForm ? 'Отмена' : '+ Добавить источник' }}
      </button>
    </div>

    <!-- Add form -->
    <div v-if="showAddForm" class="mt-6 rounded-lg border border-gray-200 bg-white p-6">
      <h2 class="text-sm font-semibold text-gray-700">Новый источник</h2>
      <form class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2" @submit.prevent="addSource">
        <div v-if="formError" class="col-span-full rounded-md bg-red-50 p-3 text-sm text-red-700">
          {{ formError }}
        </div>
        <input
          v-model="form.citekey"
          placeholder="Citekey (например: smithML2020)"
          required
          class="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <input
          v-model="form.title"
          placeholder="Название статьи"
          required
          class="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <input
          v-model="form.authors"
          placeholder="Авторы (необязательно)"
          class="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <input
          v-model="form.year"
          type="number"
          placeholder="Год"
          min="1900"
          max="2099"
          class="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <input
          v-model="form.doi"
          placeholder="DOI (необязательно)"
          class="col-span-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <div class="col-span-full">
          <button
            type="submit"
            :disabled="formLoading"
            class="rounded-lg bg-indigo-600 px-6 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {{ formLoading ? 'Добавляем...' : 'Добавить' }}
          </button>
        </div>
      </form>
    </div>

    <!-- Sources table -->
    <div class="mt-6">
      <div v-if="loading" class="py-12 text-center text-gray-400">Загрузка...</div>

      <div v-else-if="sources.length === 0" class="rounded-lg border-2 border-dashed border-gray-300 p-12 text-center">
        <div class="text-4xl">📚</div>
        <h3 class="mt-3 text-lg font-semibold text-gray-900">Библиотека пуста</h3>
        <p class="mt-1 text-sm text-gray-500">Добавьте первый источник, чтобы начать работу.</p>
        <button
          @click="showAddForm = true"
          class="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500"
        >
          + Добавить источник
        </button>
      </div>

      <div v-else class="overflow-hidden rounded-lg border border-gray-200 bg-white">
        <table class="min-w-full divide-y divide-gray-200">
          <thead class="bg-gray-50">
            <tr>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Citekey</th>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Название</th>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Авторы</th>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Год</th>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Статус</th>
              <th class="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr v-for="src in sources" :key="src.citekey" class="hover:bg-gray-50">
              <td class="px-4 py-3 text-sm font-mono text-indigo-600">{{ src.citekey }}</td>
              <td class="max-w-xs truncate px-4 py-3 text-sm text-gray-900">{{ src.title || '—' }}</td>
              <td class="max-w-[150px] truncate px-4 py-3 text-sm text-gray-500">{{ src.authors || '—' }}</td>
              <td class="px-4 py-3 text-sm text-gray-500">{{ src.year || '—' }}</td>
              <td class="px-4 py-3">
                <span
                  class="inline-block rounded-full px-2 py-0.5 text-xs font-medium"
                  :class="{
                    'bg-green-100 text-green-700': src.status === 'completed',
                    'bg-yellow-100 text-yellow-700': src.status === 'pending',
                    'bg-red-100 text-red-700': src.status === 'failed',
                  }"
                >
                  {{ src.status }}
                </span>
              </td>
              <td class="px-4 py-3 text-right">
                <button
                  v-if="deleteConfirm !== src.citekey"
                  @click="deleteConfirm = src.citekey"
                  class="text-sm text-gray-400 hover:text-red-500"
                >
                  Удалить
                </button>
                <span v-else class="flex items-center gap-2">
                  <button
                    @click="deleteSource(src.citekey)"
                    class="text-sm font-medium text-red-600 hover:text-red-800"
                  >
                    Да
                  </button>
                  <button
                    @click="deleteConfirm = null"
                    class="text-sm text-gray-400 hover:text-gray-600"
                  >
                    Нет
                  </button>
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </AppLayout>
</template>
