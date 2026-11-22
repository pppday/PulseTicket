<script setup lang="ts">
import { toasts } from '../store'

const dot = { success: 'bg-sage', error: 'bg-brick', info: 'bg-ink' }
</script>

<template>
  <div class="pointer-events-none fixed bottom-6 right-6 z-[60] flex w-[360px] max-w-[calc(100vw-3rem)] flex-col gap-3">
    <TransitionGroup
      enter-active-class="transition duration-300 ease-out"
      enter-from-class="opacity-0 translate-y-2"
      leave-active-class="transition duration-200 ease-in"
      leave-to-class="opacity-0"
      move-class="transition duration-300"
    >
      <div
        v-for="t in toasts"
        :key="t.id"
        class="pointer-events-auto flex gap-3 rounded-2xl border border-line bg-white p-4 shadow-float"
      >
        <span class="mt-1.5 h-2 w-2 shrink-0 rounded-full" :class="dot[t.type]" />
        <div class="min-w-0">
          <p class="text-sm font-medium">{{ t.title }}</p>
          <p v-if="t.detail" class="mt-1 text-xs leading-relaxed text-ink-soft">{{ t.detail }}</p>
        </div>
      </div>
    </TransitionGroup>
  </div>
</template>
