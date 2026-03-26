<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <!-- :key forces remount when the active project changes,
       so onMounted data-loading runs again in every view. -->
  <RouterView :key="(route.params.projectId as string) ?? route.path" />
</template>
