import { computed, reactive, ref } from 'vue'
import { api, getToken, readableMessage, setToken, setUnauthorizedHandler } from './api'
import { money } from './utils'
import type {
  AgentTraceItem,
  AgentTraceSummary,
  AgentTracesPayload,
  CatalogItem,
  MqLog,
  OrderDbOverview,
  OrderSalesAnalytics,
  PointsLog,
  SeataAudit,
  SeatItem,
  ShowDbOverview,
  ShowInfo,
  ShowSalesAnalytics,
  ShowSession,
  Tab,
  TicketOrder,
  Toast,
  UserDbOverview,
  UserMessage,
  UserVO
} from './types'

export const state = reactive({
  authReady: false,
  lanIp: '',

  tab: 'theater' as Tab,
  username: '',
  user: null as UserVO | null,
  context: { userId: 0, username: '', role: 'USER', traceId: '' },
  pointsLogs: [] as PointsLog[],
  messages: [] as UserMessage[],

  catalog: [] as CatalogItem[],
  showId: 0,
  sessionId: 0,
  show: null as ShowInfo | null,
  sessions: [] as ShowSession[],
  cache: { status: '', ttl: 0 },
  seats: [] as SeatItem[],
  selected: [] as number[],

  orders: [] as TicketOrder[],
  seataAudits: [] as SeataAudit[],
  mqLogs: [] as MqLog[],

  // ADMIN dedicated Sales & Multi-DB panels
  showAnalytics: null as ShowSalesAnalytics | null,
  orderSales: null as OrderSalesAnalytics | null,
  userDb: null as UserDbOverview | null,
  showDb: null as ShowDbOverview | null,
  orderDb: null as OrderDbOverview | null,
  agentTraceSummary: null as AgentTraceSummary | null,
  agentTraces: [] as AgentTraceItem[],

  ticket: null as TicketOrder | null,
  messagesOpen: false,
  now: Date.now()
})

export const isAdmin = computed(() => state.user?.role === 'ADMIN')

/* ---------------- toast ---------------- */

export const toasts = ref<Toast[]>([])
let toastSeq = 0

export function toast(type: Toast['type'], title: string, detail?: string) {
  const id = ++toastSeq
  toasts.value.push({ id, type, title, detail: detail ? readableMessage(detail) : undefined })
  setTimeout(() => {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }, 4800)
}

/* ---------------- auth & session persistence ---------------- */

function resetUserSession() {
  setToken('')
  state.username = ''
  state.user = null
  state.context = { userId: 0, username: '', role: 'USER', traceId: '' }
  state.pointsLogs = []
  state.messages = []
  state.orders = []
  state.seataAudits = []
  state.mqLogs = []
  state.showAnalytics = null
  state.orderSales = null
  state.userDb = null
  state.showDb = null
  state.orderDb = null
  state.agentTraceSummary = null
  state.agentTraces = []
  state.selected = []
  state.ticket = null
  state.messagesOpen = false
  state.tab = 'theater'
}

setUnauthorizedHandler(() => {
  if (state.user) {
    resetUserSession()
    toast('info', '登录已过期', '请重新登录您的账号')
  }
})

export async function fetchLanIp() {
  const res = await api<{ lanIp: string }>('/api/order/public/lan-ip', {}, '')
  if (res.code === 200 && res.data?.lanIp) {
    state.lanIp = res.data.lanIp
  }
}

export async function restoreSession() {
  fetchLanIp()
  const saved = getToken()
  if (!saved) {
    state.authReady = true
    return
  }
  const ok = await loadProfile()
  if (ok) {
    await loadCatalogAndDefaultShow()
    await loadOrders()
    if (isAdmin.value) {
      await Promise.all([loadTelemetry(), loadAdminSales(), loadAdminDatabases()])
    }
  } else {
    resetUserSession()
  }
  state.authReady = true
}

export async function login(username: string, password: string): Promise<{ ok: boolean; message?: string }> {
  const res = await api(
    '/api/user/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({ username, password })
    },
    ''
  )
  if (res.code !== 200 || !res.data?.accessToken) {
    return { ok: false, message: res.message || '账号或密码不正确' }
  }
  setToken(res.data.accessToken)
  state.user = res.data.user
  state.username = res.data.user.username
  state.selected = []
  state.tab = 'theater'

  await Promise.all([
    loadCatalogAndDefaultShow(),
    loadProfile(),
    loadOrders(),
    res.data.user.role === 'ADMIN'
      ? Promise.all([loadTelemetry(), loadAdminSales(), loadAdminDatabases()])
      : Promise.resolve()
  ])
  toast('success', `欢迎回来，${res.data.user.nickname}`)
  return { ok: true }
}

