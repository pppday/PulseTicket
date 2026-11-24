/**
 * 全站前后端契约 TypeScript 类型定义总表。
 * 统一约束用户、演出/场次/座位库存、票务订单、Seata/MQ 遥测、管理员多库快照、
 * 以及 AI 多智能体对话（含 SSE 思维链步骤、工具卡片、人工确认卡片与会话线程）的数据结构。
 */

/** 顶部主导航标签页枚举 */
export type Tab = 'theater' | 'orders' | 'sales' | 'database' | 'telemetry'
/** 演出票档分区编码：VIP 内场 / 优选看台 / 标准看台 */
export type ZoneCode = 'VIP' | 'STAGE_SIDE' | 'STANDARD'
/** 座位库存状态：可选 / 下单锁定中 / 已售出 */
export type SeatStatus = 'AVAILABLE' | 'LOCKED' | 'SOLD'
/** 票务订单状态：待支付 / 已支付 / 已关闭（超时、取消或退票） */
export type OrderStatus = 'UNPAID' | 'PAID' | 'CLOSED'
/** 用户权限角色：普通观众 / 系统管理员 */
export type UserRole = 'USER' | 'ADMIN'

/** 后端统一响应包装结构（对应 Java 端 Result<T>） */
export interface ApiResult<T = any> {
  code: number
  message: string
  data: T
  traceId?: string
}

/** 登录用户展示对象（脱敏，不含密码哈希） */
export interface UserVO {
  id: number
  username: string
  nickname: string
  phone: string
  vipLevel: string
  role?: UserRole
  pointsBalance: number
  totalOrders: number
}

/** 演出基本信息（对应 show 库的 t_show 表） */
export interface ShowInfo {
  id: number
  showCode?: string
  title: string
  subtitle: string
  category: string
  city: string
  venueName: string
  posterGradient?: string
  posterUrl?: string | null
  badgeText: string
  rating: number
  minPrice: number
  maxPrice: number
  tags?: string
  hotScore?: number
  status?: string
  createdAt?: string
  description: string
}

/** 对象存储（OSS/MinIO）中的海报文件条目 */
export interface OssFileItem {
  objectKey: string
  url: string
  size: number
  lastModified: string
}

/** 对象存储桶信息及已上传海报文件列表 */
export interface OssBucketInfo {
  bucket: string
  endpoint: string
  domain: string
  region: string
  files: OssFileItem[]
}

/** 管理员新增演出时的场馆预设选项 */
export interface VenueOptionItem {
  city: string
  venueName: string
  hallName: string
  categoryHint?: string
}

/** 管理员新增演出表单的城市/场馆/演播厅/角标下拉配置 */
export interface AdminShowOptions {
  cities: string[]
  venues: VenueOptionItem[]
  halls: string[]
  badges: string[]
}

/** 演出具体场次（对应 show 库的 t_show_session 表，含实时余票统计与乐观锁版本号） */
export interface ShowSession {
  id: number
  showId: number
  sessionCode?: string
  sessionTitle: string
  hallName: string
  startTime: string
  endTime?: string
  totalSeats?: number
  availableSeats?: number
  lockedSeats?: number
  soldSeats?: number
  version?: number
}

/** 首页目录项：一个演出及其名下所有场次 */
export interface CatalogItem {
  show: ShowInfo
  sessions: ShowSession[]
}

/** 单个座位库存实体（对应 show 库的 t_seat_inventory 表，支持 CAS 乐观锁防超卖） */
export interface SeatItem {
  id: number
  sessionId: number
  showId?: number
  rowNo: number
  colNo: number
  seatLabel: string
  zoneCode: ZoneCode
  zoneName: string
  price: number
  status: SeatStatus
  lockedOrderNo?: string | null
  lockedUserId?: number | null
  lockedAt?: string | null
  soldAt?: string | null
  version?: number
}

/** 票务订单实体（对应 order 库的 t_ticket_order 表，含 Seata 全局事务 XID 与过期关单时间） */
export interface TicketOrder {
  id: number
  orderNo: string
  userId?: number
  username?: string
  showId: number
  showTitle: string
  venueName: string
  sessionId?: number
  sessionTitle: string
  startTime?: string
  seatIdsJson?: string
  seatLabelsJson: string
  seatCount: number
  totalAmount: number
  status: OrderStatus
  closeReason?: string | null
  delaySeconds?: number
  ticketCode?: string | null
  seataXid?: string | null
  expireAt: string
  paidAt?: string | null
  closedAt?: string | null
  version?: number
  createdAt: string
}

