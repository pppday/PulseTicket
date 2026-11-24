<script setup lang="ts">
/**
 * 【自助阶梯退票二次确认弹窗（RefundConfirmModal）】
 * 按 [POL-REFUND-001] 规则可视化高亮当前所处退票阶梯（0% / 10% / 20% 手续费），展示原付/手续费/实退金额及积分同步扣回提示。
 */
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { AlertCircle, Check, Clock, Loader2, RotateCcw, Ticket, X } from 'lucide-vue-next'
import { state } from '../store'
import type { TicketOrder } from '../types'
import { calcRefundPreview, money, parseList, posterTheme } from '../utils'

const props = defineProps<{
  order: TicketOrder | null
  submitting?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'confirm'): void
}>()

const preview = computed(() =>
  props.order ? calcRefundPreview(props.order.totalAmount, props.order.startTime, state.now) : null
)

const seats = computed(() => (props.order ? parseList(props.order.seatLabelsJson) : []))

const tiers = [
  {
    rate: '0%',
    window: '≥ 7 天',
    label: '免手续费',
    activeClass: 'border-sage/40 bg-sage-soft/80 text-sage'
  },
  {
    rate: '10%',
    window: '48h ~ 7 天',
    label: '10% 手续费',
    activeClass: 'border-gold/40 bg-gold-soft/85 text-gold'
  },
  {
    rate: '20%',
    window: '24h ~ 48h',
    label: '20% 手续费',
    activeClass: 'border-brick/35 bg-brick-soft/80 text-brick'
  }
] as const

const timeLeftText = computed(() => {
  const h = preview.value?.hoursLeft
  if (h === null || h === undefined) return '开演时间待定'
  if (h >= 168) return `距开演约 ${Math.floor(h / 24)} 天`
  if (h >= 24) return `距开演约 ${Math.floor(h)} 小时`
  if (h > 0) return '距开演不足 24 小时'
  return '演出已开演'
})

function requestClose() {
  if (props.submitting) return
  emit('close')
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.order) {
    requestClose()
  }
}

