/**
 * 全局 HTTP 请求封装与 JWT 鉴权拦截模块。
 * 所有业务组件和 store.ts 都通过这里的 api() 发请求，它统一负责：
 * 1) 从 localStorage 持久化读写 JWT Token；
 * 2) 自动在请求头挂上 Authorization: Bearer <token>（支持按需覆盖或清空 Token，比如公开接口、压测模拟多账号）；
 * 3) 统一解析后端返回的 ApiResult JSON，遇到网关 502/网络断连或 401 登录失效时做兜底处理与自动登出回调。
 */
import type { ApiResult } from './types'

/** 保存在浏览器 localStorage 中的 JWT Token 键名 */
const AUTH_TOKEN_KEY = 'pulseticket_token'

/** 启动时从浏览器本地存储读取上次登录保存的 Token（兼容隐私模式报错兜底） */
function readSavedToken(): string {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

/** 内存中的当前登录 Token 缓存，避免每次请求都读 localStorage */
let token = readSavedToken()
/** 401 未授权回调函数（由 store.ts 注册，用于在 Token 过期时清空前端状态并弹出提示） */
let unauthorizedHandler: (() => void) | null = null

/** 获取当前内存中的 JWT Token */
export function getToken(): string {
  return token
}

/** 更新内存与 localStorage 中的 JWT Token；传入空字符串即清除登录态 */
export function setToken(value: string) {
  token = value
  try {
    if (value) {
      localStorage.setItem(AUTH_TOKEN_KEY, value)
    } else {
      localStorage.removeItem(AUTH_TOKEN_KEY)
    }
  } catch {
    // ignore storage errors in restricted environments
  }
}

/** 注册全局 401 失效拦截器（解耦 api.ts 与 store.ts，避免循环依赖） */
export function setUnauthorizedHandler(fn: () => void) {
  unauthorizedHandler = fn
}

/**
 * 统一请求入口（类似精简版 axios 拦截器）。
 * @param path 接口路径（如 /api/order/my，走 Vite / Nginx 代理到网关）
 * @param options 原生 fetch 配置项（method、body、headers 等）
 * @param overrideToken 可选覆盖 Token：传 '' 表示不带任何 Token（公开接口），传特定值可用于并发压测模拟其他用户
 */
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
  overrideToken?: string
): Promise<ApiResult<T>> {
  // 上传文件（FormData）时不要手动加 Content-Type，交给浏览器自动生成带 boundary 的 multipart 请求头
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...((options.headers as Record<string, string>) || {})
  }
  // 优先使用调用方显式传入的 overrideToken，否则使用当前登录用户的全局 token
  const bearer = overrideToken ?? token
  if (bearer) headers.Authorization = `Bearer ${bearer}`

  try {
    const resp = await fetch(path, { ...options, headers })
    const text = await resp.text()
    let parsed: ApiResult<T>
    if (!text) {
      // 响应体为空时，使用 HTTP 状态码构造标准返回对象
      parsed = { code: resp.status, message: resp.statusText, data: null as T }
    } else {
      try {
        parsed = JSON.parse(text)
      } catch {
        // 网关重启或返回 HTML 错误页时 JSON.parse 会失败，转换成前端友好的错误提示
        const statusCode = resp.status >= 400 ? resp.status : 502
        return {
          code: statusCode,
          message: `网关服务暂不可用 (HTTP ${resp.status || 502})，请稍后重试`,
          data: null as T
        }
      }
    }

    // 全局 401 登录过期拦截：排除显式传 overrideToken 的请求及登录/注册接口本身，避免误杀
    if (
      (resp.status === 401 || parsed.code === 401) &&
      overrideToken === undefined &&
      !path.startsWith('/api/user/auth/')
    ) {
      setToken('')
      unauthorizedHandler?.()
    }
    return parsed
  } catch (e: any) {
    // 断网、CORS 或本地后端未启动时的兜底返回，确保调用方永远拿到统一结构而不会直接抛出未捕获异常
    return { code: 503, message: e?.message || '网络异常，请确认网关已启动', data: null as T }
  }
}

/**
 * Feign 降级时下游错误会被包裹成一长串原始 JSON，这里提取出真正给人看的那句话。
 */
export function readableMessage(raw?: string): string {
  if (!raw) return ''
  const match = raw.match(/"message"\s*:\s*"([^"]+)"/)
  const text = match ? match[1] : raw
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}
