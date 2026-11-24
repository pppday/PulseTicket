<script setup lang="ts">
/**
 * 【运维专属 · 平台票房与订单漏斗大盘（SalesView）】
 * 汇总展示全平台实付票房、各票档（VIP/中轴/看台）营收占比、近 7 日销售趋势及订单转化漏斗。
 */
import { computed, onMounted, ref } from 'vue'
import { ArrowUpRight, Database, RotateCw, Smartphone, Ticket, TrendingUp, Users } from 'lucide-vue-next'
import { loadAdminSales, state } from '../store'
import type { OrderStatus, UserAccountRow, ZoneCode } from '../types'
import { categoryLabel, closeReasonLabel, money, parseList, posterTheme, shortTime, shortTitle } from '../utils'

const refreshing = ref(false)
const orderFilter = ref<'ALL' | OrderStatus>('ALL')

onMounted(() => {
  if (!state.showAnalytics || !state.orderSales) {
    refresh()
  }
})

async function refresh() {
  refreshing.value = true
  try {
    await loadAdminSales()
  } finally {
    refreshing.value = false
  }
}

const showSum = computed(() => state.showAnalytics?.summary)
const orderSum = computed(() => state.orderSales?.summary)

const userLookup = computed(() => {
  const map = new Map<number, UserAccountRow>()
  for (const u of state.userDb?.userAccounts || []) {
    map.set(u.id, u)
  }
  return map
})

const totalBoxOffice = computed(() => {
  const orderPaid = Number(orderSum.value?.paidRevenue ?? 0)
  const seatSold = Number(showSum.value?.soldBoxOffice ?? 0)
  return Math.max(orderPaid, seatSold)
})

const zoneTone: Record<ZoneCode, { bar: string; dot: string; badge: string }> = {
  VIP: { bar: 'bg-brick', dot: 'bg-brick', badge: 'bg-brick-soft text-brick' },
  STAGE_SIDE: { bar: 'bg-gold', dot: 'bg-gold', badge: 'bg-gold-soft text-gold' },
  STANDARD: { bar: 'bg-sage', dot: 'bg-sage', badge: 'bg-sage-soft text-sage' }
}

const totalZoneSoldRevenue = computed(() =>
  (state.showAnalytics?.zoneStats || []).reduce((acc, z) => acc + Number(z.soldRevenue || 0), 0)
)

const maxDailyRevenue = computed(() => {
  const vals = (state.orderSales?.dailyTrend || []).map(d => Number(d.paidRevenue || 0))
  return Math.max(1, ...vals)
})

const funnelRows = computed(() => {
  const s = orderSum.value
  const total = Math.max(1, s?.totalOrders || 0)
  return [
    {
      label: '选座创单总数',
      count: s?.totalOrders ?? 0,
      pct: s?.totalOrders ? 100 : 0,
      bar: 'bg-ink',
      desc: '通过 Redisson 联锁与 Seata 一阶段创单'
    },
    {
      label: '已支付出票 (PAID)',
      count: s?.paidOrders ?? 0,
      pct: Math.round(((s?.paidOrders ?? 0) / total) * 1000) / 10,
      bar: 'bg-sage',
      desc: 'CAS 支付成功并触发 Fanout 积分与电子票广播'
    },
    {
      label: '锁座待支付 (UNPAID)',
      count: s?.unpaidOrders ?? 0,
      pct: Math.round(((s?.unpaidOrders ?? 0) / total) * 1000) / 10,
      bar: 'bg-gold',
      desc: '处于倒计时保护窗口内的锁定席位订单'
    },
    {
      label: '超时死信关单 (TIMEOUT)',
      count: s?.timeoutClosedOrders ?? 0,
      pct: Math.round(((s?.timeoutClosedOrders ?? 0) / total) * 1000) / 10,
      bar: 'bg-brick',
      desc: 'RabbitMQ TTL 到期路由 DLX 自动关单释放库存'
    },
    {
      label: '用户主动取消 (CANCEL)',
      count: s?.userCancelledOrders ?? 0,
      pct: Math.round(((s?.userCancelledOrders ?? 0) / total) * 1000) / 10,
      bar: 'bg-stone-400',
      desc: '买方手动取消并即时归还锁定座位'
    }
  ]
})

