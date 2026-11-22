import type { ApiResult } from './types'

const AUTH_TOKEN_KEY = 'pulseticket_token'

function readSavedToken(): string {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

let token = readSavedToken()
let unauthorizedHandler: (() => void) | null = null

export function getToken(): string {
  return token
}

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

export function setUnauthorizedHandler(fn: () => void) {
  unauthorizedHandler = fn
}

export async function api<T = any>(
  path: string,
  options: RequestInit = {},
  overrideToken?: string
): Promise<ApiResult<T>> {
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...((options.headers as Record<string, string>) || {})
  }
  const bearer = overrideToken ?? token
  if (bearer) headers.Authorization = `Bearer ${bearer}`

  try {
    const resp = await fetch(path, { ...options, headers })
    const text = await resp.text()
    let parsed: ApiResult<T>
    if (!text) {
      parsed = { code: resp.status, message: resp.statusText, data: null as T }
    } else {
      try {
        parsed = JSON.parse(text)
      } catch {
        const statusCode = resp.status >= 400 ? resp.status : 502
        return {
          code: statusCode,
          message: `网关服务暂不可用 (HTTP ${resp.status || 502})，请稍后重试`,
          data: null as T
        }
      }
    }

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
