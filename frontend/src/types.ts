export type Tab = 'theater' | 'orders' | 'sales' | 'database' | 'telemetry'
export type ZoneCode = 'VIP' | 'STAGE_SIDE' | 'STANDARD'
export type SeatStatus = 'AVAILABLE' | 'LOCKED' | 'SOLD'
export type OrderStatus = 'UNPAID' | 'PAID' | 'CLOSED'
export type UserRole = 'USER' | 'ADMIN'

export interface ApiResult<T = any> {
  code: number
  message: string
  data: T
  traceId?: string
}

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

export interface OssFileItem {
  objectKey: string
  url: string
  size: number
  lastModified: string
}

export interface OssBucketInfo {
  bucket: string
  endpoint: string
  domain: string
  region: string
  files: OssFileItem[]
}

export interface VenueOptionItem {
  city: string
  venueName: string
  hallName: string
  categoryHint?: string
}

export interface AdminShowOptions {
  cities: string[]
  venues: VenueOptionItem[]
  halls: string[]
  badges: string[]
}

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

export interface CatalogItem {
  show: ShowInfo
  sessions: ShowSession[]
}

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

export interface Toast {
  id: number
  type: 'success' | 'error' | 'info'
  title: string
  detail?: string
}

/* ---------------- ADMIN Sales & Multi-Database Types ---------------- */

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

export interface ShowSalesAnalytics {
  summary: ShowSalesSummary
  showRanking: ShowRankingItem[]
  sessionStats: SessionSalesItem[]
  zoneStats: ZoneSalesItem[]
}

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

export interface DailySalesItem {
  date: string
  paidRevenue: number
  paidOrders: number
  paidTickets: number
  totalOrders: number
}

export interface BuyerLeaderboardItem {
  userId: number
  username: string
  paidOrders: number
  paidTickets: number
  totalSpent: number
  lastPaidAt?: string | null
}

export interface OrderSalesAnalytics {
  summary: OrderSalesSummary
  dailyTrend: DailySalesItem[]
  buyerLeaderboard: BuyerLeaderboardItem[]
  recentOrders: TicketOrder[]
}

export interface DbTableMeta {
  tableName: string
  tableComment: string
  engine: string
  rowCount: number
  totalBytes: number
}

export interface UndoLogRow {
  branchId: number
  xid: string
  context: string
  logStatus: number
  payloadBytes: number
  logCreated: string
}

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

export interface RedisCacheKeyInfo {
  key: string
  showId: number
  exists: boolean
  ttlSeconds: number
  cacheType: 'DATA_JSON' | 'NULL_SENTINEL' | 'MISS'
  payloadBytes: number
}

export interface UserDbOverview {
  database: string
  service: string
  tablesMeta: DbTableMeta[]
  userAccounts: UserAccountRow[]
  pointsLogs: PointsLog[]
  userMessages: UserMessage[]
  undoLogs: UndoLogRow[]
}

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

export interface OrderDbOverview {
  database: string
  service: string
  tablesMeta: DbTableMeta[]
  ticketOrders: TicketOrder[]
  seataTxAudits: SeataAudit[]
  mqEventLogs: MqLog[]
  undoLogs: UndoLogRow[]
}

/* ---------------- AI Multi-Agent Chat & Observability Types ---------------- */

export type AgentChatMode = 'agent' | 'chat'

export interface AgentToolStep {
  agent?: string
  tool: string
  args?: Record<string, any>
}

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

export interface AgentWidgetOption {
  id: string
  label: string
  badge?: string
  meta?: string
  price?: number
  prompt: string
}

export interface AgentInteractiveWidget {
  id: string
  widgetType: 'SHOW_PICKER' | 'SEAT_PICKER' | 'ORDER_PICKER'
  title: string
  subtitle?: string
  options: AgentWidgetOption[]
  selectedOptionId?: string
  selectedLabel?: string
}

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

export interface AgentThreadsListPayload {
  threads: AgentThreadSummary[]
  ttlDays: number
}

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

export interface AgentTraceSummary {
  totalRuns: number
  guardBlockedCount: number
  interruptedCount: number
  writeExecutedCount: number
  avgLatencyMs: number
}

export interface AgentTracesPayload {
  summary: AgentTraceSummary
  traces: AgentTraceItem[]
}
