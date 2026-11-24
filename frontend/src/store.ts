/**
 * 前端全局响应式状态中心（轻量版 Pinia / Vuex）。
 * 基于 Vue 3 reactive() 集中管理全站共享状态，并提供所有核心业务动作（Action）：
 * 1) 登录态与会话持久化：JWT 恢复、登录/注册/退出、401 自动踢下线、更新实名手机号；
 * 2) 演出与选座数据：加载剧目列表、切换演出/场次、拉取座位图、Redis 缓存重建与防穿透探测；
 * 3) 订单与异步协同：支付出票（含 800ms 延迟刷新等待 MQ 异步发积分与通知）、取消订单、退票退款；
 * 4) 管理员视角数据：票房销售分析、三库（用户/剧目/订单）实时总览、Seata/MQ/AI Agent 全链路遥测。
 */
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

/** 全站唯一响应式状态树，任何组件直接 import { state } 即可响应更新 */
export const state = reactive({
  // 是否已完成首屏 JWT 校验（为 false 时 App.vue 显示加载守卫，防止 F5 闪烁登录框）
  authReady: false,
  // 后端所在的局域网 IP（用于生成手机微信扫码可访问的电子票核验二维码链接）
  lanIp: '',

  // 导航与当前登录用户信息
  tab: 'theater' as Tab,
  username: '',
  user: null as UserVO | null,
  // 后端网关透传到微服务 ThreadLocal 的身份上下文与全链路 traceId（便于管理员观测）
  context: { userId: 0, username: '', role: 'USER', traceId: '' },
  pointsLogs: [] as PointsLog[],
  messages: [] as UserMessage[],

  // 剧目大厅与选座页状态
  catalog: [] as CatalogItem[],
  showId: 0,
  sessionId: 0,
  show: null as ShowInfo | null,
  sessions: [] as ShowSession[],
  // 当前演出详情接口的 Redis 缓存命中状态及剩余 TTL（用于演示防击穿/防雪崩）
  cache: { status: '', ttl: 0 },
  seats: [] as SeatItem[],
  // 用户在座位图上勾选中的座位 ID 列表（最多 4 座）
  selected: [] as number[],

  // 订单与分布式事务/消息队列观测数据
  orders: [] as TicketOrder[],
  seataAudits: [] as SeataAudit[],
  mqLogs: [] as MqLog[],

  // 管理员专属的票房报表、三微服务独立数据库快照以及 AI 多智能体调用轨迹
  // ADMIN dedicated Sales & Multi-DB panels
  showAnalytics: null as ShowSalesAnalytics | null,
  orderSales: null as OrderSalesAnalytics | null,
  userDb: null as UserDbOverview | null,
  showDb: null as ShowDbOverview | null,
  orderDb: null as OrderDbOverview | null,
  agentTraceSummary: null as AgentTraceSummary | null,
  agentTraces: [] as AgentTraceItem[],

  // 全局弹窗/抽屉开关与当前时间戳（每秒由 App.vue 更新，驱动倒计时）
  ticket: null as TicketOrder | null,
  messagesOpen: false,
  now: Date.now()
})

/** 当前登录用户是否为管理员（控制导航栏、管理面板、缓存实验区及压测工具的可见性） */
export const isAdmin = computed(() => state.user?.role === 'ADMIN')

/* ---------------- 全局 Toast 轻提示队列 ---------------- */

export const toasts = ref<Toast[]>([])
let toastSeq = 0

/** 弹出一条右上角轻提示，4.8 秒后自动消失；detail 会自动清洗 Feign 冗长报错 */
export function toast(type: Toast['type'], title: string, detail?: string) {
  const id = ++toastSeq
  toasts.value.push({ id, type, title, detail: detail ? readableMessage(detail) : undefined })
  setTimeout(() => {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }, 4800)
}

/* ---------------- 鉴权与会话持久化 ---------------- */

/** 彻底清空用户内存状态与本地 Token，并切回默认首页标签（登出或 401 失效时调用） */
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

// 向 api.ts 注册 401 回调：一旦任意受保护接口返回 Token 过期，立即踢回登录页并友好提示
setUnauthorizedHandler(() => {
  if (state.user) {
    resetUserSession()
    toast('info', '登录已过期', '请重新登录您的账号')
  }
})

/** 拉取后端机器的局域网 IP，确保本地演示时用手机扫二维码能直接连通电脑上的核验页面 */
export async function fetchLanIp() {
  const res = await api<{ lanIp: string }>('/api/order/public/lan-ip', {}, '')
  if (res.code === 200 && res.data?.lanIp) {
    state.lanIp = res.data.lanIp
  }
}

/**
 * 页面首次加载 / 刷新时尝试恢复登录态：
 * 如果本地存有 JWT，先请求 /api/user/profile 验明正身；有效则拉取演出、订单（以及管理员报表），无效则清理失效 Token。
 */
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

/** 账号密码登录：拿到 JWT 后写入缓存，并并行预加载演出列表、个人信息、订单及管理员大盘 */
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

/** 新用户实名注册：注册成功后后端直接签发 JWT 并赠送 100 新人积分，前端自动完成登录 */
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