/** 电子票扫码核验返回结果（公开接口返回，隐藏买票人隐私信息） */
export interface VerifiedTicket {
  valid: boolean
  status: OrderStatus
  ticketCode: string
  orderNo: string
  showId: number
  showTitle: string
  venueName: string
  sessionTitle: string
  seatLabelsJson: string
  seatCount: number
  totalAmount: number
  paidAt?: string | null
  verifiedAt: string
}

/** Seata AT 分布式事务审计记录（用于观测跨库锁座下单是否成功提交或触发故障注入回滚） */
export interface SeataAudit {
  id: number
  xid: string
  orderNo: string
  userId?: number
  sessionId?: number
  seatIds: string
  txStatus: 'COMMITTED' | 'ROLLED_BACK' | string
  faultInjected?: number
  undoSummary: string
  latencyMs: number
  createdAt: string
}

/** RabbitMQ 异步消息事件日志（涵盖延迟死信关单、支付成功广播、退票释放座位等） */
export interface MqLog {
  id: number
  messageId: string
  exchangeName: string
  routingKey: string
  orderNo: string
  eventType: string
  payloadSummary: string
  status: string
  createdAt: string
}

/** 用户积分变动流水（支付成功由 MQ 消费者异步加分） */
export interface PointsLog {
  id: number
  userId?: number
  orderNo: string
  bizType?: string
  pointsDelta: number
  balanceAfter: number
  remark: string
  createdAt: string
}

/** 用户站内信通知（出票成功、退票提醒等） */
export interface UserMessage {
  id: number
  userId?: number
  orderNo: string
  msgType?: string
  title: string
  content: string
  isRead: number
  createdAt: string
}

/** 右上角轻提示条数据结构 */
export interface Toast {
  id: number
  type: 'success' | 'error' | 'info'
  title: string
  detail?: string
}

/* ---------------- 管理员票房销售报表与多库总览类型 ---------------- */
/* ---------------- ADMIN Sales & Multi-Database Types ---------------- */

/** 演出票房汇总指标（实收票房、锁座待收、总上座率等） */
export interface ShowSalesSummary {
  totalShows: number
  totalSessions: number
  totalSeats: number
  soldSeats: number
  lockedSeats: number
  availableSeats: number
  occupancyRate: number
  soldBoxOffice: number
  lockedAmount: number
  potentialBoxOffice: number
}

/** 单个演出的票房排行项 */
export interface ShowRankingItem {
  showId: number
  showCode: string
  title: string
  category: string
  city: string
  venueName: string
  rating: number
  hotScore: number
  sessionCount: number
  totalSeats: number
  soldSeats: number
  lockedSeats: number
  availableSeats: number
  occupancyRate: number
  soldBoxOffice: number
  lockedAmount: number
  potentialBoxOffice: number
}

/** 单个场次的售卖进度统计 */
export interface SessionSalesItem {
  sessionId: number
  sessionCode: string
  showId: number
  showTitle: string
  city: string
  sessionTitle: string
  hallName: string
  startTime: string
  totalSeats: number
  soldSeats: number
  lockedSeats: number
  availableSeats: number
  occupancyRate: number
  soldBoxOffice: number
  potentialBoxOffice: number
  version: number
}

/** 按票档分区（VIP/优选/标准）汇总的售卖情况 */
export interface ZoneSalesItem {
  zoneCode: ZoneCode
  zoneName: string
  totalSeats: number
  soldSeats: number
  lockedSeats: number
  availableSeats: number
  sellThroughRate: number
  soldRevenue: number
  potentialRevenue: number
}

/** 演出服务返回的完整票房分析载荷 */
export interface ShowSalesAnalytics {
  summary: ShowSalesSummary
  showRanking: ShowRankingItem[]
  sessionStats: SessionSalesItem[]
  zoneStats: ZoneSalesItem[]
}

/** 订单维度的成交汇总（转化率、客单价、超时关单/主动取消笔数） */
export interface OrderSalesSummary {
  totalOrders: number
  paidOrders: number
  paidTickets: number
  paidRevenue: number
  avgOrderValue: number
  unpaidOrders: number
  unpaidAmount: number
  closedOrders: number
  closedAmount: number
  timeoutClosedOrders: number
  userCancelledOrders: number
  conversionRate: number
}

