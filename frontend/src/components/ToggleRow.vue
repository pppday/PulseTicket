<script setup lang="ts">
/** 【通用带标题说明的开关行组件（ToggleRow）】：用于结算栏里的管理员演练开关等布尔配置项。 */
defineProps<{ title: string; description: string; tone?: 'ink' | 'brick' }>()
const model = defineModel<boolean>({ default: false })
</script>

<template>
  <label class="flex cursor-pointer items-start justify-between gap-4">
    <span>
      <span class="block text-sm font-medium">{{ title }}</span>
      <span class="mt-0.5 block text-xs leading-relaxed text-ink-soft">{{ description }}</span>
    </span>
    <button
      type="button"
      role="switch"
      :aria-checked="model"
      class="relative mt-0.5 h-6 w-10 shrink-0 rounded-full transition-colors duration-200"
      :class="model ? (tone === 'brick' ? 'bg-brick' : 'bg-ink') : 'bg-stone-200'"
      @click="model = !model"
    >
      <span
        class="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200"
        :class="model ? 'translate-x-4' : ''"
      />
    </button>
  </label>
</template>
