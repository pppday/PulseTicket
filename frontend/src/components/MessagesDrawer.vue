<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { X } from 'lucide-vue-next'
import { markAllMessagesRead, markMessageRead, state } from '../store'
import { shortTime } from '../utils'

const hasUnread = computed(() => state.messages.some(m => !m.isRead))

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && state.messagesOpen) state.messagesOpen = false
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Transition
    enter-active-class="transition duration-200"
    enter-from-class="opacity-0"
    leave-active-class="transition duration-150"
    leave-to-class="opacity-0"
  >
    <div v-if="state.messagesOpen" class="fixed inset-0 z-40 bg-ink/20 backdrop-blur-[1px]" @click="state.messagesOpen = false" />
  </Transition>
  <Transition
    enter-active-class="transition duration-300 ease-out"
    enter-from-class="translate-x-full"
    leave-active-class="transition duration-200 ease-in"
    leave-to-class="translate-x-full"
  >
    <aside
      v-if="state.messagesOpen"
      class="fixed inset-y-0 right-0 z-50 flex w-full max-w-[400px] flex-col border-l border-line bg-white shadow-float"
    >
      <div class="flex items-center justify-between border-b border-line px-7 py-5">
        <div class="flex items-baseline gap-3">
          <h3 class="text-base font-semibold tracking-tight">通知</h3>
          <button
            v-if="hasUnread"
            class="text-xs text-ink-soft transition hover:text-ink"
            @click="markAllMessagesRead"
          >
            全部标为已读
          </button>
        </div>
        <button
          class="grid h-8 w-8 place-items-center rounded-full text-ink-mute transition hover:bg-paper hover:text-ink"
          aria-label="关闭通知"
          @click="state.messagesOpen = false"
        >
          <X class="h-4 w-4" />
        </button>
      </div>
      <ul class="flex-1 divide-y divide-line overflow-y-auto">
        <li
          v-for="m in state.messages"
          :key="m.id"
          class="cursor-pointer px-7 py-5 transition hover:bg-paper/70"
          @click="markMessageRead(m.id)"
        >
          <div class="flex items-start gap-3">
            <span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" :class="m.isRead ? 'bg-transparent' : 'bg-brick'" />
            <div class="min-w-0 flex-1">
              <p class="text-sm font-medium leading-snug" :class="m.isRead ? 'text-ink-soft' : 'text-ink'">
                {{ m.title }}
              </p>
              <p class="mt-2 text-sm leading-relaxed text-ink-soft">{{ m.content }}</p>
              <div class="mt-3 flex items-center justify-between text-xs text-ink-mute">
                <span class="font-mono">{{ m.orderNo }}</span>
                <span class="tabular">{{ shortTime(m.createdAt) }}</span>
              </div>
            </div>
          </div>
        </li>
        <li v-if="!state.messages.length" class="px-7 py-24 text-center text-sm text-ink-mute">暂无通知</li>
      </ul>
    </aside>
  </Transition>
</template>
