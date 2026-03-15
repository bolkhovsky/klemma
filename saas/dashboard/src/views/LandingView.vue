<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <div class="min-h-screen bg-white">
    <!-- Hero -->
    <header class="mx-auto max-w-4xl px-6 py-24 text-center">
      <h1 class="text-5xl font-bold tracking-tight text-gray-900">
        LitResearch
      </h1>
      <p class="mt-4 text-xl text-gray-600">
        AI-ассистент для академического письма
      </p>
      <p class="mt-2 text-lg text-gray-500">
        Загрузите PDF — получите фрагменты, покрытие и рекомендации
      </p>
      <div class="mt-10 flex justify-center gap-4">
        <RouterLink
          to="/register"
          class="rounded-lg bg-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500"
        >
          Начать бесплатно
        </RouterLink>
        <RouterLink
          to="/login"
          class="rounded-lg border border-gray-300 px-6 py-3 text-sm font-semibold text-gray-700 hover:bg-gray-50"
        >
          Войти
        </RouterLink>
      </div>
    </header>

    <!-- Core loop -->
    <section class="mx-auto max-w-4xl px-6 pb-24">
      <div class="grid grid-cols-1 gap-8 md:grid-cols-3">
        <div class="text-center">
          <div class="text-3xl">📄</div>
          <h3 class="mt-3 text-lg font-semibold text-gray-900">Загрузите</h3>
          <p class="mt-1 text-sm text-gray-500">PDF статей — без Zotero, без настроек</p>
        </div>
        <div class="text-center">
          <div class="text-3xl">🔍</div>
          <h3 class="mt-3 text-lg font-semibold text-gray-900">Анализируйте</h3>
          <p class="mt-1 text-sm text-gray-500">Фрагменты, покрытие, пробелы — автоматически</p>
        </div>
        <div class="text-center">
          <div class="text-3xl">✍️</div>
          <h3 class="mt-3 text-lg font-semibold text-gray-900">Пишите</h3>
          <p class="mt-1 text-sm text-gray-500">Исследовательские брифинги и черновики с цитатами</p>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-gray-100 py-8 text-center text-sm text-gray-400">
      LitResearch &copy; 2026. Powered by Klemma.
    </footer>
  </div>
</template>
