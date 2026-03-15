<script setup lang="ts">
import { RouterLink, RouterView, useRouter } from 'vue-router'

const router = useRouter()

function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  router.push('/login')
}
</script>

<template>
  <div class="min-h-screen bg-gray-50">
    <header class="border-b border-gray-200 bg-white px-6 py-3">
      <div class="mx-auto flex max-w-5xl items-center justify-between">
        <div class="flex items-center gap-8">
          <RouterLink to="/dashboard" class="text-lg font-semibold text-gray-900">CiteQ</RouterLink>
          <nav class="flex gap-1">
            <RouterLink
              to="/dashboard"
              class="rounded-md px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900"
              active-class="bg-gray-100 text-gray-900"
            >
              Обзор
            </RouterLink>
            <RouterLink
              to="/library"
              class="rounded-md px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900"
              active-class="bg-gray-100 text-gray-900"
            >
              Библиотека
            </RouterLink>
          </nav>
        </div>
        <button @click="logout" class="text-sm text-gray-400 hover:text-gray-600">
          Выйти
        </button>
      </div>
    </header>
    <main class="mx-auto max-w-5xl px-6 py-8">
      <slot />
    </main>
  </div>
</template>
