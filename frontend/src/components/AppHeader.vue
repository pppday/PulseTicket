<script setup lang="ts">
/**
 * 【顶部全局导航栏组件（AppHeader）】
 * 根据当前登录用户角色动态展示导航标签（普通用户看“演出选座/我的订单”，管理员额外可见“票房销量/数据库总览/架构观测”），
 * 右侧显示待支付红点、未读站内信提醒以及会员资料/退出登录菜单。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Bell, ChevronDown, LogOut, ShieldCheck, Ticket } from 'lucide-vue-next'
import {
  isAdmin,
  loadAdminDatabases,
  loadAdminSales,
  loadOrders,
  loadProfile,
  loadTelemetry,
  logout,
  state
} from '../store'
import { money } from '../utils'
import type { Tab } from '../types'

const menuOpen = ref(false)
const menuRef = ref<HTMLElement | null>(null)

const nav = computed<{ key: Tab; label: string }[]>(() => {
  const base: { key: Tab; label: string }[] = [
    { key: 'theater', label: '演出选座' },
    { key: 'orders', label: '我的订单' }
  ]
  if (isAdmin.value) {
    base.push(
      { key: 'sales', label: '票房销量' },
      { key: 'database', label: '数据库总览' },
      { key: 'telemetry', label: '架构观测' }
    )
  }
  return base
})

const unpaid = computed(() => state.orders.filter(o => o.status === 'UNPAID').length)
const unread = computed(() => state.messages.filter(m => !m.isRead).length)

const vipLabelMap: Record<string, string> = {
  BLACK_DIAMOND: '黑钻会员',
  DIAMOND: '黑钻会员',
  GOLD: '金卡会员',
  NORMAL: '观演会员'
}

const roleBadgeText = computed(() => {
  if (!state.user) return ''
  if (isAdmin.value) return '架构运维专员'
  return vipLabelMap[state.user.vipLevel] || '观演会员'
})

const displayName = computed(() => {
  const raw = (state.user?.nickname || state.user?.username || '').trim()
  return raw.split(' · ')[0]
})

const avatarChar = computed(() => {
  return (displayName.value || 'P').charAt(0).toUpperCase()
})

function go(tab: Tab) {
  menuOpen.value = false
  state.tab = tab
  if (tab === 'orders') loadOrders()
  if (tab === 'sales' && isAdmin.value) loadAdminSales()
  if (tab === 'database' && isAdmin.value) loadAdminDatabases()
  if (tab === 'telemetry' && isAdmin.value) Promise.all([loadTelemetry(), loadProfile()])
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function handleLogout() {
  menuOpen.value = false
  logout()
}

function onDocClick(e: MouseEvent) {
  if (menuRef.value && !menuRef.value.contains(e.target as Node)) menuOpen.value = false
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <header class="sticky top-0 z-30 border-b border-line bg-paper/85 backdrop-blur-md">
    <div class="mx-auto flex h-16 max-w-[1200px] items-center justify-between px-6">
      <div class="flex items-center gap-8">
        <button class="flex items-center gap-2.5" @click="go('theater')">
          <span class="grid h-8 w-8 place-items-center rounded-[10px] bg-ink">
            <Ticket class="h-4 w-4 text-paper" :stroke-width="2" />
          </span>
          <span class="text-[17px] font-semibold tracking-tight">PulseTicket</span>
          <span
            v-if="isAdmin"
            class="ml-1 hidden items-center gap-1 rounded-full bg-brick-soft px-2.5 py-0.5 text-[11px] font-medium text-brick lg:inline-flex"
          >
            <ShieldCheck class="h-3 w-3" />
            ADMIN 控制台
          </span>
        </button>

        <nav class="hidden items-center gap-1 md:flex">
          <button
            v-for="item in nav"
            :key="item.key"
            class="relative flex items-center gap-1.5 rounded-full px-3.5 py-2 text-sm transition"
            :class="state.tab === item.key ? 'bg-ink/[0.06] font-medium text-ink' : 'text-ink-soft hover:text-ink'"
            @click="go(item.key)"
          >
            {{ item.label }}
            <span
              v-if="item.key === 'orders' && unpaid"
              class="grid h-[18px] min-w-[18px] place-items-center rounded-full bg-brick px-1 text-[11px] font-medium text-white tabular"
            >
              {{ unpaid }}
            </span>
          </button>
        </nav>
      </div>

      <div class="flex items-center gap-1">
        <span v-if="state.user" class="mr-3 hidden text-sm text-ink-soft tabular sm:inline">
          {{ money(state.user.pointsBalance) }} 积分
        </span>

        <button
          class="relative grid h-9 w-9 place-items-center rounded-full text-ink-soft transition hover:bg-ink/[0.06] hover:text-ink"
          title="通知"
          @click="state.messagesOpen = true"
        >
          <Bell class="h-[18px] w-[18px]" :stroke-width="1.75" />
          <span v-if="unread" class="absolute right-2 top-2 h-2 w-2 rounded-full bg-brick ring-2 ring-paper" />
        </button>

        <div ref="menuRef" class="relative ml-0.5">
          <button
            class="flex items-center gap-2 rounded-full py-1 pl-1 pr-3 transition hover:bg-ink/[0.06]"
            @click="menuOpen = !menuOpen"
          >
            <span
              class="grid h-7 w-7 place-items-center rounded-full text-xs font-medium text-white"
              :class="isAdmin ? 'bg-brick' : 'bg-ink'"
            >
              {{ avatarChar }}
            </span>
            <span class="text-sm font-medium">{{ displayName }}</span>
            <ChevronDown class="h-3.5 w-3.5 text-ink-mute" />
          </button>

          <Transition
            enter-active-class="transition duration-150 ease-out"
            enter-from-class="opacity-0 -translate-y-1"
            leave-active-class="transition duration-100 ease-in"
            leave-to-class="opacity-0 -translate-y-1"
          >
            <div
              v-if="menuOpen && state.user"
              class="absolute right-0 mt-2 w-64 overflow-hidden rounded-2xl border border-line bg-white p-2 shadow-float"
            >
              <div class="rounded-xl bg-paper px-3.5 py-3">
                <div class="flex items-center justify-between gap-2">
                  <span class="truncate text-sm font-semibold text-ink">{{ displayName }}</span>
                  <span
                    class="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium"
                    :class="isAdmin ? 'bg-brick-soft text-brick' : 'bg-white text-ink-soft'"
                  >
                    {{ roleBadgeText }}
                  </span>
                </div>
                <p class="mt-1 font-mono text-xs text-ink-mute tabular">@{{ state.user.username }} · {{ state.user.phone }}</p>
              </div>

              <div class="mt-1.5 space-y-0.5 whitespace-nowrap">
                <button
                  class="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm text-ink-soft transition hover:bg-paper hover:text-ink"
                  @click="go('orders')"
                >
                  <span>我的订单</span>
                  <span v-if="unpaid" class="text-xs font-medium text-brick tabular">{{ unpaid }} 待付</span>
                </button>
                <template v-if="isAdmin">
                  <button
                    class="flex w-full items-center rounded-xl px-3 py-2 text-left text-sm text-ink-soft transition hover:bg-paper hover:text-ink"
                    @click="go('sales')"
                  >
                    票房销量
                  </button>
                  <button
                    class="flex w-full items-center rounded-xl px-3 py-2 text-left text-sm text-ink-soft transition hover:bg-paper hover:text-ink"
                    @click="go('database')"
                  >
                    数据库总览
                  </button>
                  <button
                    class="flex w-full items-center rounded-xl px-3 py-2 text-left text-sm text-ink-soft transition hover:bg-paper hover:text-ink"
                    @click="go('telemetry')"
                  >
                    架构观测
                  </button>
                </template>
              </div>

              <div class="my-1.5 border-t border-line" />

              <button
                class="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-brick transition hover:bg-brick-soft/60"
                @click="handleLogout"
              >
                <LogOut class="h-4 w-4" />
                <span>退出登录</span>
              </button>
            </div>
          </Transition>
        </div>
      </div>
    </div>

    <!-- Mobile navigation -->
    <nav class="flex items-center gap-1 overflow-x-auto border-t border-line px-4 py-2 md:hidden">
      <button
        v-for="item in nav"
        :key="item.key"
        class="flex shrink-0 items-center justify-center gap-1.5 rounded-full px-3 py-1.5 text-xs transition"
        :class="state.tab === item.key ? 'bg-ink/[0.06] font-medium text-ink' : 'text-ink-soft'"
        @click="go(item.key)"
      >
        {{ item.label }}
        <span
          v-if="item.key === 'orders' && unpaid"
          class="grid h-4 min-w-[16px] place-items-center rounded-full bg-brick px-1 text-[10px] font-medium text-white tabular"
        >
          {{ unpaid }}
        </span>
      </button>
    </nav>
  </header>
</template>
