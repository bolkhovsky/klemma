<script setup lang="ts">
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-gray-50 px-4">
    <div class="w-full max-w-md text-center">
      <h1 class="text-2xl font-bold text-gray-900">Личный онбординг</h1>

      <p class="mt-4 text-base leading-relaxed text-gray-600">
        Сейчас мы подключаем каждого пользователя лично —
        настраиваем проект, загружаем первые PDF и показываем,
        как получить максимум от LitResearch.
      </p>

      <a
        href="https://t.me/bolkhovsky"
        target="_blank"
        rel="noopener noreferrer"
        class="mt-8 inline-flex items-center gap-2 rounded-lg bg-[#2AABEE] px-6 py-3 text-base font-semibold text-white hover:bg-[#229ED9] transition-colors"
      >
        <svg class="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.479.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/>
        </svg>
        Написать в Telegram
      </a>

      <p class="mt-6 text-sm text-gray-500">
        Уже есть аккаунт?
        <RouterLink to="/login" class="text-indigo-600 hover:text-indigo-500">Войти</RouterLink>
      </p>
    </div>
  </div>
</template>