export async function register(payload: {
  username: string
  password: string
  phone: string
  nickname?: string
}): Promise<{ ok: boolean; message?: string }> {
  const res = await api(
    '/api/user/auth/register',
    {
      method: 'POST',
      body: JSON.stringify(payload)
    },
    ''
  )
  if (res.code !== 200 || !res.data?.accessToken) {
    return { ok: false, message: res.message || '注册失败' }
  }
  setToken(res.data.accessToken)
  state.user = res.data.user
  state.username = res.data.user.username
  state.selected = []
  state.tab = 'theater'

  await Promise.all([loadCatalogAndDefaultShow(), loadProfile(), loadOrders()])
  toast('success', '注册成功，已自动登录', `实名手机 ${res.data.user.phone} · 已发放 100 新人观演积分`)
  return { ok: true }
}

export async function updateUserPhone(phone: string): Promise<{ ok: boolean; message?: string }> {
  const res = await api<{ user: UserVO }>('/api/user/profile/phone', {
    method: 'POST',
    body: JSON.stringify({ phone })
  })
  if (res.code !== 200 || !res.data?.user) {
    return { ok: false, message: res.message || '手机号更新失败' }
  }
  state.user = res.data.user
  if (isAdmin.value) {
    loadAdminDatabases()
  }
  toast('success', '手机号已更新', `当前实名绑定手机：${res.data.user.phone}`)
  return { ok: true }
}

export function logout() {
  resetUserSession()
  toast('info', '已退出登录')
}

export async function loadProfile(): Promise<boolean> {
  const res = await api('/api/user/profile')
  if (res.code !== 200 || !res.data?.user) return false
  state.user = res.data.user
  state.username = res.data.user.username
  state.context = {
    userId: res.data.threadLocalUserId,
    username: res.data.threadLocalUsername,
    role: res.data.threadLocalRole || res.data.user.role || 'USER',
    traceId: res.data.traceId
  }
  state.pointsLogs = res.data.pointsLogs || []
  state.messages = res.data.messages || []
  return true
}

export async function markMessageRead(id: number) {
  const msg = state.messages.find(m => m.id === id)
  if (!msg || msg.isRead) return
  msg.isRead = 1
  await api(`/api/user/messages/${id}/read`, { method: 'POST' })
}

export async function markAllMessagesRead() {
  const unreadList = state.messages.filter(m => !m.isRead)
  for (const m of unreadList) m.isRead = 1
  await Promise.all(unreadList.map(m => api(`/api/user/messages/${m.id}/read`, { method: 'POST' })))
}

/* ---------------- catalog & seats ---------------- */

export async function loadCatalog() {
  const res = await api<CatalogItem[]>(`/api/show/public/list?_t=${Date.now()}`, {}, '')
  if (res.code === 200) state.catalog = res.data || []
}

export async function loadCatalogAndDefaultShow() {
  if (!state.catalog.length) await loadCatalog()
  if (state.catalog.length && !state.showId) {
    await selectShow(state.catalog[0].show.id)
  } else if (state.sessionId) {
    await loadSeats(state.sessionId)
  }
}

export async function selectShow(showId: number) {
  state.showId = showId
  state.selected = []
  const res = await api(`/api/show/public/detail/${showId}`, {}, '')
  if (res.code !== 200 || !res.data?.show) return
  state.show = res.data.show
  state.sessions = res.data.sessions || []
  state.cache = { status: res.data.cacheStatus, ttl: res.data.ttlSeconds }
  if (state.sessions.length) await loadSeats(state.sessions[0].id)
}

export async function loadSeats(sessionId = state.sessionId) {
  if (!sessionId) return
  if (sessionId !== state.sessionId) state.selected = []
  state.sessionId = sessionId
  const res = await api(`/api/show/public/sessions/${sessionId}/seats`, {}, '')
  if (res.code !== 200) return
  state.seats = res.data.seats || []
  const available = new Set(state.seats.filter(s => s.status === 'AVAILABLE').map(s => s.id))
  state.selected = state.selected.filter(id => available.has(id))
}

export async function rebuildCache() {
  const evict = await api(`/api/show/cache/evict/${state.showId}`, { method: 'POST' })
  if (evict.code !== 200) {
    toast('error', '操作受限', evict.message)
    return
  }
  const res = await api(`/api/show/public/detail/${state.showId}`, {}, '')
  if (res.code !== 200) return
  state.cache = { status: res.data.cacheStatus, ttl: res.data.ttlSeconds }
  toast('info', '缓存已重建', `Redisson 互斥锁回源 MySQL，TTL ${res.data.ttlSeconds}s（含随机抖动）`)
}