watch(
  () => props.order,
  val => {
    document.body.style.overflow = val ? 'hidden' : ''
  }
)

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <Transition name="refund-modal">
      <div
        v-if="order && preview"
        class="fixed inset-0 z-50 grid place-items-center bg-ink/45 p-4 backdrop-blur-sm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="refund-modal-title"
        @click.self="requestClose"
      >
        <div
          class="refund-modal-card flex max-h-[92vh] w-full max-w-[468px] flex-col overflow-hidden rounded-[26px] border border-line/70 bg-white shadow-float"
        >
          <!-- Scrollable Content (overflows only on very short viewports) -->
          <div class="overflow-y-auto">
            <!-- Editorial Header -->
            <div class="px-6 pb-4 pt-5">
              <div class="flex items-start justify-between gap-4">
                <div>
                  <p class="inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.16em] text-brick">
                    <RotateCcw class="h-3 w-3" />
                    退票确认 · REFUND
                  </p>
                  <h3
                    id="refund-modal-title"
                    class="mt-1.5 text-[20px] font-semibold leading-tight tracking-tight text-ink"
                  >
                    确认申请退票
                  </h3>
                </div>
                <button
                  type="button"
                  class="grid h-8 w-8 shrink-0 place-items-center rounded-full text-ink-mute transition hover:bg-paper hover:text-ink disabled:opacity-40"
                  aria-label="关闭退票确认弹窗"
                  :disabled="submitting"
                  @click="requestClose"
                >
                  <X class="h-4 w-4" />
                </button>
              </div>

              <!-- Order Mini Ticket Card -->
              <div class="mt-3.5 flex gap-3 rounded-2xl border border-line bg-paper/75 p-3.5">
                <div
                  class="w-1 shrink-0 rounded-full"
                  :style="{ background: posterTheme(order.showId) }"
                  aria-hidden="true"
                />
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center justify-between gap-2 text-[11px] text-ink-mute">
                    <span class="font-mono">{{ order.orderNo }}</span>
                    <span
                      v-if="order.ticketCode"
                      class="inline-flex items-center gap-1 font-mono text-ink-soft"
                    >
                      <Ticket class="h-3 w-3 text-ink-mute" />
                      {{ order.ticketCode }}
                    </span>
                  </div>
                  <p class="mt-1 text-[14px] font-semibold leading-snug text-ink">
                    {{ order.showTitle }}
                  </p>
                  <p class="mt-0.5 text-xs text-ink-soft">
                    {{ order.sessionTitle }} · {{ order.venueName }}
                  </p>
                  <div v-if="seats.length" class="mt-2.5 flex flex-wrap gap-1.5">
                    <span
                      v-for="label in seats"
                      :key="label"
                      class="rounded-full border border-line bg-white px-2.5 py-0.5 text-[11px] text-ink-soft"
                    >
                      {{ label }}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Perforated Ticket-Stub Divider -->
            <div class="mx-6 border-t border-dashed border-line" />

            <!-- Refund Policy Ladder & Breakdown -->
            <div class="space-y-3 px-6 py-4">
              <!-- Policy Header -->
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex items-center gap-2">
                  <span class="text-xs font-medium text-ink">阶梯退票政策</span>
                  <span class="rounded-md border border-line bg-paper px-1.5 py-0.5 font-mono text-[10px] text-ink-mute">
                    POL-REFUND-001
                  </span>
                </div>
                <span class="inline-flex items-center gap-1 text-[11px] text-ink-soft tabular">
                  <Clock class="h-3 w-3 text-ink-mute" />
                  {{ timeLeftText }}
                </span>
              </div>

              <!-- 3-Tier Visual Strip -->
              <div class="grid grid-cols-3 gap-2">
                <div
                  v-for="tier in tiers"
                  :key="tier.rate"
                  class="rounded-xl border px-2.5 py-1.5 transition"
                  :class="
                    preview.feeRate === tier.rate
                      ? tier.activeClass
                      : 'border-line/80 bg-paper/45 text-ink-mute opacity-75'
                  "
                >
                  <div class="flex items-center justify-between gap-1">
                    <span class="text-[10px] font-medium tracking-tight tabular">{{ tier.window }}</span>
                    <Check v-if="preview.feeRate === tier.rate" class="h-3 w-3 shrink-0" />
                  </div>
                  <p class="mt-0.5 text-xs font-semibold tabular">{{ tier.label }}</p>
                </div>
              </div>

              <!-- Financial Settlement Card -->
              <div class="rounded-2xl border border-line/80 bg-paper/65 p-3.5">
                <dl class="space-y-2 text-xs">
                  <div class="flex items-center justify-between gap-4">
                    <dt class="text-ink-soft">适用档位</dt>
                    <dd class="font-medium text-ink">{{ preview.tierName }}</dd>
                  </div>
                  <div class="flex items-center justify-between gap-4">
                    <dt class="text-ink-soft">订单实付（{{ order.seatCount }} 张）</dt>
                    <dd class="font-medium text-ink tabular">¥{{ money(order.totalAmount) }}</dd>
                  </div>
                  <div class="flex items-center justify-between gap-4">
                    <dt class="flex items-center gap-1.5 text-ink-soft">
                      <span>退票手续费</span>
                      <span class="rounded border border-line/80 bg-white px-1.5 py-0.5 font-mono text-[10px] text-ink-mute tabular">
                        {{ preview.feeRate }}
                      </span>
                    </dt>
                    <dd
                      class="font-medium tabular"
                      :class="preview.feeAmount === 0 ? 'text-sage' : 'text-brick'"
                    >
                      {{ preview.feeAmount === 0 ? '免手续费 ¥0' : `- ¥${money(preview.feeAmount)}` }}
                    </dd>
                  </div>
                </dl>

                <div class="my-2.5 border-t border-line" />

                <div class="flex items-end justify-between gap-4">
                  <div>
                    <p class="text-xs font-medium text-ink">预计原路实退</p>
                    <p class="mt-0.5 text-[11px] text-ink-mute">确认后即时办结并退回原账户</p>
                  </div>
                  <p class="inline-flex items-baseline gap-0.5 text-right tabular">
                    <span class="text-sm font-medium text-ink-soft">¥</span>
                    <span class="text-[26px] font-semibold leading-none tracking-tight text-ink">
                      {{ money(preview.refundAmount) }}
                    </span>
                  </p>
                </div>
              </div>

              <!-- Consequence Notice -->
              <div class="flex items-start gap-2 rounded-2xl border border-brick/15 bg-brick-soft/50 px-3.5 py-2.5">
                <AlertCircle class="mt-0.5 h-3.5 w-3.5 shrink-0 text-brick" />
                <p class="text-[11.5px] leading-relaxed text-ink-soft">
                  退票后电子核验码将立即失效，对应座位将实时释放回余票池；原单发放的观演积分也将同步冲正扣回。
                </p>
              </div>
            </div>
          </div>

          <!-- Footer Actions -->
          <div class="flex items-center gap-3 border-t border-line bg-paper/45 px-6 py-4">
            <button
              type="button"
              class="btn-ghost h-10 flex-1 whitespace-nowrap px-4"
              :disabled="submitting"
              @click="requestClose"
            >
              暂不退票
            </button>
            <button
              type="button"
              class="inline-flex h-10 flex-[1.45] items-center justify-center gap-2 whitespace-nowrap rounded-full bg-brick px-5 text-sm font-medium text-white shadow-sm transition hover:bg-[#9C3A22] disabled:cursor-not-allowed disabled:opacity-45"
              :disabled="submitting || !preview.refundable"
              @click="emit('confirm')"
            >
              <Loader2 v-if="submitting" class="h-4 w-4 animate-spin" />
              <span v-if="submitting">退票办理中…</span>
              <span v-else class="tabular">确认退票 · 退 ¥{{ money(preview.refundAmount) }}</span>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.refund-modal-enter-active,
.refund-modal-leave-active {
  transition: opacity 200ms ease;
}

.refund-modal-enter-active .refund-modal-card,
.refund-modal-leave-active .refund-modal-card {
  transition:
    transform 240ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 200ms ease;
}

.refund-modal-enter-from,
.refund-modal-leave-to {
  opacity: 0;
}

.refund-modal-enter-from .refund-modal-card {
  opacity: 0;
  transform: translateY(10px) scale(0.97);
}

.refund-modal-leave-to .refund-modal-card {
  opacity: 0;
  transform: translateY(6px) scale(0.985);
}
</style>
