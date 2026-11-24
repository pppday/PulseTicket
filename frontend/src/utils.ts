/**
 * 前端通用纯函数工具库（格式化、时间计算、字典翻译、阶梯退票费试算）。
 * 不依赖 Vue 响应状态和网络请求，供全站各页面和弹窗组件直接复用。
 */
import type { ZoneCode } from './types'

/** 将金额格式化为带千分位、最多保留两位小数的中文数字字符串（如 1280.5 -> "1,280.5"） */
export function money(value: number | string | undefined): string {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

/** 安全解析后端存的 JSON 字符串数组（如订单中的 seatLabelsJson），解析失败时兜底包装成单元素数组 */
export function parseList(raw?: string): string[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.map(String) : [String(parsed)]
  } catch {
    return [raw]
  }
}

/**
 * 将类似 ["VIP 内场 A1", "VIP 内场 A2", "标准看台 C3"] 的座位列表按票档分组，
 * 方便在订单卡片、电子票和退票确认框里整齐展示“票档 + 座位号列表”。
 */
export function groupSeatsByZone(raw?: string): { zone: string; seats: string[] }[] {
  const list = parseList(raw)
  const map = new Map<string, string[]>()
  for (const item of list) {
    const parts = item.trim().split(/\s+/)
    // 最后一个词是座位号（如 A1），前面所有词拼在一起作为票档名称
    const zone = parts.length > 1 ? parts.slice(0, -1).join(' ') : ''
    const seat = parts[parts.length - 1]
    if (!map.has(zone)) map.set(zone, [])
    map.get(zone)!.push(seat)
  }
  return [...map.entries()].map(([zone, seats]) => ({ zone, seats }))
}

/** 将后端返回的 "yyyy-MM-dd HH:mm:ss" 转为浏览器兼容的 Date 对象（Safari 等对空格日期不兼容，需替换为 T） */
function toDate(value?: string): Date | null {
  if (!value) return null
  const d = new Date(value.replace(' ', 'T'))
  return isNaN(d.getTime()) ? null : d
}

/** 计算订单距离过期还剩多少秒（最小为 0，配合 App.vue 的全局 1 秒心跳实现实时倒计时） */
export function remainingSeconds(expireAt: string, now: number): number {
  const d = toDate(expireAt)
  return d ? Math.max(0, Math.floor((d.getTime() - now) / 1000)) : 0
}