export async function probePenetration() {
  const first = await api('/api/show/public/detail/99999', {}, '')
  const second = await api('/api/show/public/detail/99999', {}, '')
  toast(
    'info',
    '穿透防护生效',
    `不存在的演出 #99999：${first.data?.cacheStatus} → ${second.data?.cacheStatus}，第二次请求未查询数据库`
  )
}

/* ---------------- orders, sales & databases ---------------- */

export async function loadOrders() {
  if (!state.user) return
  const res = await api<TicketOrder[]>('/api/order/my')
  if (res.code === 200) state.orders = res.data || []
}

export async function loadTelemetry() {
  if (!isAdmin.value) return
  const [orderTel, agentTel] = await Promise.all([
    api('/api/order/telemetry'),
    api<AgentTracesPayload>('/api/agent/traces?limit=20')
  ])
  if (orderTel.code === 200 && orderTel.data) {
    state.seataAudits = orderTel.data.seataAudits || []
    state.mqLogs = orderTel.data.mqLogs || []
  }
  if (agentTel.code === 200 && agentTel.data) {
    state.agentTraceSummary = agentTel.data.summary || null
    state.agentTraces = agentTel.data.traces || []
  }
}

export async function loadAdminSales() {
  if (!isAdmin.value) return
  const [showRes, orderRes, userRes] = await Promise.all([
    api<ShowSalesAnalytics>('/api/show/admin/analytics'),
    api<OrderSalesAnalytics>('/api/order/admin/sales'),
    api<UserDbOverview>('/api/user/admin/db-overview')
  ])
  if (showRes.code === 200 && showRes.data) state.showAnalytics = showRes.data
  if (orderRes.code === 200 && orderRes.data) state.orderSales = orderRes.data
  if (userRes.code === 200 && userRes.data) state.userDb = userRes.data
}

export async function loadAdminDatabases() {
  if (!isAdmin.value) return
  const [userRes, showRes, orderRes] = await Promise.all([
    api<UserDbOverview>('/api/user/admin/db-overview'),
    api<ShowDbOverview>('/api/show/admin/db-overview'),
    api<OrderDbOverview>('/api/order/admin/db-overview')
  ])
  if (userRes.code === 200 && userRes.data) state.userDb = userRes.data
  if (showRes.code === 200 && showRes.data) state.showDb = showRes.data
  if (orderRes.code === 200 && orderRes.data) state.orderDb = orderRes.data
}

export async function refreshAll() {
  await loadCatalog()
  if (!state.user) return
  await Promise.all([
    loadSeats(),
    loadOrders(),
    loadProfile(),
    isAdmin.value
      ? Promise.all([loadTelemetry(), loadAdminSales(), loadAdminDatabases()])
      : Promise.resolve()
  ])
}

export async function payOrder(order: TicketOrder) {
  const res = await api<TicketOrder>(`/api/order/${order.orderNo}/pay`, { method: 'POST' })
  if (res.code !== 200) {
    toast('error', '支付失败', res.message)
    await loadOrders()
    return
  }
  toast('success', '支付成功，已出票', `取票码 ${res.data.ticketCode}，积分与通知稍后到账`)
  state.ticket = res.data
  // 积分与出票由 MQ 异步消费，稍等片刻再刷新
  setTimeout(refreshAll, 800)
}

export async function cancelOrder(order: TicketOrder) {
  const res = await api(`/api/order/${order.orderNo}/cancel`, { method: 'POST' })
  if (res.code !== 200) {
    toast('error', '取消失败', res.message)
    return
  }
  toast('info', '订单已取消', '锁定的座位已释放')
  await refreshAll()
}

export async function refundOrder(order: TicketOrder) {
  const res = await api<{
    refundAmount?: number
    feeAmount?: number
    feeRate?: string
    tierName?: string
  }>(`/api/order/${order.orderNo}/refund`, { method: 'POST' })
  if (res.code !== 200) {
    toast('error', '退票失败', res.message)
    await loadOrders()
    return
  }
  const d = res.data || {}
  toast(
    'success',
    '退票成功，座位已释放',
    `实退 ¥${money(d.refundAmount ?? order.totalAmount)}（手续费 ¥${money(d.feeAmount ?? 0)} / ${d.feeRate ?? '0%'}）`
  )
  await refreshAll()
}
