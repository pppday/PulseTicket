<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SeatItem, ZoneCode } from '../types'
import { money, zones } from '../utils'

const props = defineProps<{ seats: SeatItem[]; selected: number[] }>()
const emit = defineEmits<{ (e: 'toggle', seat: SeatItem): void }>()

const hovered = ref<SeatItem | null>(null)

const rows = computed(() => {
  const map = new Map<number, SeatItem[]>()
  for (const s of props.seats) {
    if (!map.has(s.rowNo)) map.set(s.rowNo, [])
    map.get(s.rowNo)!.push(s)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([row, list]) => ({
    row,
    seats: list.sort((a, b) => a.colNo - b.colNo)
  }))
})

const columns = computed(() => rows.value[0]?.seats.map(s => s.colNo) ?? [])
const aisleAfter = computed(() => Math.floor(columns.value.length / 2))

const tierPrices = computed(() => {
  const result: { code: ZoneCode; price: number }[] = []
  for (const code of Object.keys(zones) as ZoneCode[]) {
    const seat = props.seats.find(s => s.zoneCode === code)
    if (seat) result.push({ code, price: Number(seat.price) })
  }
  return result
})

function seatClass(seat: SeatItem) {
  if (props.selected.includes(seat.id)) return 'bg-ink border-ink text-white shadow-soft scale-[1.04]'
  if (seat.status === 'SOLD') return 'bg-stone-200/90 border-transparent cursor-not-allowed'
  if (seat.status === 'LOCKED') return 'seat-locked border-gold/50 text-gold cursor-not-allowed'
  return `bg-white ${zones[seat.zoneCode].seat}`
}

const statusText: Record<string, string> = { AVAILABLE: '可选', LOCKED: '他人锁定待付中', SOLD: '已售出' }
</script>

<template>
  <div>
    <!-- Stage -->
    <div class="mx-auto flex max-w-[460px] flex-col items-center">
      <svg viewBox="0 0 400 40" class="w-full" aria-hidden="true">
        <path d="M10 34 Q200 -6 390 34" fill="none" stroke="#1A1917" stroke-width="1.5" stroke-linecap="round" />
      </svg>
      <span class="-mt-1 eyebrow">舞台正前方</span>
    </div>

    <!-- Grid -->
    <div class="mt-10 overflow-x-auto pb-2">
      <div class="mx-auto w-max">
        <div class="mb-3 flex items-center gap-2 pl-10 pr-10">
          <template v-for="(c, i) in columns" :key="c">
            <span class="w-9 text-center text-[11px] text-ink-mute tabular">{{ c }}</span>
            <span v-if="i + 1 === aisleAfter" class="w-5" />
          </template>
        </div>

        <div v-for="r in rows" :key="r.row" class="mb-2.5 flex items-center gap-2">
          <span class="w-8 text-right text-[11px] text-ink-mute tabular">{{ r.row }}</span>
          <template v-for="(seat, i) in r.seats" :key="seat.id">
            <button
              class="h-9 w-9 rounded-[10px] border-[1.5px] text-[11px] font-medium transition duration-150 tabular"
              :class="seatClass(seat)"
              :disabled="seat.status !== 'AVAILABLE'"
              :aria-label="`${seat.rowNo}排${seat.colNo}座`"
              @click="emit('toggle', seat)"
              @mouseenter="hovered = seat"
              @mouseleave="hovered = null"
            >
              <span v-if="selected.includes(seat.id)">{{ seat.colNo }}</span>
              <span v-else-if="seat.status === 'LOCKED'" class="block mx-auto h-1.5 w-1.5 rounded-full bg-gold/80" />
            </button>
            <span v-if="i + 1 === aisleAfter" class="w-5" />
          </template>
          <span class="w-8 text-left text-[11px] text-ink-mute tabular">{{ r.row }}</span>
        </div>
      </div>
    </div>

    <!-- Hover info -->
    <p class="mt-5 h-5 text-center text-sm text-ink-soft">
      <template v-if="hovered">
        <span class="font-medium text-ink">{{ hovered.rowNo }} 排 {{ hovered.colNo }} 座</span>
        <span class="mx-2 text-line">/</span>{{ zones[hovered.zoneCode].label }}
        <span class="mx-2 text-line">/</span><span class="tabular">¥{{ money(hovered.price) }}</span>
        <span class="mx-2 text-line">/</span>{{ statusText[hovered.status] }}
      </template>
      <span v-else class="text-ink-mute">将鼠标移到座位上查看票档与状态</span>
    </p>

    <!-- Legend -->
    <div class="mt-8 flex flex-wrap items-center justify-center gap-x-7 gap-y-3 border-t border-line pt-6 text-xs text-ink-soft">
      <span v-for="t in tierPrices" :key="t.code" class="flex items-center gap-2">
        <span class="h-3.5 w-3.5 rounded-[5px] border-[1.5px] bg-white" :class="zones[t.code].seat" />
        {{ zones[t.code].label }}
        <span class="text-ink tabular">¥{{ money(t.price) }}</span>
      </span>
      <span class="hidden h-4 w-px bg-line sm:block" />
      <span class="flex items-center gap-2"><span class="h-3.5 w-3.5 rounded-[5px] bg-ink" />已选</span>
      <span class="flex items-center gap-2"><span class="seat-locked h-3.5 w-3.5 rounded-[5px] border border-gold/50" />锁定中</span>
      <span class="flex items-center gap-2"><span class="h-3.5 w-3.5 rounded-[5px] bg-stone-200/90" />已售</span>
    </div>
  </div>
</template>
