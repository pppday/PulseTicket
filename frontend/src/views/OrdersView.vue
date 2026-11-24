<script setup lang="ts">
import { computed, ref } from 'vue'
import RefundConfirmModal from '../components/RefundConfirmModal.vue'
import { cancelOrder, payOrder, refundOrder, state } from '../store'
import type { OrderStatus, TicketOrder } from '../types'
import { calcRefundPreview, closeReasonLabel, countdown, money, parseList, remainingSeconds, shortTime } from '../utils'

const filter = ref<'ALL' | OrderStatus>('ALL')
const filters: { key: 'ALL' | OrderStatus; label: string }[] = [
  { key: 'ALL', label: '全部' },
  { key: 'UNPAID', label: '待支付' },
  { key: 'PAID', label: '已出票' },
  { key: 'CLOSED', label: '已关闭' }
]

const orders = computed(() =>
  filter.value === 'ALL' ? state.orders : state.orders.filter(o => o.status === filter.value)
)

const statusMeta: Record<OrderStatus, { label: string; dot: string }> = {
  UNPAID: { label: '待支付', dot: 'bg-brick' },
  PAID: { label: '已出票', dot: 'bg-sage' },
  CLOSED: { label: '已关闭', dot: 'bg-stone-300' }
}

const busy = ref<string | null>(null)
async function act(orderNo: string, fn: () => Promise<void>) {
  busy.value = orderNo
  try {
    await fn()
  } finally {
    busy.value = null
  }
}

function previewRefund(order: TicketOrder) {
  return calcRefundPreview(order.totalAmount, order.startTime, state.now)
}

const refundTarget = ref<TicketOrder | null>(null)

function openRefundModal(order: TicketOrder) {
  const preview = previewRefund(order)
  if (!preview.refundable) return
  refundTarget.value = order
}

async function confirmRefund() {
  const order = refundTarget.value
  if (!order) return
  try {
    await act(order.orderNo, () => refundOrder(order))
  } finally {
    refundTarget.value = null
  }
}
</script>