const filteredOrders = computed(() => {
  const list = state.orderSales?.recentOrders || []
  if (orderFilter.value === 'ALL') return list
  return list.filter(o => o.status === orderFilter.value)
})

const statusBadge: Record<OrderStatus, { label: string; cls: string }> = {
  PAID: { label: '已出票', cls: 'bg-sage-soft text-sage' },
  UNPAID: { label: '待支付', cls: 'bg-gold-soft text-gold' },
  CLOSED: { label: '已关闭', cls: 'bg-stone-100 text-ink-soft' }
}

function sharePct(part: number, whole: number): string {
  if (!whole || whole <= 0) return '0.0'
  return ((part / whole) * 100).toFixed(1)
}
</script>

<template>
  <div class="space-y-12">
    <!-- Editorial Header -->
    <header class="flex flex-wrap items-end justify-between gap-6">
      <div class="max-w-2xl">
        <p class="eyebrow">Box Office &amp; Sales Analytics · ADMIN</p>
        <h1 class="mt-2 text-[28px] font-semibold tracking-tight">全平台票房与销量展示面板</h1>
        <p class="mt-2.5 text-sm leading-relaxed text-ink-soft">
          实时聚合演出库存库 (<span class="font-mono text-xs text-ink">ticket_show</span>) 与订单交易库
          (<span class="font-mono text-xs text-ink">ticket_order</span>) 的实收票房、剧目上座率、票档营收占比及订单转化漏斗。
        </p>
      </div>
      <div class="flex items-center gap-2.5">
        <button class="btn-ghost h-10 text-xs" @click="state.tab = 'database'">
          <Database class="h-3.5 w-3.5 text-sage" />
          查看三库底层表
        </button>
        <button class="btn-primary h-10" :disabled="refreshing" @click="refresh">
          <RotateCw class="h-3.5 w-3.5" :class="refreshing ? 'animate-spin' : ''" />
          刷新销量数据
        </button>
      </div>
    </header>

    <!-- 1. Core KPI Grid (6 Cards) -->
    <section class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <div class="card p-6 shadow-soft">
        <div class="flex items-center justify-between">
          <span class="text-xs font-medium text-ink-soft">累计实收总票房</span>
          <span class="rounded-full bg-sage-soft px-2.5 py-0.5 text-[11px] font-medium text-sage">PAID 实收</span>
        </div>
        <p class="mt-3 text-[34px] font-semibold leading-none tracking-tight text-ink tabular">
          <span class="mr-1 text-lg font-normal text-ink-soft">¥</span>{{ money(totalBoxOffice) }}
        </p>
        <p class="mt-3 text-xs text-ink-mute tabular">
          占全场潜在总票房池 ¥{{ money(showSum?.potentialBoxOffice) }} 的
          <span class="font-medium text-ink">{{ sharePct(totalBoxOffice, Number(showSum?.potentialBoxOffice || 0)) }}%</span>
        </p>
      </div>

      <div class="card p-6 shadow-soft">
        <div class="flex items-center justify-between">
          <span class="text-xs font-medium text-ink-soft">已售出票席位</span>
          <span class="rounded-full bg-paper px-2.5 py-0.5 font-mono text-[11px] text-ink-soft tabular">
            {{ showSum?.soldSeats ?? 0 }} / {{ showSum?.totalSeats ?? 0 }} 座
          </span>
        </div>
        <p class="mt-3 text-[34px] font-semibold leading-none tracking-tight text-ink tabular">
          {{ showSum?.soldSeats ?? 0 }}<span class="ml-1.5 text-base font-normal text-ink-soft">张</span>
        </p>
        <div class="mt-3 flex items-center gap-3">
          <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-paper">
            <div
              class="h-full rounded-full bg-sage transition-all duration-500"
              :style="{ width: `${Math.min(100, Number(showSum?.occupancyRate || 0))}%` }"
            />
          </div>
          <span class="font-mono text-xs font-medium text-sage tabular">上座率 {{ showSum?.occupancyRate ?? 0 }}%</span>
        </div>
      </div>

      <div class="card p-6 shadow-soft">
        <div class="flex items-center justify-between">
          <span class="text-xs font-medium text-ink-soft">成交订单与客单均价</span>
          <span class="rounded-full bg-brick-soft px-2.5 py-0.5 text-[11px] font-medium text-brick">
            转化率 {{ orderSum?.conversionRate ?? 0 }}%
          </span>
        </div>
        <p class="mt-3 text-[34px] font-semibold leading-none tracking-tight text-ink tabular">
          {{ orderSum?.paidOrders ?? 0 }}<span class="ml-1.5 text-base font-normal text-ink-soft">笔成交</span>
        </p>
        <p class="mt-3 text-xs text-ink-mute tabular">
          平均客单价 <span class="font-medium text-ink">¥{{ money(orderSum?.avgOrderValue) }}</span> ·
          累计创单 {{ orderSum?.totalOrders ?? 0 }} 笔
        </p>
      </div>

      <div class="card p-5">
        <span class="text-xs text-ink-soft">锁座待支付票房 (UNPAID)</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-semibold text-gold tabular">¥{{ money(orderSum?.unpaidAmount) }}</span>
          <span class="font-mono text-xs text-ink-mute tabular">
            {{ orderSum?.unpaidOrders ?? 0 }} 笔 · {{ showSum?.lockedSeats ?? 0 }} 座锁定中
          </span>
        </div>
      </div>

      <div class="card p-5">
        <span class="text-xs text-ink-soft">在售剧目与场次排期</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-semibold text-ink tabular">
            {{ showSum?.totalShows ?? 0 }} 部 · {{ showSum?.totalSessions ?? 0 }} 场
          </span>
          <span class="font-mono text-xs text-ink-mute tabular">余票 {{ showSum?.availableSeats ?? 0 }} 座可售</span>
        </div>
      </div>

      <div class="card p-5">
        <span class="text-xs text-ink-soft">超时关单释放保护</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-semibold text-brick tabular">{{ orderSum?.closedOrders ?? 0 }} 笔</span>
          <span class="font-mono text-xs text-ink-mute tabular">
            DLX 超时 {{ orderSum?.timeoutClosedOrders ?? 0 }} · 取消 {{ orderSum?.userCancelledOrders ?? 0 }}
          </span>
        </div>
      </div>
    </section>

    <!-- 2. Show Box Office Leaderboard & Seat Occupancy -->
    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-line px-7 py-5">
        <div>
          <h2 class="flex items-center gap-2 text-base font-semibold tracking-tight">
            <TrendingUp class="h-4 w-4 text-brick" />
            各剧目实时票房排行与席位去化进度
          </h2>
          <p class="mt-0.5 text-xs text-ink-mute">按已售席位实收票房降序排列 · 实时映射每部演出的已售 / 锁定 / 可售座位占比</p>
        </div>
        <div class="flex items-center gap-4 text-xs text-ink-soft">
          <span class="flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-sm bg-ink" />已售出票</span>
          <span class="flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-sm bg-gold" />锁座待付</span>
          <span class="flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-sm border border-line bg-paper" />可售余票</span>
        </div>
      </div>

      <div class="divide-y divide-line">
        <div
          v-for="(item, idx) in state.showAnalytics?.showRanking || []"
          :key="item.showId"
          class="grid gap-5 px-7 py-6 transition hover:bg-paper/40 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_180px]"
        >
          <!-- Show Identity -->
          <div class="flex items-start gap-4">
            <div
              class="grid h-12 w-12 shrink-0 place-items-center rounded-2xl font-mono text-sm font-semibold text-white"
              :style="{ background: posterTheme(item.showId) }"
            >
              0{{ idx + 1 }}
            </div>
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="rounded-full bg-paper px-2.5 py-0.5 text-[11px] font-medium text-ink-soft">
                  {{ categoryLabel[item.category] ?? item.category }}
                </span>
                <span class="font-mono text-[11px] text-ink-mute">{{ item.showCode }}</span>
                <span class="text-xs text-gold tabular">★ {{ Number(item.rating).toFixed(1) }}</span>
              </div>
              <h3 class="mt-1.5 truncate text-base font-semibold tracking-tight text-ink">
                {{ item.title }}
              </h3>
              <p class="mt-1 text-xs text-ink-mute">
                {{ item.city }} · {{ item.venueName }} · 共 {{ item.sessionCount }} 个排期场次
              </p>
            </div>
          </div>

          <!-- Stacked Occupancy Progress Bar -->
          <div class="flex flex-col justify-center">
            <div class="flex items-center justify-between text-xs tabular">
              <span class="font-medium text-ink">
                已售 {{ item.soldSeats }} 座
                <span v-if="item.lockedSeats" class="ml-1 text-gold">· 锁定 {{ item.lockedSeats }} 座</span>
              </span>
              <span class="text-ink-mute">
                上座率 <strong class="text-ink">{{ item.occupancyRate }}%</strong>（余 {{ item.availableSeats }} / {{ item.totalSeats }}）
              </span>
            </div>
            <div class="mt-2.5 flex h-3 w-full overflow-hidden rounded-full border border-line bg-paper">
              <div
                class="h-full bg-ink transition-all duration-500"
                :style="{ width: `${item.totalSeats ? (item.soldSeats / item.totalSeats) * 100 : 0}%` }"
              />
              <div
                class="h-full bg-gold transition-all duration-500"
                :style="{ width: `${item.totalSeats ? (item.lockedSeats / item.totalSeats) * 100 : 0}%` }"
              />
            </div>
            <p class="mt-1.5 text-[11px] text-ink-mute tabular">
              单剧目满座潜在票房 ¥{{ money(item.potentialBoxOffice) }} · 热度指数 {{ item.hotScore }}
            </p>
          </div>

          <!-- Box Office Figure -->
          <div class="flex flex-col items-start justify-center lg:items-end">
            <span class="text-[11px] uppercase tracking-wider text-ink-mute">实收票房</span>
            <span class="mt-1 text-2xl font-semibold tracking-tight text-ink tabular">
              ¥{{ money(item.soldBoxOffice) }}
            </span>
            <span class="mt-1 rounded-full bg-sage-soft px-2 py-0.5 font-mono text-[11px] font-medium text-sage tabular">
              票房占比 {{ sharePct(Number(item.soldBoxOffice), totalBoxOffice) }}%
            </span>
          </div>
        </div>
      </div>
    </section>

    <!-- 3. Zone Revenue Structure + Order Funnel & Daily Trend -->
    <section class="grid gap-6 lg:grid-cols-2">
      <!-- Ticket Tier / Zone Breakdown -->
      <div class="card flex flex-col justify-between p-7">
        <div>
          <p class="eyebrow">Zone Revenue Mix</p>
          <h3 class="mt-1.5 text-base font-semibold tracking-tight">三大票档分区销量与营收贡献</h3>
          <p class="mt-1 text-xs text-ink-mute">对比内场 VIP、中轴优选看台与标准看台的售罄率与票房结构</p>

          <!-- Combined Zone Revenue Share Strip -->
          <div class="mt-6">
            <div class="flex h-3.5 w-full overflow-hidden rounded-full bg-paper">
              <div
                v-for="z in state.showAnalytics?.zoneStats || []"
                :key="z.zoneCode"
                class="h-full transition-all duration-500"
                :class="zoneTone[z.zoneCode]?.bar || 'bg-ink'"
                :style="{
                  width: `${totalZoneSoldRevenue > 0 ? (Number(z.soldRevenue) / totalZoneSoldRevenue) * 100 : 33.3}%`
                }"
                :title="`${z.zoneName}: ¥${money(z.soldRevenue)}`"
              />
            </div>
          </div>

          <div class="mt-6 space-y-5">
            <div
              v-for="z in state.showAnalytics?.zoneStats || []"
              :key="z.zoneCode"
              class="rounded-2xl border border-line bg-paper/50 p-4"
            >
              <div class="flex items-center justify-between gap-3">
                <div class="flex items-center gap-2.5">
                  <span class="h-2.5 w-2.5 rounded-full" :class="zoneTone[z.zoneCode]?.dot" />
                  <span class="text-sm font-semibold text-ink">{{ z.zoneName }}</span>
                  <span class="rounded-full px-2 py-0.5 font-mono text-[10px] font-medium" :class="zoneTone[z.zoneCode]?.badge">
                    {{ z.zoneCode }}
                  </span>
                </div>
                <span class="text-base font-semibold text-ink tabular">¥{{ money(z.soldRevenue) }}</span>
              </div>

              <div class="mt-3 flex items-center justify-between text-xs text-ink-soft tabular">
                <span>已售 {{ z.soldSeats }} / {{ z.totalSeats }} 席（锁定 {{ z.lockedSeats }} · 可售 {{ z.availableSeats }}）</span>
                <span>
                  去化率 <strong class="text-ink">{{ z.sellThroughRate }}%</strong> ·
                  营收占 <strong class="text-ink">{{ sharePct(Number(z.soldRevenue), totalZoneSoldRevenue) }}%</strong>
                </span>
              </div>
              <div class="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white">
                <div
                  class="h-full rounded-full transition-all duration-500"
                  :class="zoneTone[z.zoneCode]?.bar"
                  :style="{ width: `${Math.min(100, Number(z.sellThroughRate || 0))}%` }"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Order Conversion Funnel & Daily Revenue Bars -->
      <div class="card flex flex-col justify-between p-7">
        <div>
          <p class="eyebrow">Conversion &amp; Daily Trend</p>
          <h3 class="mt-1.5 text-base font-semibold tracking-tight">订单转化漏斗与日度票房走势</h3>
          <p class="mt-1 text-xs text-ink-mute">全平台选座创单、支付出票与死信超时自动释放比例</p>

          <div class="mt-6 space-y-4">
            <div v-for="row in funnelRows" :key="row.label">
              <div class="flex items-baseline justify-between text-xs">
                <span class="font-medium text-ink">{{ row.label }}</span>
                <span class="font-mono text-ink-soft tabular">
                  <strong class="text-ink">{{ row.count }}</strong> 笔 ({{ row.pct }}%)
                </span>
              </div>
              <div class="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-paper">
                <div
                  class="h-full rounded-full transition-all duration-500"
                  :class="row.bar"
                  :style="{ width: `${Math.min(100, row.pct)}%` }"
                />
              </div>
              <p class="mt-1 text-[11px] text-ink-mute">{{ row.desc }}</p>
            </div>
          </div>
        </div>

        <!-- Daily Trend Mini Bar Chart -->
        <div class="mt-7 border-t border-line pt-6">
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold text-ink">按日成交票房分布</span>
            <span class="font-mono text-[11px] text-ink-mute">PAID_AT / CREATED_AT</span>
          </div>
          <div
            v-if="state.orderSales?.dailyTrend?.length"
            class="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4"
          >
            <div
              v-for="d in (state.orderSales?.dailyTrend || []).slice(-4)"
              :key="d.date"
              class="rounded-2xl bg-paper p-3.5"
            >
              <span class="font-mono text-[11px] text-ink-mute">{{ d.date.slice(5) }}</span>
              <p class="mt-1 text-sm font-semibold text-ink tabular">¥{{ money(d.paidRevenue) }}</p>
              <div class="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white">
                <div
                  class="h-full rounded-full bg-brick"
                  :style="{ width: `${Math.max(8, (Number(d.paidRevenue) / maxDailyRevenue) * 100)}%` }"
                />
              </div>
              <p class="mt-1.5 text-[11px] text-ink-mute tabular">{{ d.paidOrders }} 单 · {{ d.paidTickets }} 张</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 4. Session-Level Live Occupancy Matrix -->
    <section class="card overflow-hidden">
      <div class="border-b border-line px-7 py-5">
        <h3 class="text-base font-semibold tracking-tight">排期场次实时上座率与票房明细</h3>
        <p class="mt-0.5 text-xs text-ink-mute">show_session · 精确到每个场次演出厅的已售、锁定、余票及乐观锁版本号</p>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="border-b border-line bg-paper/70 text-xs text-ink-soft">
            <tr>
              <th class="px-6 py-3.5 font-medium">场次编码</th>
              <th class="px-4 py-3.5 font-medium">剧目与场次名称</th>
              <th class="px-4 py-3.5 font-medium">演出厅 / 开演时间</th>
              <th class="px-4 py-3.5 font-medium">席位实时状态</th>
              <th class="px-4 py-3.5 font-medium">上座率</th>
              <th class="px-6 py-3.5 text-right font-medium">场次实收票房</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr
              v-for="ss in state.showAnalytics?.sessionStats || []"
              :key="ss.sessionId"
              class="transition hover:bg-paper/40"
            >
              <td class="whitespace-nowrap px-6 py-4 font-mono text-xs text-ink-soft">
                #{{ ss.sessionId }} · {{ ss.sessionCode }}
              </td>
              <td class="px-4 py-4">
                <p class="font-medium text-ink">{{ shortTitle(ss.showTitle) }}</p>
                <p class="mt-0.5 text-xs text-ink-mute">{{ ss.city }} · {{ ss.sessionTitle }}</p>
              </td>
              <td class="whitespace-nowrap px-4 py-4">
                <p class="text-xs text-ink">{{ ss.hallName }}</p>
                <p class="mt-0.5 font-mono text-[11px] text-ink-mute tabular">{{ ss.startTime }}</p>
              </td>
              <td class="whitespace-nowrap px-4 py-4 text-xs tabular">
                <span class="rounded-md bg-sage-soft px-2 py-0.5 font-medium text-sage">已售 {{ ss.soldSeats }}</span>
                <span
                  v-if="ss.lockedSeats"
                  class="ml-1.5 rounded-md bg-gold-soft px-2 py-0.5 font-medium text-gold"
                >
                  锁定 {{ ss.lockedSeats }}
                </span>
                <span class="ml-1.5 text-ink-mute">余 {{ ss.availableSeats }} / {{ ss.totalSeats }}</span>
              </td>
              <td class="whitespace-nowrap px-4 py-4">
                <div class="flex items-center gap-2.5">
                  <div class="h-1.5 w-20 overflow-hidden rounded-full bg-paper">
                    <div
                      class="h-full rounded-full bg-ink"
                      :style="{ width: `${Math.min(100, Number(ss.occupancyRate || 0))}%` }"
                    />
                  </div>
                  <span class="font-mono text-xs font-medium tabular">{{ ss.occupancyRate }}%</span>
                </div>
              </td>
              <td class="whitespace-nowrap px-6 py-4 text-right font-semibold text-ink tabular">
                ¥{{ money(ss.soldBoxOffice) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 5. Top Buyers Leaderboard (with Real Phones) + Platform-Wide Order Stream -->
    <section class="grid gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
      <!-- Top Buyers -->
      <div class="card overflow-hidden">
        <div class="border-b border-line px-6 py-5">
          <h3 class="flex items-center gap-2 text-base font-semibold tracking-tight">
            <Users class="h-4 w-4 text-ink-soft" />
            购票会员消费榜
          </h3>
          <p class="mt-0.5 text-xs text-ink-mute">关联实名手机号与累计出票消费</p>
        </div>
        <ul class="divide-y divide-line">
          <li
            v-for="(b, idx) in state.orderSales?.buyerLeaderboard || []"
            :key="b.userId"
            class="flex items-center justify-between gap-3 px-6 py-4"
          >
            <div class="flex min-w-0 items-center gap-3">
              <span
                class="grid h-7 w-7 shrink-0 place-items-center rounded-full font-mono text-xs font-semibold"
                :class="idx === 0 ? 'bg-brick text-white' : 'bg-paper text-ink-soft'"
              >
                {{ idx + 1 }}
              </span>
              <div class="min-w-0">
                <p class="truncate text-sm font-medium text-ink">
                  {{ userLookup.get(b.userId)?.nickname || b.username }}
                </p>
                <p class="mt-0.5 flex items-center gap-1 font-mono text-[11px] text-ink-mute tabular">
                  <Smartphone class="h-3 w-3" />
                  {{ userLookup.get(b.userId)?.phone || `@${b.username}` }}
                </p>
              </div>
            </div>
            <div class="shrink-0 text-right tabular">
              <p class="text-sm font-semibold text-ink">¥{{ money(b.totalSpent) }}</p>
              <p class="mt-0.5 text-[11px] text-ink-mute">{{ b.paidOrders }} 单 · {{ b.paidTickets }} 张</p>
            </div>
          </li>
          <li v-if="!state.orderSales?.buyerLeaderboard?.length" class="px-6 py-12 text-center text-sm text-ink-mute">
            暂无已支付购票记录
          </li>
        </ul>
      </div>

      <!-- Platform-Wide Recent Orders Stream -->
      <div class="card overflow-hidden">
        <div class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-6 py-5">
          <div>
            <h3 class="text-base font-semibold tracking-tight">全平台订单成交与状态流水</h3>
            <p class="mt-0.5 text-xs text-ink-mute">汇总所有购票会员的实时订单，点击已出票订单可调阅防伪电子票</p>
          </div>
          <div class="inline-flex rounded-full bg-ink/[0.05] p-1 text-xs">
            <button
              v-for="f in ([
                { key: 'ALL', label: '全部' },
                { key: 'PAID', label: '已出票' },
                { key: 'UNPAID', label: '待支付' },
                { key: 'CLOSED', label: '已关闭' }
              ] as const)"
              :key="f.key"
              class="rounded-full px-3 py-1 transition"
              :class="orderFilter === f.key ? 'bg-white font-medium text-ink shadow-sm' : 'text-ink-soft hover:text-ink'"
              @click="orderFilter = f.key"
            >
              {{ f.label }}
            </button>
          </div>
        </div>

        <div class="max-h-[480px] overflow-auto">
          <table class="w-full text-left text-xs">
            <thead class="sticky top-0 border-b border-line bg-paper text-[11px] text-ink-soft">
              <tr>
                <th class="px-5 py-3 font-medium">订单号 / 时间</th>
                <th class="px-3 py-3 font-medium">购票会员 / 手机号</th>
                <th class="px-3 py-3 font-medium">演出与席位</th>
                <th class="px-3 py-3 font-medium">状态</th>
                <th class="px-5 py-3 text-right font-medium">金额 / 操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line">
              <tr
                v-for="o in filteredOrders"
                :key="o.orderNo"
                class="transition hover:bg-paper/40"
              >
                <td class="whitespace-nowrap px-5 py-3.5">
                  <p class="font-mono font-medium text-ink">{{ o.orderNo }}</p>
                  <p class="mt-0.5 font-mono text-[11px] text-ink-mute tabular">{{ shortTime(o.paidAt || o.createdAt) }}</p>
                </td>
                <td class="whitespace-nowrap px-3 py-3.5">
                  <p class="font-medium text-ink">@{{ o.username }}</p>
                  <p class="mt-0.5 font-mono text-[11px] text-ink-soft tabular">
                    {{ (o.userId && userLookup.get(o.userId)?.phone) || '虚拟压测席位' }}
                  </p>
                </td>
                <td class="max-w-[260px] px-3 py-3.5">
                  <p class="truncate font-medium text-ink">{{ shortTitle(o.showTitle) }} · {{ o.sessionTitle }}</p>
                  <p class="mt-0.5 truncate text-[11px] text-ink-mute">
                    {{ parseList(o.seatLabelsJson).join('、') }}（{{ o.seatCount }} 张）
                  </p>
                </td>
                <td class="whitespace-nowrap px-3 py-3.5">
                  <span
                    class="inline-flex rounded-full px-2.5 py-0.5 text-[11px] font-medium"
                    :class="statusBadge[o.status]?.cls"
                  >
                    {{ statusBadge[o.status]?.label ?? o.status }}
                  </span>
                  <p v-if="o.status === 'PAID' && o.ticketCode" class="mt-1 font-mono text-[10px] text-ink-mute">
                    {{ o.ticketCode }}
                  </p>
                  <p v-else-if="o.status === 'CLOSED' && o.closeReason" class="mt-1 text-[10px] text-ink-mute">
                    {{ closeReasonLabel[o.closeReason] ?? o.closeReason }}
                  </p>
                </td>
                <td class="whitespace-nowrap px-5 py-3.5 text-right tabular">
                  <p class="text-sm font-semibold text-ink">¥{{ money(o.totalAmount) }}</p>
                  <button
                    v-if="o.status === 'PAID'"
                    class="mt-1 inline-flex items-center gap-0.5 text-[11px] font-medium text-brick hover:underline"
                    @click="state.ticket = o"
                  >
                    <Ticket class="h-3 w-3" />
                    电子票
                    <ArrowUpRight class="h-3 w-3" />
                  </button>
                </td>
              </tr>
              <tr v-if="!filteredOrders.length">
                <td colspan="5" class="px-5 py-12 text-center text-sm text-ink-mute">当前筛选条件下暂无订单记录</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>
