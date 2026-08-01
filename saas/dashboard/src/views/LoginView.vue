<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { auth, userProjects, ApiError } from '@/api/client'
import { postLoginPath, safeRedirect } from '@/router'

const route = useRoute()
const router = useRouter()
const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleLogin() {
  error.value = ''
  loading.value = true
  try {
    const data = await auth.login(email.value, password.value)
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    // Адрес, с которого нас развернул beforeEach (внешний диплинк на встречу),
    // важнее дефолтной посадочной страницы — иначе ссылка из мобильного
    // приложения после ввода пароля приводит на общий список без ?open=.
    // safeRedirect отсекает чужие домены: страница логина не должна быть
    // open-redirect'ом.
    const back = safeRedirect(route.query.redirect)
    if (back) {
      router.push(back)
      return
    }
    try {
      const projectsData = await userProjects.list()
      const first = projectsData.projects[0]
      router.push(first ? postLoginPath(first.project_id) : '/library')
    } catch {
      router.push('/library')
    }
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Ошибка входа'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-gray-50 px-4">
    <div class="w-full max-w-sm">
      <h1 class="text-center text-2xl font-bold text-gray-900">Вход в LitResearch</h1>

      <form class="mt-8 space-y-4" @submit.prevent="handleLogin">
        <div v-if="error" class="rounded-md bg-red-50 p-3 text-sm text-red-700">
          {{ error }}
        </div>

        <input
          v-model="email"
          type="email"
          placeholder="Email"
          required
          class="w-full rounded-lg border border-gray-300 px-4 py-2.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <input
          v-model="password"
          type="password"
          placeholder="Пароль"
          required
          class="w-full rounded-lg border border-gray-300 px-4 py-2.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />

        <button
          type="submit"
          :disabled="loading"
          class="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {{ loading ? 'Входим...' : 'Войти' }}
        </button>
      </form>

      <p class="mt-6 text-center text-sm text-gray-500">
        Нет аккаунта?
        <RouterLink to="/register" class="text-indigo-600 hover:text-indigo-500">Зарегистрироваться</RouterLink>
      </p>
    </div>
  </div>
</template>
