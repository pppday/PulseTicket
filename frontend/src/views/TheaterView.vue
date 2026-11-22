<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, BarChart3, CloudUpload, Database, Pencil, Plus } from 'lucide-vue-next'
import { api } from '../api'
import SeatMap from '../components/SeatMap.vue'
import CheckoutPanel from '../components/CheckoutPanel.vue'
import ShowManageModal from '../components/ShowManageModal.vue'
import StressLab from '../components/StressLab.vue'
import {
  isAdmin,
  loadCatalog,
  loadSeats,
  probePenetration,
  rebuildCache,
  refreshAll,
  selectShow,
  state,
  toast
} from '../store'
import type { SeatItem, ShowInfo } from '../types'
import { cacheLabel, categoryLabel, money, posterTheme, shortTitle } from '../utils'

const manageOpen = ref(false)
const editingShow = ref<ShowInfo | null>(null)
const reorderingCard = ref(false)

function openShowManager(show?: ShowInfo | null) {
  editingShow.value = show ?? null
  manageOpen.value = true
}

async function moveShowOnHome(showId: number, delta: -1 | 1) {
  if (reorderingCard.value) return
  const ids = state.catalog.map(c => c.show.id)
  const idx = ids.indexOf(showId)
  const targetIdx = idx + delta
  if (idx < 0 || targetIdx < 0 || targetIdx >= ids.length) return
  const [moved] = ids.splice(idx, 1)
  ids.splice(targetIdx, 0, moved)

  reorderingCard.value = true
  try {
    const res = await api('/api/show/admin/reorder', {
      method: 'POST',
      body: JSON.stringify({ orderedIds: ids })
    })
    if (res.code === 200) {
      await loadCatalog()
      toast('success', '演出排序已调整', delta < 0 ? '已向前移动一位' : '已向后移动一位')
    }
  } finally {
    reorderingCard.value = false
  }
}

function cardPosterStyle(show: ShowInfo) {
  if (show.posterUrl) {
    return {
      backgroundImage: `linear-gradient(180deg, rgba(20,19,17,0.42) 0%, rgba(20,19,17,0.18) 45%, rgba(20,19,17,0.82) 100%), url("${show.posterUrl}")`,
      backgroundSize: 'cover',
      backgroundPosition: 'center',
      backgroundRepeat: 'no-repeat'
    }
  }
  return { background: posterTheme(show.id) }
}

const categoryFilter = ref<string>('ALL')
const categories = [
  { key: 'ALL', label: '全部演出' },
  { key: 'CONCERT', label: '演唱会' },
  { key: 'MUSICAL', label: '音乐剧' },
  { key: 'IMAX_MOVIE', label: 'IMAX 电影' },
  { key: 'LIVEHOUSE', label: 'Livehouse' }
]

const vipLabelMap: Record<string, string> = {
  BLACK_DIAMOND: '黑钻会员',
  DIAMOND: '黑钻会员',
  GOLD: '金卡会员',
  NORMAL: '观演会员'
}

const filteredCatalog = computed(() =>
  categoryFilter.value === 'ALL'
    ? state.catalog
    : state.catalog.filter(c => c.show.category === categoryFilter.value)
)

watch(
  () => [state.showId, state.catalog.length] as const,
  ([id]) => {
    if (
      categoryFilter.value !== 'ALL' &&
      id &&
      !filteredCatalog.value.some(c => c.show.id === id)
    ) {
      categoryFilter.value = 'ALL'
    }
  }
)

async function chooseCategory(cat: string) {
  categoryFilter.value = cat
  const list = cat === 'ALL' ? state.catalog : state.catalog.filter(c => c.show.category === cat)
  if (list.length && !list.some(c => c.show.id === state.showId)) {
    await selectShow(list[0].show.id)
  }
}

const fastClose = ref(true)
const rollback = ref(false)
const submitting = ref(false)

const selectedSeats = computed(() =>
  state.selected.map(id => state.seats.find(s => s.id === id)).filter(Boolean) as SeatItem[]
)
const session = computed(() => state.sessions.find(s => s.id === state.sessionId))
const availableCount = computed(() => state.seats.filter(s => s.status === 'AVAILABLE').length)

function toggle(seat: SeatItem) {
  const i = state.selected.indexOf(seat.id)
  if (i >= 0) {
    state.selected.splice(i, 1)
  } else if (state.selected.length >= 4) {
    toast('info', '每笔订单最多可选择 4 个座位')
  } else {
    state.selected.push(seat.id)
  }
}

