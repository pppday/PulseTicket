<script setup lang="ts">
/**
 * 【公开电子票扫码核验 H5 页（VerifyTicketView）】
 * 手机微信或相机扫描电子票二维码后免登录打开，调用 `/api/order/public/verify/{ticketCode}` 实时核验票面状态与座位归属。
 */
import { computed, onMounted, ref } from 'vue'
import { AlertCircle, ArrowLeft, CheckCircle2, Ticket } from 'lucide-vue-next'
import { api } from '../api'
import type { VerifiedTicket } from '../types'
import { groupSeatsByZone, money, posterTheme } from '../utils'

const props = defineProps<{ ticketCode: string }>()
const emit = defineEmits<{ (e: 'exit'): void }>()

const loading = ref(true)
const errorMsg = ref('')
const ticket = ref<VerifiedTicket | null>(null)

const groupedSeats = computed(() => groupSeatsByZone(ticket.value?.seatLabelsJson))

onMounted(async () => {
  loading.value = true
  const res = await api<VerifiedTicket>(
    `/api/order/public/verify/${encodeURIComponent(props.ticketCode)}`,
    {},
    ''
  )
  if (res.code === 200 && res.data) {
    ticket.value = res.data
  } else {
    errorMsg.value = res.message || '未找到该电子票核验码，请确认票面真伪'
  }
  loading.value = false
})
</script>