/** 修改当前用户的实名绑定手机号（用于短信通知和入场核验），并同步更新管理员数据库视图 */
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

/** 主动退出登录 */
export function logout() {
  resetUserSession()
  toast('info', '已退出登录')
}

/** 拉取个人资料、网关注入的 ThreadLocal 上下文、积分流水和站内消息列表 */
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

/** 标记单条站内消息为已读（采用乐观更新：先改前端红点状态再发请求，交互零延迟） */
export async function markMessageRead(id: number) {
  const msg = state.messages.find(m => m.id === id)
  if (!msg || msg.isRead) return
  msg.isRead = 1
  await api(`/api/user/messages/${id}/read`, { method: 'POST' })
}

/** 一键将所有未读站内消息标为已读 */
export async function markAllMessagesRead() {
  const unreadList = state.messages.filter(m => !m.isRead)
  for (const m of unreadList) m.isRead = 1
  await Promise.all(unreadList.map(m => api(`/api/user/messages/${m.id}/read`, { method: 'POST' })))
}

/* ---------------- 演出目录、场次座位与缓存实验 ---------------- */

/** 拉取全站上架的剧目与场次目录（带时间戳参数防止浏览器或代理缓存旧列表） */
export async function loadCatalog() {
  const res = await api<CatalogItem[]>(`/api/show/public/list?_t=${Date.now()}`, {}, '')
  if (res.code === 200) state.catalog = res.data || []
}

/** 初始化演出目录并默认选中第一部演出；如果已选中过某场次则仅刷新当前场次座位图 */
export async function loadCatalogAndDefaultShow() {
  if (!state.catalog.length) await loadCatalog()
  if (state.catalog.length && !state.showId) {
    await selectShow(state.catalog[0].show.id)
  } else if (state.sessionId) {
    await loadSeats(state.sessionId)
  }
}

/** 切换当前正在浏览的演出：清空已选座位、请求详情（附带 Redis 缓存状态）并默认加载首个场次座位 */
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

/** 加载指定场次的实时座位库存，并自动剔除用户刚才勾选但已被别人抢走的座位 */
export async function loadSeats(sessionId = state.sessionId) {
  if (!sessionId) return
  if (sessionId !== state.sessionId) state.selected = []
  state.sessionId = sessionId
  const res = await api(`/api/show/public/sessions/${sessionId}/seats`, {}, '')
  if (res.code !== 200) return
  state.seats = res.data.seats || []
  // 过滤掉状态已经不是 AVAILABLE 的已选座位，防止前端提交过期选择
  const available = new Set(state.seats.filter(s => s.status === 'AVAILABLE').map(s => s.id))
  state.selected = state.selected.filter(id => available.has(id))
}

/** 管理员缓存实验：主动淘汰当前演出的 Redis 缓存，再立刻回源查询触发 Redisson 互斥锁重建 */
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

/** 管理员缓存实验：连续两次查询不存在的演出 #99999，验证“第一次查库写空值 -> 第二次 Redis 空值拦截”的防穿透逻辑 */
export async function probePenetration() {
  const first = await api('/api/show/public/detail/99999', {}, '')
  const second = await api('/api/show/public/detail/99999', {}, '')
  toast(
    'info',
    '穿透防护生效',
    `不存在的演出 #99999：${first.data?.cacheStatus} → ${second.data?.cacheStatus}，第二次请求未查询数据库`
  )
}

/* ---------------- 订单、票房报表与多库观测 ---------------- */

/** 拉取当前登录用户的订单列表 */
export async function loadOrders() {
  if (!state.user) return
  const res = await api<TicketOrder[]>('/api/order/my')
  if (res.code === 200) state.orders = res.data || []
}

/** 管理员专用：并行拉取 Seata 分布式事务审计、RabbitMQ 消息日志和 AI Agent 轨迹观测数据 */
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

/** 管理员专用：并行拉取剧目票房分析、订单成交趋势和用户库概览（支撑“票房销售”仪表盘） */
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

/** 管理员专用：分别从用户服务、演出服务、订单服务拉取各自独立 MySQL 库的表结构、行数据、undo_log 与 Redis Key 快照 */
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

/** 全局一键同步：下单、支付、退票、超时关单或 AI 操作完成后调用，把座位图、订单、积分、管理员面板全部刷新成最新状态 */
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

/** 支付待付款订单：支付成功后立刻弹出电子票二维码，并延时 800ms 刷新全局状态以等待 RabbitMQ 异步加积分与发站内信落库 */
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

/** 主动取消未支付订单：立即触发后端释放已锁定座位，并刷新全站视图 */
export async function cancelOrder(order: TicketOrder) {
  const res = await api(`/api/order/${order.orderNo}/cancel`, { method: 'POST' })
  if (res.code !== 200) {
    toast('error', '取消失败', res.message)
    return
  }
  toast('info', '订单已取消', '锁定的座位已释放')
  await refreshAll()
}

/** 对已支付订单申请退票退款：后端按开演前时间阶梯扣手续费并释放座位回池，成功后展示实退金额并全局刷新 */
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
