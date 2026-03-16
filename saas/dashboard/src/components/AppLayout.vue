<script setup lang="ts">
import { RouterLink, useRouter, useRoute } from 'vue-router'
import { ref, computed, onMounted } from 'vue'
import { auth, usage } from '@/api/client'

const router = useRouter()
const route = useRoute()
const userName = ref('')

// Token balance
const tokenBalance = ref<{ total_granted: number; total_used: number; remaining: number } | null>(null)

const tokenPercent = computed(() => {
  if (!tokenBalance.value || tokenBalance.value.total_granted === 0) return 0
  return Math.round((tokenBalance.value.total_used / tokenBalance.value.total_granted) * 100)
})

const tokenColor = computed(() => {
  const p = tokenPercent.value
  if (p >= 90) return 'text-[var(--color-err)]'
  if (p >= 50) return 'text-[var(--color-warn)]'
  return 'text-[var(--color-ok)]'
})

const tokenBarColor = computed(() => {
  const p = tokenPercent.value
  if (p >= 90) return 'bg-[var(--color-err)]'
  if (p >= 50) return 'bg-[var(--color-warn)]'
  return 'bg-[var(--color-ok)]'
})

function formatTokens(n: number): string {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${Math.round(n / 1000)}K`
  return String(n)
}

onMounted(async () => {
  try {
    const [me, bal] = await Promise.all([auth.me(), usage.me()])
    userName.value = me.name ?? me.email.split('@')[0]
    tokenBalance.value = bal
  } catch {
    /* ignore — header still works without data */
  }
})

function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  router.push('/login')
}
</script>

<template>
  <div class="min-h-screen bg-[var(--color-paper)]">
    <!-- Header -->
    <header class="border-b border-[var(--color-rule)] bg-[var(--color-paper-white)]">
      <div class="mx-auto flex max-w-6xl items-center justify-between px-8 py-4">
        <!-- Logo + nav -->
        <div class="flex items-center gap-10">
          <RouterLink to="/dashboard" class="flex items-center gap-2.5 group">
            <div class="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--color-accent)] text-white font-[var(--font-display)] text-sm font-bold tracking-tight">
              Lr
            </div>
            <span class="font-[var(--font-display)] text-lg font-semibold text-[var(--color-ink)] tracking-tight">
              LitResearch
            </span>
          </RouterLink>

          <nav class="flex gap-1">
            <RouterLink
              to="/dashboard"
              class="relative rounded-md px-3.5 py-2 text-sm font-medium transition-colors"
              :class="route.path === '/dashboard'
                ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
            >
              Обзор
            </RouterLink>
            <RouterLink
              to="/library"
              class="relative rounded-md px-3.5 py-2 text-sm font-medium transition-colors"
              :class="route.path.startsWith('/library')
                ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
            >
              Библиотека
            </RouterLink>
            <RouterLink
              to="/coverage"
              class="relative rounded-md px-3.5 py-2 text-sm font-medium transition-colors"
              :class="route.path === '/coverage'
                ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
            >
              Покрытие
            </RouterLink>
            <RouterLink
              to="/write"
              class="relative rounded-md px-3.5 py-2 text-sm font-medium transition-colors"
              :class="route.path === '/write'
                ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
            >
              Написать
            </RouterLink>
          </nav>
        </div>

        <!-- Token balance + User -->
        <div class="flex items-center gap-5">
          <!-- Token meter -->
          <div
            v-if="tokenBalance && tokenBalance.total_granted > 0"
            class="flex items-center gap-2"
            :title="`Использовано ${tokenBalance.total_used.toLocaleString('ru-RU')} из ${tokenBalance.total_granted.toLocaleString('ru-RU')} токенов`"
          >
            <div class="flex items-center gap-1.5">
              <span class="font-[var(--font-mono)] text-xs" :class="tokenColor">
                {{ formatTokens(tokenBalance.total_used) }}
              </span>
              <span class="text-xs text-[var(--color-ink-muted)]">/</span>
              <span class="font-[var(--font-mono)] text-xs text-[var(--color-ink-muted)]">
                {{ formatTokens(tokenBalance.total_granted) }}
              </span>
            </div>
            <div class="w-16 h-1.5 rounded-full bg-[var(--color-rule-light)] overflow-hidden">
              <div
                class="h-full rounded-full transition-all duration-500"
                :class="tokenBarColor"
                :style="{ width: `${Math.min(tokenPercent, 100)}%` }"
              ></div>
            </div>
          </div>

          <!-- Low balance warning -->
          <span
            v-if="tokenBalance && tokenBalance.total_granted > 0 && tokenPercent >= 90"
            class="text-xs text-[var(--color-err)] font-medium"
          >
            Мало токенов
          </span>

          <span v-if="userName" class="text-sm text-[var(--color-ink-muted)]">{{ userName }}</span>
          <button
            @click="logout"
            class="text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-err)] transition-colors"
          >
            Выйти
          </button>
        </div>
      </div>
    </header>

    <!-- Low balance banner -->
    <div
      v-if="tokenBalance && tokenBalance.total_granted > 0 && tokenBalance.remaining <= 0"
      class="bg-[var(--color-err-bg)] border-b border-[var(--color-err)] px-8 py-2 text-center"
    >
      <span class="text-sm font-medium text-[var(--color-err)]">
        Токены закончились. Обработка и генерация текста недоступны. Свяжитесь с администратором.
      </span>
    </div>

    <!-- Content -->
    <main class="mx-auto max-w-6xl px-8 py-8">
      <slot />
    </main>
  </div>
</template>