/** 将剩余秒数格式化为 "MM:SS" 倒计时字符串（如 119 -> "01:59"） */
export function countdown(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** 将完整时间字符串缩短为 "MM-DD HH:mm" 紧凑展示格式 */
export function shortTime(value?: string): string {
  const d = toDate(value)
  if (!d) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 提炼演出简短标题：优先提取书名号/引号内主剧名，例如「星际回响 · PulseLive」2026 … → 星际回响 · PulseLive */
export function shortTitle(title: string): string {
  const bracket = title.match(/[「《](.+?)[」》]/)
  if (bracket) return bracket[1]
  return title.split(' · ')[0]
}

/** 演出分类英文枚举 -> 中文标签映射 */
export const categoryLabel: Record<string, string> = {
  CONCERT: '演唱会',
  MUSICAL: '音乐剧',
  IMAX_MOVIE: 'IMAX 电影',
  LIVEHOUSE: 'Livehouse'
}

/** 无海报图片时的默认剧场风渐变底色池 */
const posterThemes = [
  'linear-gradient(140deg, #2B2926 0%, #4A2C22 100%)',
  'linear-gradient(140deg, #34191F 0%, #6B2C38 100%)',
  'linear-gradient(140deg, #1D2A31 0%, #3A5560 100%)',
  'linear-gradient(140deg, #222E27 0%, #4C6352 100%)'
]

/** 根据 showId 取模分配固定的兜底渐变海报背景，保证同一场演出颜色稳定 */
export function posterTheme(showId: number): string {
  return posterThemes[(showId - 1 + posterThemes.length) % posterThemes.length]
}

/** 三大票档区间的中文名称及对应色点/边框 Tailwind 样式配置 */
export const zones: Record<ZoneCode, { label: string; dot: string; seat: string }> = {
  VIP: { label: 'VIP 内场', dot: 'bg-brick', seat: 'border-brick/70 hover:bg-brick-soft' },
  STAGE_SIDE: { label: '优选看台', dot: 'bg-gold', seat: 'border-gold/70 hover:bg-gold-soft' },
  STANDARD: { label: '标准看台', dot: 'bg-sage', seat: 'border-sage/70 hover:bg-sage-soft' }
}

/** Redis 缓存防击穿/防穿透状态码 -> 中文可读说明（展示在管理员缓存观测条上） */
export const cacheLabel: Record<string, string> = {
  REDIS_HIT: '缓存命中',
  REDIS_HIT_AFTER_WAIT: '等待后命中',
  MYSQL_MUTEX_REBUILT: '互斥锁重建',
  PENETRATION_PROTECTED: '已写入空值',
  REDIS_NULL_GUARD: '空值拦截'
}

/** 订单关闭原因枚举 -> 中文说明 */
export const closeReasonLabel: Record<string, string> = {
  TIMEOUT_UNPAID: '超时未支付，座位已释放',
  USER_CANCEL: '已取消，座位已释放',
  USER_REFUND: '已申请退票退款，座位已释放'
}

/** RabbitMQ 异步消息事件类型 -> 中文说明（用于全链路追踪页） */
export const mqEventLabel: Record<string, string> = {
  DELAY_CLOSE_SENT: '投递延迟关单',
  TIMEOUT_CLOSED: '超时自动关单',
  TIMEOUT_SKIPPED_PAID: '已支付，跳过关单',
  ORDER_PAID_FANOUT: '支付成功广播',
  ORDER_REFUNDED: '退票释放座位'
}

/** 计算当前时间距离演出开演还剩多少小时（用于阶梯退票手续费判断） */
export function hoursUntil(startTime?: string, now = Date.now()): number | null {
  const d = toDate(startTime)
  if (!d) return null
  return (d.getTime() - now) / 3600000
}

/** 退票手续费试算结果结构（用于退票确认弹窗和订单页前置展示） */
export interface RefundPreview {
  known: boolean
  refundable: boolean
  hoursLeft: number | null
  feeRate: string
  tierName: string
  feeAmount: number
  refundAmount: number
}

/**
 * 阶梯退票手续费前端实时试算（与后端 OrderService 退票策略严格对齐）：
 * - 距开演 ≥ 168h（7天）：0% 免费退；
 * - 距开演 48h ~ 168h：收 10% 手续费；
 * - 距开演 24h ~ 48h：收 20% 手续费；
 * - 距开演 < 24h 或已开演：不可退票。
 */
export function calcRefundPreview(
  totalAmount: number | string | undefined,
  startTime?: string,
  now = Date.now()
): RefundPreview {
  const total = Number(totalAmount || 0)
  const h = hoursUntil(startTime, now)
  if (h === null) {
    return {
      known: false,
      refundable: false,
      hoursLeft: null,
      feeRate: '--',
      tierName: '开演时间待确认',
      feeAmount: 0,
      refundAmount: 0
    }
  }
  const roundedHours = Math.round(h * 10) / 10
  // 阶梯 1：距开演 7 天及以上，全额无损退
  if (h >= 168) {
    return {
      known: true,
      refundable: true,
      hoursLeft: roundedHours,
      feeRate: '0%',
      tierName: '无条件免费退票期（≥7天）',
      feeAmount: 0,
      refundAmount: Number(total.toFixed(2))
    }
  }
  // 阶梯 2：距开演 2~7 天，收取 10% 手续费
  if (h >= 48) {
    const fee = Math.round(total * 0.1 * 100) / 100
    return {
      known: true,
      refundable: true,
      hoursLeft: roundedHours,
      feeRate: '10%',
      tierName: '标准退票期（48h~168h）',
      feeAmount: fee,
      refundAmount: Number((total - fee).toFixed(2))
    }
  }
  // 阶梯 3：距开演 24~48 小时，收取 20% 手续费
  if (h >= 24) {
    const fee = Math.round(total * 0.2 * 100) / 100
    return {
      known: true,
      refundable: true,
      hoursLeft: roundedHours,
      feeRate: '20%',
      tierName: '临期退票期（24h~48h）',
      feeAmount: fee,
      refundAmount: Number((total - fee).toFixed(2))
    }
  }
  // 阶梯 4：不足 24 小时或已经开演，禁止退票
  return {
    known: true,
    refundable: false,
    hoursLeft: roundedHours,
    feeRate: '不可退',
    tierName: '距开演不足 24 小时或已开演',
    feeAmount: total,
    refundAmount: 0
  }
}