<template>
  <div class="min-h-screen bg-paper px-4 py-8 sm:py-12">
    <div class="mx-auto max-w-[420px]">
      <!-- Top brand header -->
      <div class="mb-6 flex items-center justify-between px-1">
        <div class="flex items-center gap-2">
          <span class="grid h-7 w-7 place-items-center rounded-lg bg-ink">
            <Ticket class="h-3.5 w-3.5 text-paper" :stroke-width="2" />
          </span>
          <span class="text-sm font-semibold tracking-tight">PulseTicket 电子票闸机核验</span>
        </div>
        <span class="font-mono text-[11px] text-ink-mute">OFFICIAL PASS</span>
      </div>

      <!-- Loading state -->
      <div v-if="loading" class="card p-12 text-center shadow-soft">
        <p class="text-sm font-medium text-ink">正在实时联网核验票面凭证…</p>
        <p class="mt-2 font-mono text-xs text-ink-mute">{{ ticketCode }}</p>
      </div>

      <!-- Invalid / Not found state -->
      <div v-else-if="errorMsg || !ticket" class="card overflow-hidden p-8 text-center shadow-soft">
        <div class="mx-auto grid h-12 w-12 place-items-center rounded-full bg-brick-soft text-brick">
          <AlertCircle class="h-6 w-6" />
        </div>
        <h1 class="mt-4 text-lg font-semibold">核验未通过</h1>
        <p class="mt-2 text-sm leading-relaxed text-ink-soft">{{ errorMsg }}</p>
        <p class="mt-4 rounded-xl bg-paper py-2 font-mono text-xs text-ink-mute">{{ ticketCode }}</p>
        <button class="btn-ghost mt-6 h-10 w-full" @click="emit('exit')">
          <ArrowLeft class="h-4 w-4" />
          前往选座购票首页
        </button>
      </div>

      <!-- Authentic Gate Pass Certificate -->
      <article v-else class="overflow-hidden rounded-[28px] border border-line bg-white shadow-float">
        <!-- Verification status banner -->
        <div
          class="flex items-center justify-between px-6 py-4"
          :class="ticket.valid ? 'bg-sage-soft text-sage' : 'bg-brick-soft text-brick'"
        >
          <div class="flex items-center gap-2.5">
            <CheckCircle2 v-if="ticket.valid" class="h-5 w-5 shrink-0" />
            <AlertCircle v-else class="h-5 w-5 shrink-0" />
            <div>
              <p class="text-sm font-semibold">
                {{ ticket.valid ? '电子票核验通过 · 有效入场凭证' : '此票据当前不可入场' }}
              </p>
              <p class="text-[11px] opacity-85">
                {{ ticket.valid ? `核验时间 ${ticket.verifiedAt}` : `当前订单状态：${ticket.status}` }}
              </p>
            </div>
          </div>
          <span
            class="rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wider"
            :class="ticket.valid ? 'bg-sage text-white' : 'bg-brick text-white'"
          >
            {{ ticket.valid ? 'VALID' : ticket.status }}
          </span>
        </div>

        <!-- Show poster header -->
        <div class="p-7 text-white" :style="{ background: posterTheme(ticket.showId) }">
          <p class="text-[11px] uppercase tracking-[0.16em] text-white/65">Verified E-Ticket</p>
          <h1 class="mt-3 text-xl font-semibold leading-snug">{{ ticket.showTitle }}</h1>
          <p class="mt-2 text-xs text-white/80">{{ ticket.venueName }}</p>
        </div>

        <!-- Perforated divider -->
        <div class="relative border-t border-dashed border-line">
          <span class="absolute -left-3 -top-3 h-6 w-6 rounded-full border border-line bg-paper" />
          <span class="absolute -right-3 -top-3 h-6 w-6 rounded-full border border-line bg-paper" />
        </div>

        <!-- Ticket Details -->
        <div class="p-7">
          <div class="rounded-2xl bg-paper px-5 py-4 text-center">
            <p class="text-[11px] uppercase tracking-[0.14em] text-ink-mute">入场核验码</p>
            <p class="mt-1.5 font-mono text-2xl font-semibold tracking-[0.18em] text-ink">
              {{ ticket.ticketCode }}
            </p>
          </div>

          <dl class="mt-6 space-y-4 text-sm">
            <div class="flex items-baseline justify-between gap-4">
              <dt class="shrink-0 text-xs text-ink-mute">演出场次</dt>
              <dd class="text-right font-medium">{{ ticket.sessionTitle }}</dd>
            </div>

            <div class="flex items-start justify-between gap-4">
              <dt class="shrink-0 pt-0.5 text-xs text-ink-mute">入场席位</dt>
              <dd class="space-y-1.5 text-right">
                <div v-for="(g, idx) in groupedSeats" :key="idx">
                  <p v-if="g.zone" class="text-xs text-ink-soft">{{ g.zone }}</p>
                  <div class="mt-1 flex flex-wrap justify-end gap-1.5">
                    <span
                      v-for="s in g.seats"
                      :key="s"
                      class="whitespace-nowrap rounded-md bg-paper px-2.5 py-0.5 text-xs font-medium text-ink tabular"
                    >
                      {{ s }}
                    </span>
                  </div>
                </div>
              </dd>
            </div>

            <div class="flex items-baseline justify-between gap-4">
              <dt class="shrink-0 text-xs text-ink-mute">票面数量</dt>
              <dd class="text-right font-medium tabular">{{ ticket.seatCount }} 张</dd>
            </div>

            <div class="flex items-baseline justify-between gap-4">
              <dt class="shrink-0 text-xs text-ink-mute">实付金额</dt>
              <dd class="text-right font-semibold tabular">¥{{ money(ticket.totalAmount) }}</dd>
            </div>

            <div v-if="ticket.paidAt" class="flex items-baseline justify-between gap-4 border-t border-line pt-4">
              <dt class="shrink-0 text-xs text-ink-mute">出票时间</dt>
              <dd class="text-right font-mono text-xs text-ink-soft">{{ ticket.paidAt }}</dd>
            </div>

            <div class="flex items-baseline justify-between gap-4">
              <dt class="shrink-0 text-xs text-ink-mute">订单编号</dt>
              <dd class="text-right font-mono text-xs text-ink-mute">{{ ticket.orderNo }}</dd>
            </div>
          </dl>

          <button class="btn-ghost mt-7 h-10 w-full text-xs" @click="emit('exit')">
            前往 PulseTicket 选座首页
          </button>
        </div>
      </article>

      <p class="mt-5 text-center text-[11px] text-ink-mute">
        由 PulseTicket 票务中心实时签名核验 · 一票一码防伪
      </p>
    </div>
  </div>
</template>
