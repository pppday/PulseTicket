import type { ZoneCode } from './types'

export function money(value: number | string | undefined): string {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function parseList(raw?: string): string[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.map(String) : [String(parsed)]
  } catch {
    return [raw]
  }
}

export function groupSeatsByZone(raw?: string): { zone: string; seats: string[] }[] {
  const list = parseList(raw)
  const map = new Map<string, string[]>()
  for (const item of list) {
    const parts = item.trim().split(/\s+/)
    const zone = parts.length > 1 ? parts.slice(0, -1).join(' ') : ''
    const seat = parts[parts.length - 1]
    if (!map.has(zone)) map.set(zone, [])
    map.get(zone)!.push(seat)
  }
  return [...map.entries()].map(([zone, seats]) => ({ zone, seats }))
}

function toDate(value?: string): Date | null {
  if (!value) return null
  const d = new Date(value.replace(' ', 'T'))
  return isNaN(d.getTime()) ? null : d
}

export function remainingSeconds(expireAt: string, now: number): number {
  const d = toDate(expireAt)
  return d ? Math.max(0, Math.floor((d.getTime() - now) / 1000)) : 0
}

export function countdown(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export function shortTime(value?: string): string {
  const d = toDate(value)
  if (!d) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 「星际回响 · PulseLive」2026 … → 星际回响 · PulseLive */
export function shortTitle(title: string): string {
  const bracket = title.match(/[「《](.+?)[」》]/)
  if (bracket) return bracket[1]
  return title.split(' · ')[0]
}

export const categoryLabel: Record<string, string> = {
  CONCERT: '演唱会',
  MUSICAL: '音乐剧',
  IMAX_MOVIE: 'IMAX 电影',
  LIVEHOUSE: 'Livehouse'
}

const posterThemes = [
  'linear-gradient(140deg, #2B2926 0%, #4A2C22 100%)',
  'linear-gradient(140deg, #34191F 0%, #6B2C38 100%)',
  'linear-gradient(140deg, #1D2A31 0%, #3A5560 100%)',
  'linear-gradient(140deg, #222E27 0%, #4C6352 100%)'
]

export function posterTheme(showId: number): string {
  return posterThemes[(showId - 1 + posterThemes.length) % posterThemes.length]
}

export const zones: Record<ZoneCode, { label: string; dot: string; seat: string }> = {
  VIP: { label: 'VIP 内场', dot: 'bg-brick', seat: 'border-brick/70 hover:bg-brick-soft' },
  STAGE_SIDE: { label: '优选看台', dot: 'bg-gold', seat: 'border-gold/70 hover:bg-gold-soft' },
  STANDARD: { label: '标准看台', dot: 'bg-sage', seat: 'border-sage/70 hover:bg-sage-soft' }
}

export const cacheLabel: Record<string, string> = {
  REDIS_HIT: '缓存命中',
  REDIS_HIT_AFTER_WAIT: '等待后命中',
  MYSQL_MUTEX_REBUILT: '互斥锁重建',
  PENETRATION_PROTECTED: '已写入空值',
  REDIS_NULL_GUARD: '空值拦截'
}

export const closeReasonLabel: Record<string, string> = {
  TIMEOUT_UNPAID: '超时未支付，座位已释放',
  USER_CANCEL: '已取消，座位已释放'
}

export const mqEventLabel: Record<string, string> = {
  DELAY_CLOSE_SENT: '投递延迟关单',
  TIMEOUT_CLOSED: '超时自动关单',
  TIMEOUT_SKIPPED_PAID: '已支付，跳过关单',
  ORDER_PAID_FANOUT: '支付成功广播'
}
