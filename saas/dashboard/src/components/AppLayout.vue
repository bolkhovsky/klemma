<script setup lang="ts">
import { RouterLink, useRouter, useRoute } from 'vue-router'
import { ref, onMounted } from 'vue'
import { auth } from '@/api/client'

const router = useRouter()
const route = useRoute()
const userName = ref('')

onMounted(async () => {
  try {
    const me = await auth.me()
    userName.value = me.name ?? me.email.split('@')[0]
  } catch {
    /* ignore — header still works without name */
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
              :class="route.path === '/library'
                ? 'text-[var(--color-accent-deep)] bg-[var(--color-accent-pale)]'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-rule-light)]'"
            >
              Библиотека
            </RouterLink>
          </nav>
        </div>

        <!-- User -->
        <div class="flex items-center gap-4">
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

    <!-- Content -->
    <main class="mx-auto max-w-6xl px-8 py-8">
      <slot />
    </main>
  </div>
</template>
