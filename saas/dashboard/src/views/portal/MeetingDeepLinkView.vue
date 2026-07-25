<script setup lang="ts">
/**
 * `/meetings/:sourceId` — стабильная внешняя ссылка на одну встречу.
 *
 * Портальные экраны живут под `/:projectId/portal/…`, но внешние отправители
 * (мобильное приложение строит `portal_url` в klemma-stt/api/app.py) projectId
 * не знают и знать не должны. Этот экран резолвит проект пользователя — тем же
 * `userProjects.list()`, что и вход, — и заменяет себя настоящим маршрутом.
 *
 * Собственного UI почти нет: он живёт доли секунды. Неаутентифицированного
 * посетителя сюда не пустит `requiresAuth` — router.beforeEach отправит его на
 * логин, сохранив полный путь в `?redirect=`, а LoginView вернёт обратно.
 */
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { userProjects } from '@/api/client'

const route = useRoute()
const router = useRouter()
const error = ref('')

onMounted(async () => {
  const sourceId = String(route.params.sourceId || '')
  if (!sourceId) {
    router.replace('/library')
    return
  }
  try {
    const data = await userProjects.list()
    const first = data.projects[0]
    if (!first) {
      router.replace('/library')
      return
    }
    router.replace({
      path: `/${first.project_id}/portal/meetings`,
      query: { open: sourceId },
    })
  } catch {
    error.value = 'Не удалось открыть встречу. Попробуйте войти заново.'
  }
})
</script>

<template>
  <div class="flex min-h-screen items-center justify-center px-4 text-sm text-gray-500">
    <p v-if="error" class="text-red-600">{{ error }}</p>
    <p v-else>Открываем встречу…</p>
  </div>
</template>
