<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import AppHeader from './components/AppHeader.vue'
import ToastHost from './components/ToastHost.vue'
import TicketModal from './components/TicketModal.vue'
import MessagesDrawer from './components/MessagesDrawer.vue'
import AgentChatWidget from './components/AgentChatWidget.vue'
import AuthView from './views/AuthView.vue'
import VerifyTicketView from './views/VerifyTicketView.vue'
import TheaterView from './views/TheaterView.vue'
import OrdersView from './views/OrdersView.vue'
import SalesView from './views/SalesView.vue'
import DatabaseView from './views/DatabaseView.vue'
import TelemetryView from './views/TelemetryView.vue'
import { isAdmin, refreshAll, restoreSession, state } from './store'
import { remainingSeconds } from './utils'

const verifyCode = ref(new URLSearchParams(window.location.search).get('verify')?.trim() || '')

let timer: ReturnType<typeof setInterval> | undefined
let lastExpiredRefresh = 0

async function exitVerify() {
  const url = new URL(window.location.href)
  url.searchParams.delete('verify')
  window.history.replaceState({}, '', url.pathname + url.search)
  verifyCode.value = ''
  if (!state.authReady) {
    await restoreSession()
  }
}

onMounted(async () => {
  if (!verifyCode.value) {
    await restoreSession()
  }

  // 每秒驱动倒计时；有订单到期时节流刷新，等待死信消费者关单
  timer = setInterval(() => {
    state.now = Date.now()
    if (!state.user) return
    const expired = state.orders.some(
      o => o.status === 'UNPAID' && remainingSeconds(o.expireAt, state.now) === 0
    )
    if (expired && state.now - lastExpiredRefresh > 2000) {
      lastExpiredRefresh = state.now
      refreshAll()
    }
  }, 1000)
})

onUnmounted(() => timer && clearInterval(timer))
</script>

<template>
  <!-- Public WeChat / Camera QR Gate Verification H5 Page (Zero Login Required) -->
  <VerifyTicketView v-if="verifyCode" :ticket-code="verifyCode" @exit="exitVerify" />

  <!-- Initial JWT session restoration guard (prevents login screen flash on F5) -->
  <div v-else-if="!state.authReady" class="grid min-h-screen place-items-center bg-paper">
    <p class="text-xs tracking-widest text-ink-mute uppercase">PulseTicket</p>
  </div>

  <!-- Unauthenticated Gate: Login / Registration -->
  <div v-else-if="!state.user">
    <AuthView />
    <ToastHost />
  </div>

  <!-- Authenticated Application Shell -->
  <div v-else class="flex min-h-screen flex-col">
    <AppHeader />

    <main class="mx-auto w-full max-w-[1200px] flex-1 px-6 pb-24 pt-12">
      <Transition
        mode="out-in"
        enter-active-class="transition duration-200 ease-out"
        enter-from-class="opacity-0 translate-y-1"
        leave-active-class="transition duration-100"
        leave-to-class="opacity-0"
      >
        <TheaterView v-if="state.tab === 'theater'" />
        <OrdersView v-else-if="state.tab === 'orders'" />
        <SalesView v-else-if="state.tab === 'sales' && isAdmin" />
        <DatabaseView v-else-if="state.tab === 'database' && isAdmin" />
        <TelemetryView v-else-if="state.tab === 'telemetry' && isAdmin" />
        <TheaterView v-else />
      </Transition>
    </main>

    <footer class="border-t border-line">
      <div class="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-3 px-6 py-8 text-xs text-ink-mute">
        <span>PulseTicket · 演出与影院在线选座平台</span>
        <span v-if="isAdmin">Spring Cloud Alibaba · Seata AT · RabbitMQ DLX · Redisson · Vue 3</span>
        <span v-else>一票一码防伪核验 · 官方实名选座保障</span>
      </div>
    </footer>

    <ToastHost />
    <TicketModal />
    <MessagesDrawer />
    <AgentChatWidget />
  </div>
</template>
