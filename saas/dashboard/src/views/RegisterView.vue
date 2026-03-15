<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth, ApiError } from '@/api/client'

const router = useRouter()
const email = ref('')
const password = ref('')
const name = ref('')
const error = ref('')
const loading = ref(false)

async function handleRegister() {
  error.value = ''
  loading.value = true
  try {
    const data = await auth.register(email.value, password.value, name.value)
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    router.push('/dashboard')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Ошибка регистрации'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-gray-50 px-4">
    <div class="w-full max-w-sm">
      <h1 class="text-center text-2xl font-bold text-gray-900">Регистрация в CiteQ</h1>

      <form class="mt-8 space-y-4" @submit.prevent="handleRegister">
        <div v-if="error" class="rounded-md bg-red-50 p-3 text-sm text-red-700">
          {{ error }}
        </div>

        <input
          v-model="name"
          type="text"
          placeholder="Имя (необязательно)"
          class="w-full rounded-lg border border-gray-300 px-4 py-2.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
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
          placeholder="Пароль (минимум 8 символов)"
          required
          minlength="8"
          class="w-full rounded-lg border border-gray-300 px-4 py-2.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />

        <button
          type="submit"
          :disabled="loading"
          class="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {{ loading ? 'Создаём аккаунт...' : 'Зарегистрироваться' }}
        </button>
      </form>

      <p class="mt-6 text-center text-sm text-gray-500">
        Уже есть аккаунт?
        <RouterLink to="/login" class="text-indigo-600 hover:text-indigo-500">Войти</RouterLink>
      </p>
    </div>
  </div>
</template>
