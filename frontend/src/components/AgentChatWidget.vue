<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  ArrowUp,
  Check,
  ChevronDown,
  Clock,
  History,
  Maximize2,
  Minimize2,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  X
} from 'lucide-vue-next'
import { api, getToken } from '../api'
import { refreshAll, state, toast } from '../store'
import type {
  AgentChatMessage,
  AgentConfirmationCard,
  AgentThreadDetailPayload,
  AgentThreadSummary,
  AgentThreadsListPayload,
  AgentThoughtStep
} from '../types'
import { money } from '../utils'

const isOpen = ref(false)
const isWide = ref(false)
const historyOpen = ref(false)
const loadingThreads = ref(false)
const loadingThreadDetail = ref(false)
const inputText = ref('')
const sending = ref(false)
const scrollBox = ref<HTMLElement | null>(null)

function makeThreadId() {
  return `web-${state.user?.id || 'u'}-${Date.now().toString(36)}`
}

function threadStorageKey(uid?: number | null): string {
  return uid ? `pulseticket_agent_thread_${uid}` : ''
}

function saveActiveThreadId(tid: string) {
  const key = threadStorageKey(state.user?.id)
  if (!key) return
  try {
    if (tid) {
      localStorage.setItem(key, tid)
    } else {
      localStorage.removeItem(key)
    }
  } catch {
    // ignore storage errors
  }
}

function readSavedThreadId(uid?: number | null): string {
  const key = threadStorageKey(uid)
  if (!key) return ''
  try {
    return localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

const threadId = ref(makeThreadId())
const messages = ref<AgentChatMessage[]>([])
const threads = ref<AgentThreadSummary[]>([])

const groupedThreads = computed(() => {
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const yesterdayStart = todayStart - 86400 * 1000
  const groups: { label: string; items: AgentThreadSummary[] }[] = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '近 7 天', items: [] }
  ]
  for (const item of threads.value) {
    const ts = Date.parse(item.updatedAt || '')
    if (!Number.isFinite(ts) || ts >= todayStart) {
      groups[0].items.push(item)
    } else if (ts >= yesterdayStart) {
      groups[1].items.push(item)
    } else {
      groups[2].items.push(item)
    }
  }
  return groups.filter(g => g.items.length > 0)
})

function formatRelativeTime(iso?: string): string {
  if (!iso) return ''
  const dt = new Date(iso)
  if (Number.isNaN(dt.getTime())) return ''
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const ts = dt.getTime()
  const hh = String(dt.getHours()).padStart(2, '0')
  const mm = String(dt.getMinutes()).padStart(2, '0')
  if (ts >= todayStart) return `${hh}:${mm}`
  if (ts >= todayStart - 86400 * 1000) return `昨天 ${hh}:${mm}`
  return `${dt.getMonth() + 1}/${dt.getDate()}`
}

async function loadThreads() {
  if (!state.user?.id || !getToken()) return
  loadingThreads.value = true
  try {
    const res = await api<AgentThreadsListPayload>('/api/agent/threads?limit=30')
    if (res.code === 200 && res.data?.threads) {
      threads.value = res.data.threads
    }
  } finally {
    loadingThreads.value = false
  }
}

async function selectThread(targetThreadId: string, closeDrawer = true) {
  if (!targetThreadId || sending.value) return
  loadingThreadDetail.value = true
  try {
    const res = await api<AgentThreadDetailPayload>(
      `/api/agent/threads/${encodeURIComponent(targetThreadId)}`
    )
    if (res.code === 200 && res.data) {
      threadId.value = res.data.threadId
      messages.value = res.data.messages || []
      saveActiveThreadId(res.data.threadId)
      if (closeDrawer) {
        historyOpen.value = false
      }
      if (res.data.pendingCardExpired) {
        toast('info', '历史确认卡片已超时失效', '该笔报价已超过 15 分钟有效期，如需下单请重新选座核价')
      }
      scrollToBottom()
    } else if (readSavedThreadId(state.user?.id) === targetThreadId) {
      saveActiveThreadId('')
    }
  } finally {
    loadingThreadDetail.value = false
  }
}

async function deleteThread(targetThreadId: string) {
  if (!targetThreadId || sending.value) return
  const res = await api<{ threadId: string; deleted: boolean }>(
    `/api/agent/threads/${encodeURIComponent(targetThreadId)}`,
    { method: 'DELETE' }
  )
  if (res.code === 200) {
    threads.value = threads.value.filter(t => t.threadId !== targetThreadId)
    if (threadId.value === targetThreadId) {
      startNewConversation()
    }
    toast('success', '已删除该历史对话', '服务端会话与检查点断点已同步清理')
  } else {
    toast('error', '删除失败', res.message || '请稍后重试')
  }
}

const quickPrompts = [
  {
    eyebrow: '双意图协同 · RAG + 连座算法',
    text: '上海那场星际回响临时去不了能退票吗？可以的话帮我推荐两张 700 元以内的中间连座。'
  },
  {
    eyebrow: '跨轮槽位继承 · 权威核价中断',
    text: '太好了，就帮我锁定刚才推荐的这两张座位下单吧！'
  },
  {
    eyebrow: '场馆交通 · 条款溯源',
    text: '上海梅赛德斯文化中心坐几号线地铁？下雨天长柄雨伞能带进场吗？'
  },
  {
    eyebrow: '知识盲区 · 零幻觉诚实拒答',
    text: '买完票怎么开增值税专用发票并寄顺丰纸质票？'
  }
]

