<script setup lang="ts">
/**
 * 根组件（整个前端应用的“总控台”和页面外壳）。
 * 主要干三件事：
 * 1) 路由分流：如果 URL 带着 ?verify=取票码（比如手机微信扫码进来的），免登录直接进电子票核验页；否则先走 JWT 静默恢复登录态，未登录进登录页，已登录进主工作台；
 * 2) 按角色切页面：普通用户看“在线选座 / 我的订单”，管理员额外能切到“票房销售 / 多库总览 / 链路观测”；
 * 3) 全局 1 秒心跳定时器：推动所有未支付订单倒计时跳动，一旦有订单倒计时归零，节流刷新全量数据等待后端 RabbitMQ 死信队列自动关单释放座位。
 */
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

// 读取地址栏 ?verify=xxx 参数，若存在说明是扫码核验电子票的公开入口
const verifyCode = ref(new URLSearchParams(window.location.search).get('verify')?.trim() || '')

let timer: ReturnType<typeof setInterval> | undefined
// 上次触发“订单超时自动刷新”的时间戳，用来做 2 秒节流，防止倒计时归零后每秒狂刷接口
let lastExpiredRefresh = 0

/** 从电子票核验 H5 页返回主站：清掉地址栏的 verify 参数，并按需恢复登录会话 */
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
  // 非扫码核验模式时，应用启动立刻拿本地保存的 JWT 去后端恢复用户信息与剧目列表
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

// 组件卸载时清理全局定时器，避免内存泄漏
onUnmounted(() => timer && clearInterval(timer))
</script>

<template>
  <!-- 公开的微信/相机扫码核验 H5 页面（免登录直接核验真伪） -->
  <!-- Public WeChat / Camera QR Gate Verification H5 Page (Zero Login Required) -->
  <VerifyTicketView v-if="verifyCode" :ticket-code="verifyCode" @exit="exitVerify" />

  <!-- 启动期 JWT 静默恢复守卫：在拿到后端校验结果前展示极简启动屏，防止刷新(F5)时闪现登录页 -->
  <!-- Initial JWT session restoration guard (prevents login screen flash on F5) -->
  <div v-else-if="!state.authReady" class="grid min-h-screen place-items-center bg-paper">
    <p class="text-xs tracking-widest text-ink-mute uppercase">PulseTicket</p>
  </div>

  <!-- 未登录态门卫：展示登录 / 注册页面与全局吐司提示 -->
  <!-- Unauthenticated Gate: Login / Registration -->
  <div v-else-if="!state.user">
    <AuthView />
    <ToastHost />
  </div>

  <!-- 已登录态应用主框架：顶部导航 + 动态切换的业务视图 + 底部页脚 + 全局弹窗/抽屉/AI 助手悬浮窗 -->
  <!-- Authenticated Application Shell -->
  <div v-else class="flex min-h-screen flex-col">
    <AppHeader />

    <main class="mx-auto w-full max-w-[1200px] flex-1 px-6 pb-24 pt-12">
      <!-- 切换标签页时的轻微淡入上移过渡动效 -->
      <Transition
        mode="out-in"
        enter-active-class="transition duration-200 ease-out"
        enter-from-class="opacity-0 translate-y-1"
        leave-active-class="transition duration-100"
        leave-to-class="opacity-0"
      >
        <TheaterView v-if="state.tab === 'theater'" />
        <OrdersView v-else-if="state.tab === 'orders'" />
        <!-- 以下三个后台观测与管理页面严格限制仅 ADMIN 角色可见 -->
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

    <!-- 全局悬浮挂载件：消息提示条、电子票二维码弹窗、站内信抽屉、右下角 AI 票务助手 -->
    <ToastHost />
    <TicketModal />
    <MessagesDrawer />
    <AgentChatWidget />
  </div>
</template>