async function submit() {
  if (!selectedSeats.value.length) return
  submitting.value = true
  const useFastClose = isAdmin.value && fastClose.value
  const withRollback = isAdmin.value && rollback.value
  try {
    const res = await api('/api/order/create', {
      method: 'POST',
      body: JSON.stringify({
        sessionId: state.sessionId,
        seatIds: [...state.selected],
        fastCloseDemo: useFastClose,
        simulateSeataRollback: withRollback
      })
    })
    await refreshAll()

    if (res.code === 200) {
      state.selected = []
      toast(
        'success',
        '座位已锁定',
        `订单 ${res.data.orderNo}，请在 ${useFastClose ? '15 秒' : '15 分钟'}内完成支付`
      )
      state.tab = 'orders'
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } else if (withRollback) {
      toast('info', '事务已回滚', '模拟异常触发 Seata 二阶段回滚，座位已恢复可选')
    } else {
      toast('error', '锁座失败', res.message)
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="space-y-12">
    <!-- Homepage Member Real-Name Identity & Real Phone Strip -->
    <section
      v-if="state.user"
      class="card flex flex-wrap items-center justify-between gap-4 px-6 py-4 shadow-soft"
    >
      <div class="flex flex-wrap items-center gap-4">
        <div class="flex items-center gap-3">
          <span
            class="grid h-10 w-10 place-items-center rounded-2xl text-sm font-semibold text-white"
            :class="isAdmin ? 'bg-brick' : 'bg-ink'"
          >
            {{ (state.user.nickname || state.user.username).charAt(0).toUpperCase() }}
          </span>
          <div>
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-sm font-semibold text-ink">{{ state.user.nickname }}</span>
              <span
                class="rounded-full px-2.5 py-0.5 text-[11px] font-medium"
                :class="isAdmin ? 'bg-brick-soft text-brick' : 'bg-paper text-ink-soft'"
              >
                {{ isAdmin ? 'ADMIN · 架构运维专员' : vipLabelMap[state.user.vipLevel] || '观演会员' }}
              </span>
            </div>
            <p class="mt-0.5 font-mono text-xs text-ink-mute">
              @{{ state.user.username }} · 累计购票 {{ state.user.totalOrders }} 单 · 可用 {{ money(state.user.pointsBalance) }} 积分
            </p>
          </div>
        </div>
      </div>

      <div v-if="isAdmin" class="flex flex-wrap items-center gap-3">
        <!-- Quick ADMIN jump pills right on homepage -->
        <button
          type="button"
          class="btn-ghost h-9 px-3.5 text-xs"
          @click="openShowManager(state.show)"
        >
          <CloudUpload class="h-3.5 w-3.5 text-gold" />
          OSS 演出与海报管理
        </button>
        <button
          type="button"
          class="btn-ghost h-9 px-3.5 text-xs"
          @click="state.tab = 'sales'"
        >
          <BarChart3 class="h-3.5 w-3.5 text-brick" />
          票房销量面板
        </button>
        <button
          type="button"
          class="btn-ghost h-9 px-3.5 text-xs"
          @click="state.tab = 'database'"
        >
          <Database class="h-3.5 w-3.5 text-sage" />
          数据库总览
        </button>
      </div>
    </section>

    <!-- Shows -->
    <section>
      <div class="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p class="eyebrow">正在热售</p>
          <h1 class="mt-2 text-[28px] font-semibold tracking-tight">近期热门演出与放映</h1>
        </div>

        <div class="flex flex-wrap items-center gap-1.5">
          <button
            v-if="isAdmin"
            type="button"
            class="inline-flex items-center gap-1 rounded-full border border-line bg-white px-3.5 py-1.5 text-xs text-ink-soft transition hover:border-ink/30 hover:text-ink"
            @click="openShowManager(null)"
          >
            <Plus class="h-3.5 w-3.5 text-gold" />
            发布新演出
          </button>
          <button
            v-for="cat in categories"
            :key="cat.key"
            class="rounded-full px-3.5 py-1.5 text-xs transition"
            :class="
              categoryFilter === cat.key
                ? 'bg-ink font-medium text-white'
                : 'border border-line bg-white text-ink-soft hover:border-ink/30 hover:text-ink'
            "
            @click="chooseCategory(cat.key)"
          >
            {{ cat.label }}
          </button>
        </div>
      </div>

      <div class="mt-7 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <button
          v-for="item in filteredCatalog"
          :key="item.show.id"
          class="group rounded-[22px] border bg-white p-2.5 text-left transition duration-200"
          :class="
            state.showId === item.show.id
              ? 'border-ink/80 shadow-soft'
              : 'border-line hover:-translate-y-0.5 hover:border-ink/25 hover:shadow-soft'
          "
          @click="selectShow(item.show.id)"
        >
          <div
            class="relative flex aspect-[4/3] flex-col justify-between overflow-hidden rounded-2xl p-5 text-white"
            :style="cardPosterStyle(item.show)"
          >
            <!-- Fallback bespoke geometric editorial line art when no OSS poster is set -->
            <template v-if="!item.show.posterUrl">
              <svg
                v-if="item.show.category === 'CONCERT'"
                viewBox="0 0 160 120"
                class="pointer-events-none absolute inset-0 h-full w-full opacity-20"
                fill="none"
                stroke="currentColor"
              >
                <circle cx="132" cy="96" r="26" stroke-width="1" />
                <circle cx="132" cy="96" r="46" stroke-width="0.8" />
                <circle cx="132" cy="96" r="68" stroke-width="0.6" />
              </svg>
              <svg
                v-else-if="item.show.category === 'MUSICAL'"
                viewBox="0 0 160 120"
                class="pointer-events-none absolute inset-0 h-full w-full opacity-20"
                fill="none"
                stroke="currentColor"
              >
                <path d="M92 120 V54 A32 32 0 0 1 156 54 V120" stroke-width="1" />
                <path d="M104 120 V58 A20 20 0 0 1 144 58 V120" stroke-width="0.8" />
              </svg>
              <svg
                v-else-if="item.show.category === 'IMAX_MOVIE'"
                viewBox="0 0 160 120"
                class="pointer-events-none absolute inset-0 h-full w-full opacity-20"
                fill="none"
                stroke="currentColor"
              >
                <circle cx="122" cy="74" r="24" stroke-width="1" />
                <ellipse cx="122" cy="74" rx="52" ry="14" transform="rotate(-18 122 74)" stroke-width="0.9" />
              </svg>
              <svg
                v-else
                viewBox="0 0 160 120"
                class="pointer-events-none absolute inset-0 h-full w-full opacity-20"
                fill="none"
                stroke="currentColor"
              >
                <circle cx="128" cy="84" r="10" stroke-width="1" />
                <circle cx="128" cy="84" r="28" stroke-width="0.8" stroke-dasharray="4 3" />
                <circle cx="128" cy="84" r="44" stroke-width="0.8" />
              </svg>
            </template>

            <div class="relative flex items-center justify-between text-[11px] tracking-[0.14em] text-white/80">
              <span class="uppercase">{{ categoryLabel[item.show.category] ?? item.show.category }}</span>
              <span class="tracking-normal text-white/85 tabular">{{ item.show.rating.toFixed(1) }}</span>
            </div>
            <span class="relative text-lg font-semibold leading-snug tracking-tight drop-shadow-sm">
              {{ shortTitle(item.show.title) }}
            </span>
          </div>

          <div class="px-2 pb-2 pt-4">
            <p class="truncate text-xs text-ink-mute">
              {{ item.show.city }} · {{ item.show.venueName.split(' · ')[0] }}
            </p>
            <div class="mt-2 flex items-baseline justify-between">
              <p class="text-sm tabular">
                <span class="font-medium">¥{{ money(item.show.minPrice) }}</span>
                <span class="text-xs text-ink-mute"> 起</span>
              </p>
              <div class="flex items-center gap-1.5">
                <template v-if="isAdmin && categoryFilter === 'ALL'">
                  <span
                    role="button"
                    tabindex="0"
                    class="grid h-5 w-5 place-items-center rounded-full border border-line text-ink-mute transition hover:border-ink/40 hover:text-ink"
                    title="向前移动顺位"
                    @click.stop="moveShowOnHome(item.show.id, -1)"
                  >
                    <ArrowLeft class="h-2.5 w-2.5" />
                  </span>
                  <span
                    role="button"
                    tabindex="0"
                    class="grid h-5 w-5 place-items-center rounded-full border border-line text-ink-mute transition hover:border-ink/40 hover:text-ink"
                    title="向后移动顺位"
                    @click.stop="moveShowOnHome(item.show.id, 1)"
                  >
                    <ArrowRight class="h-2.5 w-2.5" />
                  </span>
                </template>
                <span
                  v-if="isAdmin"
                  role="button"
                  tabindex="0"
                  class="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-soft transition hover:border-ink/40 hover:text-ink"
                  @click.stop="openShowManager(item.show)"
                >
                  <Pencil class="h-2.5 w-2.5" />
                  管理
                </span>
                <span v-if="state.showId === item.show.id" class="text-[11px] font-medium text-ink">
                  正在选座
                </span>
              </div>
            </div>
          </div>
        </button>
      </div>
    </section>

    <!-- Seat selection -->
    <section v-if="state.show" class="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div class="card">
        <div class="p-8 pb-7 md:p-10 md:pb-8">
          <div class="flex flex-wrap items-center justify-between gap-4">
            <p class="eyebrow">{{ categoryLabel[state.show.category] }} · {{ state.show.city }}</p>

            <!-- ADMIN-only cache telemetry controls -->
            <div v-if="isAdmin" class="flex items-center gap-3 text-xs text-ink-soft">
              <span
                class="inline-flex items-center gap-1.5 rounded-full bg-paper px-2.5 py-1"
                :title="`${state.cache.status} · TTL ${state.cache.ttl ?? '-'}s`"
              >
                <span
                  class="h-1.5 w-1.5 rounded-full"
                  :class="state.cache.status === 'REDIS_HIT' ? 'bg-sage' : 'bg-gold'"
                />
                {{ cacheLabel[state.cache.status] ?? state.cache.status }}
              </span>
              <button class="transition hover:text-ink" @click="rebuildCache">重建缓存</button>
              <span class="h-3 w-px bg-line" />
              <button class="transition hover:text-ink" @click="probePenetration">穿透测试</button>
            </div>

            <!-- Pure commercial badge for normal buyers -->
            <span v-else class="rounded-full bg-paper px-3 py-1 text-xs text-ink-soft">
              {{ state.show.badgeText || '官方电子票 · 实名选座' }}
            </span>
          </div>

          <h2 class="mt-3 text-2xl font-semibold leading-snug tracking-tight">{{ state.show.title }}</h2>
          <p class="mt-2 text-sm text-ink-soft">{{ state.show.venueName }}</p>
          <p v-if="state.show.subtitle" class="mt-1.5 text-xs leading-relaxed text-ink-mute">
            {{ state.show.subtitle }}
          </p>

          <div class="mt-7 flex flex-wrap gap-2">
            <button
              v-for="s in state.sessions"
              :key="s.id"
              class="rounded-full border px-4 py-2 text-sm transition"
              :class="
                state.sessionId === s.id
                  ? 'border-ink bg-ink text-white'
                  : 'border-line text-ink-soft hover:border-ink/30 hover:text-ink'
              "
              @click="loadSeats(s.id)"
            >
              {{ s.sessionTitle }}
            </button>
          </div>
        </div>

        <div class="border-t border-line p-8 md:p-10">
          <div class="mb-8 flex items-center justify-between text-xs text-ink-soft">
            <span>{{ session?.hallName }}</span>
            <span class="tabular">剩余可选 {{ availableCount }} / {{ state.seats.length }} 座</span>
          </div>
          <SeatMap :seats="state.seats" :selected="state.selected" @toggle="toggle" />
        </div>
      </div>

      <aside class="lg:sticky lg:top-24">
        <CheckoutPanel
          v-model:fast-close="fastClose"
          v-model:rollback="rollback"
          :seats="selectedSeats"
          :session="session"
          :submitting="submitting"
          :is-admin="isAdmin"
          :user-phone="state.user?.phone"
          @remove="toggle"
          @submit="submit"
        />
      </aside>
    </section>

    <!-- Stress testing lab is strictly isolated to the dedicated ADMIN account -->
    <StressLab v-if="isAdmin" />

    <!-- Admin Dynamic Show & Aliyun OSS Poster Manager Modal -->
    <ShowManageModal
      v-if="isAdmin"
      :open="manageOpen"
      :initial-show="editingShow"
      @close="manageOpen = false"
    />
  </div>
</template>