function scrollToBottom() {
  nextTick(() => {
    if (scrollBox.value) {
      scrollBox.value.scrollTop = scrollBox.value.scrollHeight
    }
  })
}

function startNewConversation() {
  if (sending.value) return
  threadId.value = makeThreadId()
  messages.value = []
  saveActiveThreadId('')
  historyOpen.value = false
}

watch(
  () => state.user?.id,
  async uid => {
    if (!uid) {
      threads.value = []
      messages.value = []
      historyOpen.value = false
      threadId.value = makeThreadId()
      return
    }
    await loadThreads()
    const saved = readSavedThreadId(uid)
    if (saved && threads.value.some(t => t.threadId === saved)) {
      await selectThread(saved, false)
    } else {
      if (saved) saveActiveThreadId('')
      threadId.value = makeThreadId()
      messages.value = []
    }
  },
  { immediate: true }
)

watch(isOpen, open => {
  if (open && state.user?.id) {
    loadThreads()
  }
})

function toggleThinking(msg: AgentChatMessage) {
  msg.thinkingExpanded = !msg.thinkingExpanded
  msg.userToggledThinking = true
}

function formatDurationSec(ms?: number): string {
  if (!ms || ms <= 0) return '0.1s'
  return `${(ms / 1000).toFixed(1)}s`
}

function formatStepSec(ms?: number): string {
  if (ms === undefined || ms < 0) return ''
  return `+${(ms / 1000).toFixed(1)}s`
}

function activeThoughtSummary(msg: AgentChatMessage): string {
  const steps = msg.thinkingSteps || []
  if (!steps.length) {
    return msg.liveStepLabel || '正在唤醒主图安全护栏与意图路由…'
  }
  const running = [...steps].reverse().find(s => s.status === 'running')
  const latest = running || steps[steps.length - 1]
  return `${latest.agentLabel} · ${latest.title}`
}

function countDistinctAgents(steps?: AgentThoughtStep[]): number {
  if (!steps || !steps.length) return 1
  return new Set(steps.map(s => s.agentLabel)).size
}