/** 按日汇总的票房营收趋势点 */
export interface DailySalesItem {
  date: string
  paidRevenue: number
  paidOrders: number
  paidTickets: number
  totalOrders: number
}

/** 高价值购票用户消费排行榜项 */
export interface BuyerLeaderboardItem {
  userId: number
  username: string
  paidOrders: number
  paidTickets: number
  totalSpent: number
  lastPaidAt?: string | null
}

/** 订单服务返回的完整交易分析载荷 */
export interface OrderSalesAnalytics {
  summary: OrderSalesSummary
  dailyTrend: DailySalesItem[]
  buyerLeaderboard: BuyerLeaderboardItem[]
  recentOrders: TicketOrder[]
}

/** 数据库单表元信息（表名、注释、引擎、估算行数、占用字节） */
export interface DbTableMeta {
  tableName: string
  tableComment: string
  engine: string
  rowCount: number
  totalBytes: number
}

/** Seata AT 模式的 undo_log 回滚日志行（正常提交后会被清理，残留可用于诊断） */
export interface UndoLogRow {
  branchId: number
  xid: string
  context: string
  logStatus: number
  payloadBytes: number
  logCreated: string
}

/** 用户库管理员视图中的账号行数据（含密码加密算法标识） */
export interface UserAccountRow {
  id: number
  username: string
  nickname: string
  phone: string
  vipLevel: string
  role: UserRole
  pointsBalance: number
  totalOrders: number
  passwordAlgo: string
  createdAt?: string | null
  updatedAt?: string | null
}

/** Redis 中演出详情缓存 Key 的实时体检信息（含空值哨兵防穿透状态与剩余 TTL） */
export interface RedisCacheKeyInfo {
  key: string
  showId: number
  exists: boolean
  ttlSeconds: number
  cacheType: 'DATA_JSON' | 'NULL_SENTINEL' | 'MISS'
  payloadBytes: number
}

/** 用户微服务独立库（pulseticket_user）总览快照 */
export interface UserDbOverview {
  database: string
  service: string
  tablesMeta: DbTableMeta[]
  userAccounts: UserAccountRow[]
  pointsLogs: PointsLog[]
  userMessages: UserMessage[]
  undoLogs: UndoLogRow[]
}

/** 演出微服务独立库（pulseticket_show）与 Redis 缓存总览快照 */
export interface ShowDbOverview {
  database: string
  service: string
  tablesMeta: DbTableMeta[]
  redisCacheKeys: RedisCacheKeyInfo[]
  shows: ShowInfo[]
  sessions: ShowSession[]
  seatInventory: SeatItem[]
  undoLogs: UndoLogRow[]
}

/** 订单微服务独立库（pulseticket_order）总览快照 */
export interface OrderDbOverview {
  database: string
  service: string
  tablesMeta: DbTableMeta[]
  ticketOrders: TicketOrder[]
  seataTxAudits: SeataAudit[]
  mqEventLogs: MqLog[]
  undoLogs: UndoLogRow[]
}

/* ---------------- AI 多智能体对话与链路可观测性类型 ---------------- */
/* ---------------- AI Multi-Agent Chat & Observability Types ---------------- */

/** AI 对话模式：agent（多智能体路由+工具调用+人工确认）/ chat（纯问答直聊模式） */
export type AgentChatMode = 'agent' | 'chat'

/** Agent 轨迹中的单个工具调用记录 */
export interface AgentToolStep {
  agent?: string
  tool: string
  args?: Record<string, any>
}

/** 聊天框折叠面板里展示的“深度思考/执行轨迹”单步节点 */
export interface AgentThoughtStep {
  key: string
  phase: string
  agentLabel: string
  title: string
  detail: string
  badges?: string[]
  status: 'running' | 'done' | 'warn'
  atMs: number
}

/** 工具执行卡片（记录调用参数、返回摘要以及展开/折叠状态） */
export interface AgentToolCard {
  id: string
  toolName: string
  title: string
  status: 'running' | 'done' | 'error'
  summary?: string
  input?: any
  output?: any
  atMs?: number
  expanded?: boolean
}

/** 交互式选择卡片（选演出、选座位、选订单）里的单个可点击选项 */
export interface AgentWidgetOption {
  id: string
  label: string
  badge?: string
  meta?: string
  price?: number
  prompt: string
}