<template>
  <div>
    <header class="flex flex-wrap items-end justify-between gap-6">
      <div>
        <p class="eyebrow">票夹</p>
        <h1 class="mt-2 text-[28px] font-semibold tracking-tight">我的订单</h1>
      </div>
      <div class="inline-flex rounded-full bg-ink/[0.05] p-1 text-sm">
        <button
          v-for="f in filters"
          :key="f.key"
          class="rounded-full px-4 py-1.5 transition"
          :class="filter === f.key ? 'bg-white font-medium text-ink shadow-sm' : 'text-ink-soft hover:text-ink'"
          @click="filter = f.key"
        >
          {{ f.label }}
        </button>
      </div>
    </header>

    <div v-if="!orders.length" class="card mt-10 grid place-items-center px-6 py-24 text-center">
      <p class="text-base font-medium">暂无订单</p>
      <p class="mt-2 text-sm text-ink-soft">选好座位后，订单会出现在这里</p>
      <button class="btn-primary mt-6 h-10" @click="state.tab = 'theater'">去选座</button>
    </div>

    <TransitionGroup v-else tag="div" class="mt-10 space-y-5" move-class="transition duration-300">
      <article
        v-for="order in orders"
        :key="order.orderNo"
        class="card flex flex-col transition md:flex-row"
        :class="order.status === 'CLOSED' ? 'opacity-65' : ''"
      >
        <!-- main -->
        <div class="flex-1 p-7 md:p-8">
          <div class="flex items-center gap-2 text-xs text-ink-mute">
            <span class="font-mono">{{ order.orderNo }}</span>
            <span>·</span>
            <span class="tabular">{{ shortTime(order.createdAt) }}</span>
          </div>
          <h3 class="mt-3 text-lg font-semibold leading-snug tracking-tight">{{ order.showTitle }}</h3>
          <p class="mt-1.5 text-sm text-ink-soft">{{ order.sessionTitle }} · {{ order.venueName }}</p>
          <div class="mt-5 flex flex-wrap gap-2">
            <span
              v-for="label in parseList(order.seatLabelsJson)"
              :key="label"
              class="rounded-full border border-line bg-paper/60 px-3 py-1 text-xs text-ink-soft"
            >
              {{ label }}
            </span>
          </div>
        </div>

        <!-- stub -->
        <div class="relative flex flex-col justify-between gap-6 border-t border-dashed border-line p-7 md:w-[300px] md:border-l md:border-t-0 md:p-8">
          <span
            aria-hidden="true"
            class="pointer-events-none absolute -left-[11px] -top-[1px] hidden h-[11px] w-[22px] rounded-b-full border border-t-0 border-line bg-paper md:block"
          />
          <span
            aria-hidden="true"
            class="pointer-events-none absolute -bottom-[1px] -left-[11px] hidden h-[11px] w-[22px] rounded-t-full border border-b-0 border-line bg-paper md:block"
          />

          <div>
            <p class="flex items-center gap-2 text-xs text-ink-soft">
              <span class="h-1.5 w-1.5 rounded-full" :class="statusMeta[order.status].dot" />
              {{ statusMeta[order.status].label }}
              <span v-if="order.status === 'UNPAID' && order.delaySeconds === 15" class="text-ink-mute">· 15s 演示</span>
            </p>

            <template v-if="order.status === 'UNPAID'">
              <p class="mt-3 text-[34px] font-semibold leading-none tracking-tight text-brick tabular">
                {{ countdown(remainingSeconds(order.expireAt, state.now)) }}
              </p>
              <p class="mt-2 text-xs text-ink-mute">超时后座位自动释放</p>
            </template>
            <template v-else-if="order.status === 'PAID'">
              <p class="mt-3 font-mono text-xl tracking-wider">{{ order.ticketCode }}</p>
              <p class="mt-2 text-xs text-ink-mute">入场时出示取票码</p>
            </template>
            <p v-else class="mt-3 text-sm text-ink-soft">
              {{ closeReasonLabel[order.closeReason ?? ''] ?? '订单已关闭' }}
            </p>
          </div>

          <div>
            <p class="text-sm tabular">
              <span class="text-ink-soft">¥</span><span class="text-xl font-semibold">{{ money(order.totalAmount) }}</span>
              <span class="ml-1 text-xs text-ink-mute">· {{ order.seatCount }} 张</span>
            </p>
            <div v-if="order.status === 'UNPAID'" class="mt-4 flex gap-2">
              <button
                class="btn-ghost h-10 flex-1 px-0"
                :disabled="busy === order.orderNo"
                @click="act(order.orderNo, () => cancelOrder(order))"
              >
                取消
              </button>
              <button
                class="btn-primary h-10 flex-[1.6] px-0"
                :disabled="busy === order.orderNo"
                @click="act(order.orderNo, () => payOrder(order))"
              >
                立即支付
              </button>
            </div>
            <div v-else-if="order.status === 'PAID'" class="mt-4 space-y-2">
              <div class="flex gap-2">
                <button
                  v-if="previewRefund(order).refundable"
                  class="btn-ghost h-10 flex-1 whitespace-nowrap border-brick/35 px-2 text-brick hover:bg-brick-soft/50"
                  :disabled="busy === order.orderNo"
                  @click="openRefundModal(order)"
                >
                  {{ busy === order.orderNo ? '退票中…' : `申请退票 (${previewRefund(order).feeRate})` }}
                </button>
                <button
                  class="btn-ghost h-10 flex-1 whitespace-nowrap px-2"
                  @click="state.ticket = order"
                >
                  查看电子票
                </button>
              </div>
              <p v-if="!previewRefund(order).refundable" class="text-[11px] text-ink-mute">
                {{ previewRefund(order).known ? '距开演不足 24h 或已开演，暂不支持自助退票' : '开演时间待定，请联系客服核实退票' }}
              </p>
            </div>
          </div>
        </div>
      </article>
    </TransitionGroup>

    <RefundConfirmModal
      :order="refundTarget"
      :submitting="!!refundTarget && busy === refundTarget.orderNo"
      @close="refundTarget = null"
      @confirm="confirmRefund"
    />
  </div>
</template>