/* ---------------- Editorial XSS-Safe Markdown & Table Renderer ---------------- */

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function formatInline(escapedLine: string): string {
  return escapedLine
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-ink">$1</strong>')
    .replace(
      /`?\[(POL-[A-Z]+-\d+)\]`?/g,
      '<span class="mx-0.5 inline-flex items-center rounded-md border border-gold/25 bg-gold-soft/80 px-1.5 py-0.5 font-mono text-[10.5px] font-medium text-gold">[$1]</span>'
    )
    .replace(
      /`([^`]+)`/g,
      '<code class="rounded-md border border-line/80 bg-paper px-1.5 py-0.5 font-mono text-[11px] text-ink">$1</code>'
    )
}

function isTableSeparator(line: string): boolean {
  const trimmed = line.trim()
  return /^\|?[\s:-]+\|[\s|:-]+\|?$/.test(trimmed) && trimmed.includes('-')
}

function splitTableCells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  return trimmed.split('|').map(c => formatInline(c.trim()))
}

function renderEditorialMarkdown(rawText: string): string {
  if (!rawText) return ''
  const escaped = escapeHtml(rawText)
  const lines = escaped.split('\n')
  const htmlBlocks: string[] = []

  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      i++
      continue
    }

    // Horizontal rule
    if (/^-{3,}$/.test(trimmed)) {
      htmlBlocks.push('<hr class="my-3.5 border-t border-dashed border-line" />')
      i++
      continue
    }

    // Markdown table block
    if (trimmed.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headers = splitTableCells(lines[i])
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().includes('|') && !isTableSeparator(lines[i])) {
        rows.push(splitTableCells(lines[i]))
        i++
      }
      const thead = `<thead><tr class="border-b border-line bg-paper/90 text-[11px] font-semibold text-ink-soft">${headers
        .map(h => `<th class="px-3 py-2 font-medium">${h}</th>`)
        .join('')}</tr></thead>`
      const tbody = `<tbody class="divide-y divide-line/70">${rows
        .map(
          r =>
            `<tr class="transition hover:bg-paper/40">${r
              .map(c => `<td class="px-3 py-2 text-[11.5px] text-ink tabular">${c}</td>`)
              .join('')}</tr>`
        )
        .join('')}</tbody>`
      htmlBlocks.push(
        `<div class="my-2.5 overflow-x-auto rounded-2xl border border-line bg-white shadow-[0_1px_2px_rgba(26,25,23,0.03)]"><table class="w-full border-collapse text-left">${thead}${tbody}</table></div>`
      )
      continue
    }

    // Headings (## / ###)
    const hMatch = trimmed.match(/^(#{1,3})\s+(.+)$/)
    if (hMatch) {
      const level = hMatch[1].length
      const content = formatInline(hMatch[2])
      if (level <= 2) {
        htmlBlocks.push(
          `<h4 class="mt-3 mb-1.5 text-[13px] font-semibold tracking-tight text-ink first:mt-0">${content}</h4>`
        )
      } else {
        htmlBlocks.push(
          `<h5 class="mt-3 mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold tracking-tight text-ink first:mt-0">${content}</h5>`
        )
      }
      i++
      continue
    }

    // Blockquote (> ...)
    if (trimmed.startsWith('&gt;')) {
      const quoteLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('&gt;')) {
        quoteLines.push(formatInline(lines[i].trim().replace(/^&gt;\s?/, '')))
        i++
      }
      htmlBlocks.push(
        `<blockquote class="my-2.5 rounded-r-xl border-l-2 border-gold bg-gold-soft/45 px-3.5 py-2 text-[11.5px] leading-relaxed text-ink-soft">${quoteLines.join(
          '<br/>'
        )}</blockquote>`
      )
      continue
    }

    // Bullet / Numbered list
    if (/^([-*•]|\d+\.)\s+/.test(trimmed)) {
      const items: string[] = []
      while (i < lines.length && /^([-*•]|\d+\.)\s+/.test(lines[i].trim())) {
        const itemRaw = lines[i].trim()
        const numMatch = itemRaw.match(/^(\d+)\.\s+(.+)$/)
        if (numMatch) {
          items.push(
            `<li class="flex items-start gap-2"><span class="mt-0.5 font-mono text-[10.5px] font-medium text-ink-mute tabular">${numMatch[1]}.</span><span class="flex-1">${formatInline(
              numMatch[2]
            )}</span></li>`
          )
        } else {
          const cleanItem = itemRaw.replace(/^[-*•]\s+/, '')
          items.push(
            `<li class="flex items-start gap-2"><span class="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ink-mute"></span><span class="flex-1">${formatInline(
              cleanItem
            )}</span></li>`
          )
        }
        i++
      }
      htmlBlocks.push(`<ul class="my-1.5 space-y-1.5">${items.join('')}</ul>`)
      continue
    }

    // Standard paragraph
    const paraLines: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,3}\s+|[-*•]\s+|\d+\.\s+|&gt;|-{3,}$)/.test(lines[i].trim()) &&
      !(lines[i].includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
    ) {
      paraLines.push(formatInline(lines[i].trim()))
      i++
    }
    htmlBlocks.push(`<p class="my-1.5 leading-[1.68] first:mt-0 last:mb-0">${paraLines.join('<br/>')}</p>`)
  }

  return htmlBlocks.join('')
}

function upsertThoughtStep(msg: AgentChatMessage, incoming: AgentThoughtStep) {
  if (!msg.thinkingSteps) {
    msg.thinkingSteps = []
  }
  const idx = msg.thinkingSteps.findIndex(s => s.key === incoming.key)
  if (idx >= 0) {
    msg.thinkingSteps[idx] = incoming
  } else {
    msg.thinkingSteps.push(incoming)
  }
}

async function consumeSsePost(
  path: string,
  payload: Record<string, any>,
  assistantMsg: AgentChatMessage
) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json'
  }
  const jwt = getToken()
  if (jwt) {
    headers.Authorization = `Bearer ${jwt}`
  }

  const resp = await fetch(path, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload)
  })

  if (!resp.ok || !resp.body) {
    let errText = `智能体服务暂不可用 (HTTP ${resp.status})`
    try {
      const errBody = await resp.json()
      errText = errBody.detail || errBody.message || errText
    } catch {
      // ignore non-json error body
    }
    throw new Error(errText)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const rawBlock = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')

      let eventType = 'message'
      let dataStr = ''
      for (const line of rawBlock.split('\n')) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataStr += line.slice(5).trim()
        }
      }
      if (!dataStr) continue

      try {
        const parsed = JSON.parse(dataStr)
        if (eventType === 'thought') {
          upsertThoughtStep(assistantMsg, parsed as AgentThoughtStep)
        } else if (eventType === 'route') {
          assistantMsg.intents = parsed.intents || []
        } else if (eventType === 'step') {
          assistantMsg.liveStepLabel = parsed.label || parsed.node
        } else if (eventType === 'token') {
          // 正文开始流式打印时：若用户没有手动固定展开，自动优雅收起思考抽屉让阅读视线聚焦正文
          if (!assistantMsg.content && !assistantMsg.userToggledThinking) {
            assistantMsg.thinkingExpanded = false
          }
          assistantMsg.content += parsed.delta || ''
        } else if (eventType === 'interrupt') {
          assistantMsg.confirmationCard = parsed.confirmationCard as AgentConfirmationCard
          assistantMsg.cardDecision = 'PENDING'
        } else if (eventType === 'done') {
          if (!assistantMsg.content && parsed.reply) {
            assistantMsg.content = parsed.reply
          }
          assistantMsg.status = parsed.status || 'COMPLETED'
          assistantMsg.intents = parsed.intents || assistantMsg.intents || []
          assistantMsg.subgraphs = parsed.subgraphs || []
          assistantMsg.citations = parsed.citations || []
          assistantMsg.toolSteps = parsed.toolSteps || []
          if (Array.isArray(parsed.thinkingSteps) && parsed.thinkingSteps.length) {
            assistantMsg.thinkingSteps = parsed.thinkingSteps
          }
          assistantMsg.latencyMs = parsed.latencyMs
          if (!assistantMsg.userToggledThinking) {
            assistantMsg.thinkingExpanded = false
          }
          if (parsed.confirmationCard) {
            assistantMsg.confirmationCard = parsed.confirmationCard
            assistantMsg.cardDecision = assistantMsg.cardDecision || 'PENDING'
          }
        } else if (eventType === 'error') {
          throw new Error(parsed.message || '智能体流式响应异常')
        }
      } catch (e: any) {
        if (eventType === 'error') throw e
      }
      scrollToBottom()
    }
  }
}

async function sendMessage(preset?: string) {
  const text = (preset ?? inputText.value).trim()
  if (!text || sending.value) return

  if (!preset) {
    inputText.value = ''
  }

  messages.value.push({
    id: `u-${Date.now()}`,
    role: 'user',
    content: text
  })

  const assistantMsg: AgentChatMessage = {
    id: `a-${Date.now() + 1}`,
    role: 'assistant',
    content: '',
    streaming: true,
    liveStepLabel: '正在经过安全护栏与主图路由…',
    intents: [],
    citations: [],
    toolSteps: [],
    thinkingSteps: [],
    thinkingExpanded: true,
    userToggledThinking: false
  }
  messages.value.push(assistantMsg)
  const reactiveAssistant = messages.value[messages.value.length - 1]
  sending.value = true
  scrollToBottom()

  try {
    await consumeSsePost(
      '/api/agent/chat/stream',
      { threadId: threadId.value, message: text },
      reactiveAssistant
    )
    saveActiveThreadId(threadId.value)
    await loadThreads()
  } catch (err: any) {
    reactiveAssistant.status = 'ERROR'
    reactiveAssistant.content =
      err?.message || '智能体服务 (:8204) 未启动或网关连接异常，请稍后重试。'
  } finally {
    reactiveAssistant.streaming = false
    reactiveAssistant.liveStepLabel = undefined
    sending.value = false
    scrollToBottom()
  }
}

async function handleCardDecision(msg: AgentChatMessage, confirmed: boolean) {
  if (!msg.confirmationCard || msg.cardDecision !== 'PENDING' || sending.value) {
    return
  }

  msg.cardDecision = 'SUBMITTING'
  sending.value = true

  const receiptMsg: AgentChatMessage = {
    id: `r-${Date.now()}`,
    role: 'assistant',
    content: '',
    streaming: true,
    liveStepLabel: confirmed ? '正在携带幂等键请求 Java 订单服务…' : '正在释放挂起确认态…',
    thinkingSteps: [],
    thinkingExpanded: true,
    userToggledThinking: false
  }
  messages.value.push(receiptMsg)
  const reactiveReceipt = messages.value[messages.value.length - 1]
  scrollToBottom()

  try {
    await consumeSsePost(
      '/api/agent/confirm/stream',
      { threadId: threadId.value, confirmed },
      reactiveReceipt
    )
    msg.cardDecision = confirmed ? 'CONFIRMED' : 'REJECTED'
    saveActiveThreadId(threadId.value)
    await loadThreads()

    if (confirmed) {
      await refreshAll()
      toast('success', '锁座与订单状态已同步', '座位图与我的订单已实时刷新')
    }
  } catch (err: any) {
    msg.cardDecision = 'PENDING'
    reactiveReceipt.status = 'ERROR'
    reactiveReceipt.content = err?.message || '确认请求超时，请重试（幂等键保障绝不重复创单）'
  } finally {
    reactiveReceipt.streaming = false
    reactiveReceipt.liveStepLabel = undefined
    sending.value = false
    scrollToBottom()
  }
}
</script>

<template>
  <div class="fixed bottom-6 right-6 z-30">
    <!-- Backdrop when expanded to Studio Wide mode on smaller viewports -->
    <Transition
      enter-active-class="transition duration-200"
      enter-from-class="opacity-0"
      leave-active-class="transition duration-150"
      leave-to-class="opacity-0"
    >
      <div
        v-if="isOpen && isWide"
        class="fixed inset-0 -z-10 bg-ink/10 backdrop-blur-[1px] sm:hidden"
        @click="isWide = false"
      />
    </Transition>

    <!-- Main Concierge Panel -->
    <Transition
      enter-active-class="transition duration-250 ease-out"
      enter-from-class="opacity-0 translate-y-4 scale-[0.97]"
      leave-active-class="transition duration-150 ease-in"
      leave-to-class="opacity-0 translate-y-3 scale-[0.98]"
    >
      <section
        v-if="isOpen"
        class="relative mb-3 flex flex-col overflow-hidden rounded-[28px] border border-[#E5E1DA] bg-[#FCFBF9] shadow-[0_24px_64px_-16px_rgba(26,25,23,0.24),0_2px_8px_rgba(26,25,23,0.05)] transition-all duration-300 ease-out"
        :class="
          isWide
            ? 'h-[760px] max-h-[calc(100vh-96px)] w-[660px] max-w-[calc(100vw-32px)]'
            : 'h-[650px] max-h-[calc(100vh-96px)] w-[436px] max-w-[calc(100vw-32px)]'
        "
      >
        <!-- Editorial Glass Header -->
        <header class="flex items-center justify-between border-b border-line bg-[#FAF8F4]/95 px-5 py-4 backdrop-blur">
          <div class="flex items-center gap-3">
            <div class="relative grid h-9 w-9 place-items-center rounded-2xl border border-ink/10 bg-ink text-[#FAF9F7] shadow-sm">
              <Sparkles class="h-4 w-4 text-[#E6C786]" />
              <span
                class="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-[#FAF8F4]"
                :class="sending ? 'animate-pulse bg-gold' : 'bg-sage'"
              />
            </div>
            <div>
              <div class="flex items-center gap-2">
                <h3 class="text-[14px] font-semibold tracking-tight text-ink">PulseTicket 智能顾问</h3>
                <span class="rounded-full border border-line bg-white px-2 py-0.5 font-mono text-[10px] text-ink-soft">
                  Multi-Agent
                </span>
              </div>
              <p class="mt-0.5 text-[11px] text-ink-mute">
                {{ sending ? '正在深度思考与调度子图…' : '主图意图路由 · 3轮轻量记忆 · 7天历史恢复' }}
              </p>
            </div>
          </div>

          <div class="flex items-center gap-1">
            <button
              type="button"
              class="relative grid h-8 w-8 place-items-center rounded-full text-ink-mute transition hover:bg-white hover:text-ink"
              :class="historyOpen ? 'bg-white text-ink shadow-sm' : ''"
              title="最近 7 天对话"
              @click="historyOpen = !historyOpen"
            >
              <History class="h-3.5 w-3.5" />
              <span
                v-if="threads.length"
                class="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-[14px] items-center justify-center rounded-full bg-gold px-1 font-mono text-[9px] font-semibold text-white"
              >
                {{ threads.length }}
              </span>
            </button>
            <button
              class="hidden h-8 w-8 place-items-center rounded-full text-ink-mute transition hover:bg-white hover:text-ink sm:grid"
              :title="isWide ? '切换紧凑视图' : '切换宽屏阅读视图'"
              @click="isWide = !isWide"
            >
              <Minimize2 v-if="isWide" class="h-3.5 w-3.5" />
              <Maximize2 v-else class="h-3.5 w-3.5" />
            </button>
            <button
              class="grid h-8 w-8 place-items-center rounded-full text-ink-mute transition hover:bg-white hover:text-ink disabled:opacity-35"
              title="开启新对话"
              :disabled="sending"
              @click="startNewConversation"
            >
              <RotateCcw class="h-3.5 w-3.5" />
            </button>
            <button
              class="grid h-8 w-8 place-items-center rounded-full text-ink-mute transition hover:bg-white hover:text-ink"
              aria-label="收起窗口"
              @click="isOpen = false"
            >
              <X class="h-4 w-4" />
            </button>
          </div>
        </header>

        <!-- 7-Day Sliding History Drawer Overlay -->
        <Transition
          enter-active-class="transition duration-200 ease-out"
          enter-from-class="opacity-0 -translate-x-3"
          leave-active-class="transition duration-150 ease-in"
          leave-to-class="opacity-0 -translate-x-3"
        >
          <div
            v-if="historyOpen"
            class="absolute inset-x-0 bottom-0 top-[73px] z-20 flex bg-ink/20 backdrop-blur-[1px]"
            @click.self="historyOpen = false"
          >
            <aside class="flex h-full w-[276px] max-w-[84%] flex-col border-r border-line bg-[#FAF8F4] shadow-xl">
              <div class="flex items-center justify-between border-b border-line px-4 py-3">
                <div>
                  <p class="text-xs font-semibold text-ink">最近 7 天对话</p>
                  <p class="text-[10.5px] text-ink-mute">活跃自动顺延 · 模型仅读近 3 轮</p>
                </div>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 rounded-lg border border-line bg-white px-2.5 py-1 text-[11px] font-medium text-ink transition hover:border-ink/30 hover:bg-paper disabled:opacity-40"
                  :disabled="sending"
                  @click="startNewConversation"
                >
                  <Plus class="h-3 w-3" />
                  <span>新对话</span>
                </button>
              </div>

              <div class="flex-1 space-y-4 overflow-y-auto px-3 py-3">
                <div v-if="loadingThreads && !threads.length" class="py-8 text-center text-xs text-ink-mute">
                  正在加载历史对话…
                </div>
                <div
                  v-else-if="!threads.length"
                  class="rounded-xl border border-dashed border-line bg-white/70 px-3 py-6 text-center text-xs text-ink-mute"
                >
                  近 7 天暂无保存的对话记录
                </div>

                <div v-for="group in groupedThreads" :key="group.label" class="space-y-1.5">
                  <p class="px-1.5 font-mono text-[10px] font-medium uppercase tracking-wider text-ink-mute">
                    {{ group.label }}
                  </p>
                  <div
                    v-for="item in group.items"
                    :key="item.threadId"
                    role="button"
                    tabindex="0"
                    class="group relative flex cursor-pointer items-start justify-between gap-2 rounded-xl border px-3 py-2.5 transition"
                    :class="
                      item.threadId === threadId
                        ? 'border-ink/30 bg-white shadow-sm'
                        : 'border-transparent bg-white/60 hover:border-line hover:bg-white'
                    "
                    @click="selectThread(item.threadId)"
                  >
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center gap-1.5">
                        <span class="truncate text-xs font-medium text-ink">
                          {{ item.title }}
                        </span>
                        <span
                          v-if="item.hasPendingCard"
                          class="shrink-0 rounded bg-gold-soft px-1.5 py-0.5 text-[9.5px] font-medium text-gold"
                        >
                          待确认
                        </span>
                      </div>
                      <p class="mt-0.5 truncate text-[11px] text-ink-soft">
                        {{ item.lastPreview || '点击恢复对话' }}
                      </p>
                      <p class="mt-1 font-mono text-[10px] text-ink-mute">
                        {{ formatRelativeTime(item.updatedAt) }} · {{ item.messageCount }} 条
                      </p>
                    </div>
                    <button
                      type="button"
                      class="shrink-0 rounded-lg p-1 text-ink-mute opacity-70 transition hover:bg-brick-soft/50 hover:text-brick group-hover:opacity-100 disabled:opacity-30"
                      title="删除该会话"
                      :disabled="sending"
                      @click.stop="deleteThread(item.threadId)"
                    >
                      <Trash2 class="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </aside>
          </div>
        </Transition>

        <!-- Conversation Canvas -->
        <div ref="scrollBox" class="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          <div
            v-if="loadingThreadDetail && !messages.length"
            class="rounded-2xl border border-line bg-white px-4 py-6 text-center text-xs text-ink-mute"
          >
            正在恢复历史对话与思考链轨迹…
          </div>
          <!-- Curated Empty State -->
          <div v-else-if="!messages.length" class="space-y-5 py-1">
            <div class="rounded-2xl border border-line/90 bg-white p-4 shadow-[0_1px_3px_rgba(26,25,23,0.02)]">
              <p class="eyebrow">Concierge Architecture</p>
              <p class="mt-2 text-[13px] font-medium leading-snug text-ink">
                你好，{{ state.user?.nickname || state.username }}。
              </p>
              <p class="mt-1.5 text-xs leading-relaxed text-ink-soft">
                每一次提问都会实时展示<strong>主图路由与子智能体思考链</strong>：规则检索经证据判分防幻觉，选座与锁座经 Java 服务端核价与人工确认门控。
              </p>
            </div>

            <div class="space-y-2">
              <p class="eyebrow px-0.5">精选体验场景</p>
              <div class="grid gap-2" :class="isWide ? 'sm:grid-cols-2' : 'grid-cols-1'">
                <button
                  v-for="item in quickPrompts"
                  :key="item.eyebrow"
                  class="group flex flex-col justify-between rounded-2xl border border-line bg-white p-3.5 text-left transition duration-150 hover:border-ink/30 hover:bg-[#FAF8F4] hover:shadow-soft"
                  @click="sendMessage(item.text)"
                >
                  <span class="font-mono text-[10px] tracking-wide text-gold">
                    {{ item.eyebrow }}
                  </span>
                  <p class="mt-1.5 text-xs leading-relaxed text-ink group-hover:text-black">
                    {{ item.text }}
                  </p>
                </button>
              </div>
            </div>
          </div>

          <!-- Streamed Conversation Turns -->
          <template v-for="msg in messages" :key="msg.id">
            <!-- User Turn -->
            <div v-if="msg.role === 'user'" class="flex justify-end pl-10">
              <div class="rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-[12.5px] leading-relaxed text-[#FAF9F7] shadow-sm">
                {{ msg.content }}
              </div>
            </div>

            <!-- Assistant Turn -->
            <div v-else class="space-y-2.5 pr-2">
              <!-- 1. Collapsible Editorial Thinking & Orchestration Drawer -->
              <div
                v-if="(msg.thinkingSteps && msg.thinkingSteps.length) || msg.streaming"
                class="overflow-hidden rounded-2xl border border-[#E6E2DA] bg-[#F6F4EE]/90 transition"
              >
                <button
                  type="button"
                  class="flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left transition hover:bg-[#F0EDE5]/70"
                  @click="toggleThinking(msg)"
                >
                  <div class="flex min-w-0 items-center gap-2.5">
                    <!-- Status Indicator -->
                    <span v-if="msg.streaming && !msg.content" class="relative flex h-2 w-2 shrink-0">
                      <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-gold opacity-75" />
                      <span class="relative inline-flex h-2 w-2 rounded-full bg-gold" />
                    </span>
                    <span
                      v-else
                      class="h-1.5 w-1.5 shrink-0 rounded-full"
                      :class="msg.status === 'BLOCKED' ? 'bg-brick' : 'bg-sage'"
                    />

                    <div class="min-w-0 truncate">
                      <span v-if="msg.streaming && !msg.content" class="text-[11.5px] font-medium text-ink">
                        {{ activeThoughtSummary(msg) }}
                      </span>
                      <span v-else class="text-[11.5px] font-medium text-ink-soft">
                        思考与编排轨迹
                        <span class="mx-1 text-ink-mute">·</span>
                        协同 {{ countDistinctAgents(msg.thinkingSteps) }} 个模块 / {{ msg.thinkingSteps?.length || 1 }} 步推理
                      </span>
                    </div>
                  </div>

                  <div class="flex shrink-0 items-center gap-2">
                    <span v-if="msg.latencyMs" class="font-mono text-[10.5px] text-ink-mute tabular">
                      {{ formatDurationSec(msg.latencyMs) }}
                    </span>
                    <ChevronDown
                      class="h-3.5 w-3.5 text-ink-mute transition-transform duration-200"
                      :class="msg.thinkingExpanded ? 'rotate-180' : ''"
                    />
                  </div>
                </button>

                <!-- Expanded Step-by-Step Reasoning Rail -->
                <div
                  v-if="msg.thinkingExpanded && msg.thinkingSteps && msg.thinkingSteps.length"
                  class="border-t border-[#E8E4DC] bg-[#FAF9F5] px-4 py-3.5"
                >
                  <ol class="relative ml-1.5 space-y-3 border-l border-[#DED9CF] pl-4">
                    <li v-for="step in msg.thinkingSteps" :key="step.key" class="relative">
                      <!-- Timeline Node Dot -->
                      <span
                        class="absolute -left-[20.5px] top-1 flex h-2.5 w-2.5 items-center justify-center rounded-full ring-2 ring-[#FAF9F5]"
                        :class="
                          step.status === 'running'
                            ? 'bg-gold animate-pulse'
                            : step.status === 'warn'
                              ? 'bg-brick'
                              : 'bg-sage'
                        "
                      />

                      <div class="flex flex-wrap items-baseline justify-between gap-2">
                        <div class="flex flex-wrap items-center gap-1.5">
                          <span class="rounded border border-line bg-white px-1.5 py-0.5 text-[10px] font-medium text-ink-soft">
                            {{ step.agentLabel }}
                          </span>
                          <span class="text-[11.5px] font-medium text-ink">
                            {{ step.title }}
                          </span>
                        </div>
                        <span class="font-mono text-[10px] text-ink-mute tabular">
                          {{ formatStepSec(step.atMs) }}
                        </span>
                      </div>

                      <p v-if="step.detail" class="mt-1 text-[11px] leading-relaxed text-ink-soft">
                        {{ step.detail }}
                      </p>

                      <div v-if="step.badges && step.badges.length" class="mt-1.5 flex flex-wrap gap-1">
                        <span
                          v-for="b in step.badges"
                          :key="b"
                          class="rounded bg-ink/[0.05] px-1.5 py-0.5 font-mono text-[10px] text-ink-soft"
                        >
                          {{ b }}
                        </span>
                      </div>
                    </li>
                  </ol>
                </div>
              </div>

              <!-- 2. Main Editorial Answer Card -->
              <div
                v-if="msg.content"
                class="rounded-2xl rounded-tl-md border px-4 py-3.5 text-[12.5px] shadow-[0_1px_2px_rgba(26,25,23,0.03)]"
                :class="
                  msg.status === 'BLOCKED' || msg.status === 'ERROR'
                    ? 'border-brick/30 bg-brick-soft/35 text-ink'
                    : 'border-line bg-white text-ink'
                "
              >
                <div class="editorial-prose" v-html="renderEditorialMarkdown(msg.content)" />
              </div>

              <!-- 3. Perforated Theater-Stub Confirmation Card (Interrupt Gate) -->
              <div
                v-if="msg.confirmationCard"
                class="relative overflow-hidden rounded-2xl border border-ink/20 bg-white shadow-soft"
              >
                <!-- Top Ticket Header -->
                <div class="flex items-center justify-between bg-ink px-4 py-2.5 text-[#FAF9F7]">
                  <span class="flex items-center gap-1.5 text-xs font-medium tracking-wide">
                    <ShieldCheck class="h-3.5 w-3.5 text-[#E6C786]" />
                    {{ msg.confirmationCard.title }}
                  </span>
                  <span
                    v-if="msg.confirmationCard.isMock"
                    class="rounded bg-white/15 px-1.5 py-0.5 font-mono text-[10px] text-[#E6C786]"
                  >
                    DEV_MOCK
                  </span>
                  <span v-else class="font-mono text-[10px] text-[#B8D4C5]">
                    Java BigDecimal 实时核价
                  </span>
                </div>

                <!-- Ticket Body -->
                <div class="px-4 py-3.5 text-xs">
                  <p class="text-[13px] font-semibold leading-snug text-ink">
                    {{ msg.confirmationCard.args.show_title }}
                  </p>
                  <p v-if="msg.confirmationCard.args.session_title" class="mt-1 text-ink-soft">
                    {{ msg.confirmationCard.args.session_title }}
                    <template v-if="msg.confirmationCard.args.venue_name">
                      · {{ msg.confirmationCard.args.venue_name }}
                    </template>
                  </p>

                  <div
                    v-if="msg.confirmationCard.args.seat_labels && msg.confirmationCard.args.seat_labels.length"
                    class="mt-2.5 flex flex-wrap gap-1.5"
                  >
                    <span
                      v-for="lb in msg.confirmationCard.args.seat_labels"
                      :key="lb"
                      class="rounded-full border border-line bg-paper px-2.5 py-0.5 text-[11px] font-medium text-ink"
                    >
                      {{ lb }}
                    </span>
                  </div>

                  <p v-if="msg.confirmationCard.args.order_no" class="mt-2 font-mono text-[11px] text-ink-soft">
                    关联单号：{{ msg.confirmationCard.args.order_no }}
                  </p>

                  <!-- Perforated Stub Divider -->
                  <div class="relative my-3.5 border-t border-dashed border-line">
                    <span class="absolute -left-6 -top-2 h-4 w-4 rounded-full border border-line bg-[#FCFBF9]" />
                    <span class="absolute -right-6 -top-2 h-4 w-4 rounded-full border border-line bg-[#FCFBF9]" />
                  </div>

                  <div class="flex items-baseline justify-between">
                    <div>
                      <span class="block text-[10.5px] text-ink-mute">服务端权威核定总额</span>
                      <span class="mt-0.5 block max-w-[200px] truncate font-mono text-[10px] text-ink-mute">
                        幂等键 {{ msg.confirmationCard.idempotencyKey }}
                      </span>
                    </div>
                    <div class="text-right tabular">
                      <span class="text-xs text-ink-soft">¥</span>
                      <span class="text-xl font-semibold tracking-tight text-ink">
                        {{ money(Number(msg.confirmationCard.args.total_amount || 0)) }}
                      </span>
                    </div>
                  </div>

                  <!-- Single-Click Guarded Buttons -->
                  <div v-if="msg.cardDecision === 'PENDING'" class="mt-3.5 flex gap-2">
                    <button
                      type="button"
                      class="btn-ghost h-9 flex-1 px-0 text-xs"
                      :disabled="sending"
                      @click="handleCardDecision(msg, false)"
                    >
                      暂不执行
                    </button>
                    <button
                      type="button"
                      class="btn-primary h-9 flex-[1.5] px-0 text-xs"
                      :class="msg.confirmationCard.actionType === 'CANCEL_ORDER' ? '!bg-brick hover:!bg-[#9c3a22]' : ''"
                      :disabled="sending"
                      @click="handleCardDecision(msg, true)"
                    >
                      {{ msg.confirmationCard.actionType === 'CREATE_ORDER' ? '确认锁座下单' : '确认取消订单' }}
                    </button>
                  </div>

                  <div
                    v-else-if="msg.cardDecision === 'SUBMITTING'"
                    class="mt-3 rounded-xl bg-paper py-2 text-center text-[11px] text-ink-mute"
                  >
                    正在携带幂等键向 Java 订单服务提交…
                  </div>

                  <div
                    v-else-if="msg.cardDecision === 'EXPIRED'"
                    class="mt-3 flex items-center justify-center gap-1.5 rounded-xl bg-paper py-2 text-[11px] font-medium text-ink-mute"
                  >
                    <Clock class="h-3.5 w-3.5" />
                    <span>报价已超过 15 分钟失效，请重新选座核价</span>
                  </div>

                  <div
                    v-else
                    class="mt-3 flex items-center justify-center gap-1.5 rounded-xl bg-paper py-2 text-[11px] font-medium"
                    :class="msg.cardDecision === 'CONFIRMED' ? 'text-sage' : 'text-ink-mute'"
                  >
                    <Check v-if="msg.cardDecision === 'CONFIRMED'" class="h-3.5 w-3.5" />
                    {{ msg.cardDecision === 'CONFIRMED' ? '已确认提交 · 单次防重锁定生效' : '已取消该次操作' }}
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>

        <!-- Refined Input Composer -->
        <form
          class="border-t border-line bg-white px-4 py-3"
          @submit.prevent="sendMessage()"
        >
          <div class="flex items-center gap-2 rounded-2xl border border-line bg-[#FAF9F6] px-3.5 py-1.5 transition focus-within:border-ink/40 focus-within:bg-white focus-within:shadow-[0_0_0_3px_rgba(26,25,23,0.04)]">
            <input
              v-model="inputText"
              type="text"
              :disabled="sending"
              placeholder="咨询退改与地铁、筛选连座，或输入“锁座下单”…"
              class="h-8 flex-1 bg-transparent text-xs text-ink placeholder:text-ink-mute focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              :disabled="!inputText.trim() || sending"
              class="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-ink text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-30"
              aria-label="发送消息"
            >
              <ArrowUp class="h-3.5 w-3.5" />
            </button>
          </div>
        </form>
      </section>
    </Transition>

    <!-- Tactile Floating Concierge Pill -->
    <div class="flex justify-end">
      <button
        type="button"
        class="group inline-flex h-12 items-center gap-2.5 rounded-full border border-white/10 bg-ink px-5 text-xs font-medium text-[#FAF9F7] shadow-float transition duration-200 hover:-translate-y-0.5 hover:bg-black"
        @click="isOpen = !isOpen"
      >
        <span class="relative flex h-2 w-2">
          <span v-if="!isOpen" class="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#E6C786] opacity-60" />
          <span class="relative inline-flex h-2 w-2 rounded-full bg-[#E6C786]" />
        </span>
        <span class="tracking-wide">{{ isOpen ? '收起智能顾问' : 'AI 购票顾问' }}</span>
      </button>
    </div>
  </div>
</template>