/** AI 主动下发的快捷交互卡片（用户点按钮即可直接触发下一轮精准指令，免手打） */
export interface AgentInteractiveWidget {
  id: string
  widgetType: 'SHOW_PICKER' | 'SEAT_PICKER' | 'ORDER_PICKER'
  title: string
  subtitle?: string
  options: AgentWidgetOption[]
  selectedOptionId?: string
  selectedLabel?: string
}

/**
 * LangGraph interrupt 人工确认卡片（高危写操作必经卡点）：
 * 当 AI 准备下单锁座、取消订单或退票退款时，图执行会在后端挂起并下发此卡片，
 * 必须由用户明确点“确认执行”携带 idempotencyKey 恢复图执行，防止 AI 误操作。
 */
export interface AgentConfirmationCard {
  actionType: 'CREATE_ORDER' | 'CANCEL_ORDER' | 'REFUND_ORDER'
  toolName: string
  toolCallId: string
  idempotencyKey: string
  title: string
  isMock?: boolean
  priceSource?: string
  args: {
    session_id?: number
    seat_ids?: number[]
    seat_labels?: string[]
    show_title?: string
    session_title?: string
    venue_name?: string
    total_amount?: number
    order_no?: string
    refund_reason?: string
    fee_rate?: string
    fee_amount?: number
    refund_amount?: number
    tier_name?: string
    hours_before_start?: number
  }
}

/** 对话窗口中的单条消息实体（同时承载流式输出状态、思考轨迹、工具卡片、交互卡片和中断确认卡） */
export interface AgentChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  mode?: AgentChatMode
  streaming?: boolean
  aborted?: boolean
  liveStepLabel?: string
  status?: 'COMPLETED' | 'INTERRUPTED' | 'BLOCKED' | 'ERROR'
  intents?: string[]
  subgraphs?: string[]
  citations?: string[]
  toolSteps?: AgentToolStep[]
  thinkingSteps?: AgentThoughtStep[]
  toolCards?: AgentToolCard[]
  widgets?: AgentInteractiveWidget[]
  thinkingExpanded?: boolean
  userToggledThinking?: boolean
  confirmationCard?: AgentConfirmationCard | null
  cardDecision?: 'PENDING' | 'SUBMITTING' | 'CONFIRMED' | 'REJECTED' | 'EXPIRED'
  feedback?: 'up' | 'down' | null
  copied?: boolean
  latencyMs?: number
  createdAt?: string
}

/** 左侧历史会话列表中的单条线程摘要（对应后端 LangGraph thread_id） */
export interface AgentThreadSummary {
  threadId: string
  title: string
  lastPreview: string
  status: 'ACTIVE' | 'INTERRUPTED'
  hasPendingCard: boolean
  messageCount: number
  createdAt: string
  updatedAt: string
  expiresAt: string
}

/** 历史会话列表接口返回载荷（含会话 TTL 保存天数） */
export interface AgentThreadsListPayload {
  threads: AgentThreadSummary[]
  ttlDays: number
}

/** 单个历史会话详情载荷（点开历史对话时恢复全部消息以及尚未处理的挂起确认卡片） */
export interface AgentThreadDetailPayload {
  threadId: string
  title: string
  status: 'ACTIVE' | 'INTERRUPTED'
  pendingCard: AgentConfirmationCard | null
  pendingCardExpired: boolean
  expiresAt: string
  updatedAt: string
  messages: AgentChatMessage[]
}

/** 管理员观测页中的单条 AI 多智能体调用追踪记录 */
export interface AgentTraceItem {
  id: number
  traceId: string
  threadId: string
  userId: number
  username: string
  endpoint: string
  userMessage: string
  intents: string[]
  subgraphs: string[]
  toolSteps: AgentToolStep[]
  citations: string[]
  guardBlocked: boolean
  interrupted: boolean
  writeExecuted: boolean
  latencyMs: number
  status: string
  replyPreview: string
  createdAt: string
}

/** AI 调用追踪汇总 KPI（总次数、安全拦截数、人工确认中断数、写操作落地数、平均耗时） */
export interface AgentTraceSummary {
  totalRuns: number
  guardBlockedCount: number
  interruptedCount: number
  writeExecutedCount: number
  avgLatencyMs: number
}

/** /api/agent/traces 接口返回载荷 */
export interface AgentTracesPayload {
  summary: AgentTraceSummary
  traces: AgentTraceItem[]
}
