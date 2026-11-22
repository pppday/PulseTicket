<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  AlertCircle,
  ArrowUp,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  Code2,
  Copy,
  Headphones,
  History,
  Loader2,
  Maximize2,
  Minimize2,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X
} from 'lucide-vue-next'
import { api, getToken } from '../api'
import { refreshAll, state, toast } from '../store'
import type {
  AgentChatMessage,
  AgentChatMode,
  AgentConfirmationCard,
  AgentInteractiveWidget,
  AgentThreadDetailPayload,
  AgentThreadSummary,
  AgentThreadsListPayload,
  AgentThoughtStep,
  AgentToolCard,
  AgentWidgetOption
} from '../types'
import { money } from '../utils'

const isOpen = ref(false)
const isWide = ref(false)
const historyOpen = ref(false)
const loadingThreads = ref(false)
const loadingThreadDetail = ref(false)
const inputText = ref('')
const sending = ref(false)
const chatMode = ref<AgentChatMode>('agent')
const activeAbortController = ref<AbortController | null>(null)
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

function upsertToolCard(msg: AgentChatMessage, incoming: AgentToolCard) {
  if (!msg.toolCards) {
    msg.toolCards = []
  }
  const idx = msg.toolCards.findIndex(c => c.id === incoming.id)
  if (idx >= 0) {
    const prevExpanded = msg.toolCards[idx].expanded
    msg.toolCards[idx] = { ...incoming, expanded: prevExpanded }
  } else {
    msg.toolCards.push({ ...incoming, expanded: false })
  }
}

function upsertWidget(msg: AgentChatMessage, incoming: AgentInteractiveWidget) {
  if (!msg.widgets) {
    msg.widgets = []
  }
  if (incoming.widgetType === 'SEAT_PICKER') {
    msg.widgets = msg.widgets.filter(w => w.widgetType !== 'SHOW_PICKER')
  }
  const idx = msg.widgets.findIndex(w => w.id === incoming.id)
  if (idx >= 0) {
    const prev = msg.widgets[idx]
    msg.widgets[idx] = {
      ...incoming,
      selectedOptionId: prev.selectedOptionId,
      selectedLabel: prev.selectedLabel
    }
  } else {
    msg.widgets.push({ ...incoming })
  }
}

function formatJsonBlock(val: any): string {
  if (val === undefined) return ''
  if (typeof val === 'string') return val
  try {
    return JSON.stringify(val, null, 2)
  } catch {
    return String(val)
  }
}

function stopStreaming() {
  activeAbortController.value?.abort()
}

async function copyMessage(msg: AgentChatMessage) {
  if (!msg.content) return
  try {
    await navigator.clipboard.writeText(msg.content)
    msg.copied = true
    setTimeout(() => {
      msg.copied = false
    }, 1500)
  } catch {
    toast('info', '复制失败', '当前浏览器环境限制剪贴板写入')
  }
}

function setFeedback(msg: AgentChatMessage, val: 'up' | 'down') {
  msg.feedback = msg.feedback === val ? null : val
}

async function handleWidgetOptionClick(widget: AgentInteractiveWidget, opt: AgentWidgetOption) {
  if (sending.value || widget.selectedOptionId) return
  widget.selectedOptionId = opt.id
  widget.selectedLabel = opt.label
  await sendMessage(opt.prompt, { forceMode: 'agent' })
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

  const controller = new AbortController()
  activeAbortController.value = controller

  try {
    const resp = await fetch(path, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: controller.signal
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
          } else if (eventType === 'tool_card') {
            upsertToolCard(assistantMsg, parsed as AgentToolCard)
          } else if (eventType === 'widget') {
            upsertWidget(assistantMsg, parsed as AgentInteractiveWidget)
          } else if (eventType === 'route') {
            assistantMsg.intents = parsed.intents || []
            if (parsed.mode) assistantMsg.mode = parsed.mode
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
            if (parsed.reply) {
              assistantMsg.content = parsed.reply
            }
            assistantMsg.status = parsed.status || 'COMPLETED'
            assistantMsg.mode = parsed.mode || assistantMsg.mode || 'agent'
            assistantMsg.intents = parsed.intents || assistantMsg.intents || []
            assistantMsg.subgraphs = parsed.subgraphs || []
            assistantMsg.citations = parsed.citations || []
            assistantMsg.toolSteps = parsed.toolSteps || []
            if (Array.isArray(parsed.thinkingSteps) && parsed.thinkingSteps.length) {
              assistantMsg.thinkingSteps = parsed.thinkingSteps
            }
            if (Array.isArray(parsed.toolCards) && parsed.toolCards.length) {
              for (const tc of parsed.toolCards) {
                upsertToolCard(assistantMsg, tc as AgentToolCard)
              }
            }
            if (Array.isArray(parsed.widgets) && parsed.widgets.length) {
              for (const wg of parsed.widgets) {
                upsertWidget(assistantMsg, wg as AgentInteractiveWidget)
              }
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
  } finally {
    if (activeAbortController.value === controller) {
      activeAbortController.value = null
    }
  }
}

async function sendMessage(preset?: string, opts?: { forceMode?: AgentChatMode }) {
  const text = (preset ?? inputText.value).trim()
  if (!text || sending.value) return

  if (opts?.forceMode) {
    chatMode.value = opts.forceMode
  }
  const activeMode = chatMode.value

  if (!preset) {
    inputText.value = ''
  }

  messages.value.push({
    id: `u-${Date.now()}`,
    role: 'user',
    content: text,
    mode: activeMode
  })

  const assistantMsg: AgentChatMessage = {
    id: `a-${Date.now() + 1}`,
    role: 'assistant',
    content: '',
    mode: activeMode,
    streaming: true,
    liveStepLabel: activeMode === 'chat' ? 'Chat 只读咨询模式检索中…' : '正在经过安全护栏与主图路由…',
    intents: [],
    citations: [],
    toolSteps: [],
    thinkingSteps: [],
    toolCards: [],
    widgets: [],
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
      { threadId: threadId.value, message: text, mode: activeMode },
      reactiveAssistant
    )
    saveActiveThreadId(threadId.value)
    await loadThreads()
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      reactiveAssistant.aborted = true
      reactiveAssistant.status = 'COMPLETED'
      if (!reactiveAssistant.content) {
        reactiveAssistant.content = '已手动停止当前生成。'
      }
    } else {
      reactiveAssistant.status = 'ERROR'
      reactiveAssistant.content =
        err?.message || '智能体服务 (:8204) 未启动或网关连接异常，请稍后重试。'
    }
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
    mode: 'agent',
    streaming: true,
    liveStepLabel: confirmed ? '正在携带幂等键请求 Java 订单服务…' : '正在释放挂起确认态…',
    thinkingSteps: [],
    toolCards: [],
    widgets: [],
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
    if (err?.name === 'AbortError') {
      msg.cardDecision = 'PENDING'
      reactiveReceipt.aborted = true
      reactiveReceipt.status = 'COMPLETED'
      reactiveReceipt.content = '已手动停止接收执行回执，如已提交请在「我的订单」确认最新状态。'
    } else {
      msg.cardDecision = 'PENDING'
      reactiveReceipt.status = 'ERROR'
      reactiveReceipt.content = err?.message || '确认请求超时，请重试（幂等键保障绝不重复创单）'
    }
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
                  {{ chatMode === 'chat' ? 'Chat' : 'Agent' }}
                </span>
              </div>
              <p class="mt-0.5 text-[11px] text-ink-mute">
                {{
                  sending
                    ? (chatMode === 'chat' ? '正在实时检索并流式生成…' : '正在编排子图与执行工具调用…')
                    : (chatMode === 'chat'
                        ? 'Chat 只读咨询 · 真流式输出 · 7 天历史'
                        : 'Agent 任务执行 · 工具卡片 · 交互选择框')
                }}
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

              <!-- 2. Inline Tool Execution Cards (with </> JSON Inspector) -->
              <div
                v-if="msg.toolCards && msg.toolCards.length"
                class="space-y-2"
              >
                <div
                  v-for="card in msg.toolCards"
                  :key="card.id"
                  class="overflow-hidden rounded-2xl border border-line bg-white shadow-[0_1px_2px_rgba(26,25,23,0.03)]"
                >
                  <div class="flex items-center justify-between gap-2 px-3.5 py-2.5">
                    <div class="flex min-w-0 items-center gap-2">
                      <Loader2
                        v-if="card.status === 'running'"
                        class="h-4 w-4 shrink-0 animate-spin text-gold"
                      />
                      <CheckCircle2
                        v-else-if="card.status === 'done'"
                        class="h-4 w-4 shrink-0 text-sage"
                      />
                      <AlertCircle
                        v-else
                        class="h-4 w-4 shrink-0 text-brick"
                      />
                      <span class="truncate text-[12px] font-medium text-ink">
                        {{ card.title }}
                      </span>
                    </div>
                    <button
                      type="button"
                      class="grid h-6 w-7 shrink-0 place-items-center rounded-md border text-[11px] transition"
                      :class="
                        card.expanded
                          ? 'border-ink/30 bg-paper text-ink'
                          : 'border-line bg-white text-ink-mute hover:border-ink/25 hover:text-ink'
                      "
                      title="查看工具入参 / 返回结果 JSON"
                      @click="card.expanded = !card.expanded"
                    >
                      <Code2 class="h-3.5 w-3.5" />
                    </button>
                  </div>

                  <div
                    v-if="card.summary"
                    class="border-t border-line/60 bg-[#FAF9F6] px-3.5 py-2 text-[11.5px] leading-snug"
                    :class="card.status === 'error' ? 'text-brick' : 'text-ink-soft'"
                  >
                    <span class="mr-1 font-medium">
                      {{ card.status === 'done' ? '✓' : card.status === 'error' ? '✕' : '…' }}
                    </span>
                    {{ card.summary }}
                  </div>

                  <div
                    v-if="card.expanded"
                    class="space-y-2 border-t border-line/70 bg-[#1A1917] px-3.5 py-2.5 font-mono text-[10.5px] leading-relaxed text-[#EDE9E0]"
                  >
                    <div v-if="card.input !== undefined">
                      <div class="mb-0.5 text-[9.5px] uppercase tracking-wider text-[#B6AF9F]">
                        Input · {{ card.toolName }}
                      </div>
                      <pre class="max-h-32 overflow-auto whitespace-pre-wrap break-all">{{ formatJsonBlock(card.input) }}</pre>
                    </div>
                    <div v-if="card.output !== undefined">
                      <div class="mb-0.5 text-[9.5px] uppercase tracking-wider text-[#9BC4AE]">
                        Output
                      </div>
                      <pre class="max-h-40 overflow-auto whitespace-pre-wrap break-all">{{ formatJsonBlock(card.output) }}</pre>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 3. Main Editorial Answer Card -->
              <div
                v-if="msg.content"
                class="rounded-2xl rounded-tl-md border px-4 py-3.5 text-[12.5px] shadow-[0_1px_2px_rgba(26,25,23,0.03)]"
                :class="
                  msg.status === 'BLOCKED' || msg.status === 'ERROR'
                    ? 'border-brick/30 bg-brick-soft/35 text-ink'
                    : 'border-line bg-white text-ink'
                "
              >
                <div class="editorial-prose inline" v-html="renderEditorialMarkdown(msg.content)" />
                <span
                  v-if="msg.streaming"
                  class="ml-1 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-ink/70 align-middle"
                />
              </div>

              <!-- 4. Embedded Interactive Selection Widgets -->
              <div
                v-for="wg in (msg.widgets || [])"
                :key="wg.id"
                class="rounded-2xl border border-line bg-white p-3.5 shadow-[0_1px_2px_rgba(26,25,23,0.03)]"
              >
                <div class="flex items-start justify-between gap-2">
                  <div>
                    <p class="text-[12px] font-semibold text-ink">{{ wg.title }}</p>
                    <p v-if="wg.subtitle" class="mt-0.5 text-[11px] text-ink-mute">
                      {{ wg.subtitle }}
                    </p>
                  </div>
                </div>

                <!-- Selected State (In-Place Confirmation Stamp) -->
                <div
                  v-if="wg.selectedOptionId"
                  class="mt-2.5 flex items-center gap-1.5 rounded-xl border border-sage/25 bg-sage-soft/55 px-3 py-2 text-[11.5px] font-medium text-sage"
                >
                  <CheckCircle2 class="h-3.5 w-3.5 shrink-0" />
                  <span class="truncate">已选择：{{ wg.selectedLabel }}</span>
                </div>

                <!-- Option Picker Buttons -->
                <div v-else class="mt-2.5 space-y-1.5">
                  <button
                    v-for="opt in wg.options"
                    :key="opt.id"
                    type="button"
                    :disabled="sending"
                    class="group flex w-full items-center justify-between gap-2 rounded-xl border border-line bg-[#FAF9F6] px-3 py-2 text-left transition hover:border-ink/40 hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
                    @click="handleWidgetOptionClick(wg, opt)"
                  >
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center gap-1.5">
                        <span
                          v-if="opt.badge"
                          class="shrink-0 rounded border border-gold/30 bg-gold-soft/75 px-1.5 py-0.2 font-mono text-[10px] font-medium text-gold"
                        >
                          {{ opt.badge }}
                        </span>
                        <span class="truncate text-[11.5px] font-medium text-ink group-hover:text-black">
                          {{ opt.label }}
                        </span>
                      </div>
                      <p v-if="opt.meta" class="mt-0.5 truncate text-[10.5px] text-ink-mute">
                        {{ opt.meta }}
                      </p>
                    </div>
                    <span class="shrink-0 text-[11px] font-medium text-ink-soft group-hover:text-ink">
                      选择 →
                    </span>
                  </button>
                </div>
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

                  <div
                    v-if="msg.confirmationCard.actionType === 'REFUND_ORDER'"
                    class="mt-2.5 space-y-1 rounded-xl border border-brick/20 bg-brick-soft/35 px-3 py-2 text-[11px]"
                  >
                    <p class="font-medium text-ink">
                      {{ msg.confirmationCard.args.tier_name || '阶梯退票 [POL-REFUND-001]' }}
                    </p>
                    <p class="text-ink-soft tabular">
                      原实付 ¥{{ money(Number(msg.confirmationCard.args.total_amount || 0)) }} · 手续费（{{
                        msg.confirmationCard.args.fee_rate || '0%'
                      }}）¥{{ money(Number(msg.confirmationCard.args.fee_amount || 0)) }}
                    </p>
                  </div>

                  <!-- Perforated Stub Divider -->
                  <div class="relative my-3.5 border-t border-dashed border-line">
                    <span class="absolute -left-6 -top-2 h-4 w-4 rounded-full border border-line bg-[#FCFBF9]" />
                    <span class="absolute -right-6 -top-2 h-4 w-4 rounded-full border border-line bg-[#FCFBF9]" />
                  </div>

                  <div class="flex items-baseline justify-between">
                    <div>
                      <span class="block text-[10.5px] text-ink-mute">
                        {{ msg.confirmationCard.actionType === 'REFUND_ORDER' ? '预计原路实退金额' : '服务端权威核定总额' }}
                      </span>
                      <span class="mt-0.5 block max-w-[200px] truncate font-mono text-[10px] text-ink-mute">
                        幂等键 {{ msg.confirmationCard.idempotencyKey }}
                      </span>
                    </div>
                    <div
                      class="text-right tabular"
                      :class="msg.confirmationCard.actionType === 'REFUND_ORDER' ? 'text-brick' : 'text-ink'"
                    >
                      <span class="text-xs opacity-75">¥</span>
                      <span class="text-xl font-semibold tracking-tight">
                        {{
                          money(
                            Number(
                              msg.confirmationCard.actionType === 'REFUND_ORDER'
                                ? (msg.confirmationCard.args.refund_amount ?? msg.confirmationCard.args.total_amount ?? 0)
                                : (msg.confirmationCard.args.total_amount || 0)
                            )
                          )
                        }}
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
                      :class="
                        msg.confirmationCard.actionType === 'CANCEL_ORDER' ||
                        msg.confirmationCard.actionType === 'REFUND_ORDER'
                          ? '!bg-brick hover:!bg-[#9c3a22]'
                          : ''
                      "
                      :disabled="sending"
                      @click="handleCardDecision(msg, true)"
                    >
                      {{
                        msg.confirmationCard.actionType === 'CREATE_ORDER'
                          ? '确认锁座下单'
                          : msg.confirmationCard.actionType === 'REFUND_ORDER'
                            ? '确认申请退票'
                            : '确认取消订单'
                      }}
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
                    <CheckCircle2 v-if="msg.cardDecision === 'CONFIRMED'" class="h-3.5 w-3.5" />
                    {{
                      msg.cardDecision === 'CONFIRMED'
                        ? `已选择：${
                            msg.confirmationCard.actionType === 'CREATE_ORDER'
                              ? '确认锁座下单'
                              : msg.confirmationCard.actionType === 'REFUND_ORDER'
                                ? '确认申请退票'
                                : '确认取消订单'
                          }（单次防重锁定生效）`
                        : '已选择：暂不执行'
                    }}
                  </div>
                </div>
              </div>

              <!-- 6. Feedback & Copy Toolbar -->
              <div
                v-if="!msg.streaming && msg.content"
                class="flex items-center gap-1.5 px-1"
              >
                <button
                  type="button"
                  class="grid h-6 w-6 place-items-center rounded-md border transition"
                  :class="
                    msg.feedback === 'up'
                      ? 'border-sage/40 bg-sage-soft/50 text-sage'
                      : 'border-line/80 bg-white text-ink-mute hover:text-ink'
                  "
                  title="有帮助"
                  @click="setFeedback(msg, 'up')"
                >
                  <ThumbsUp class="h-3 w-3" />
                </button>
                <button
                  type="button"
                  class="grid h-6 w-6 place-items-center rounded-md border transition"
                  :class="
                    msg.feedback === 'down'
                      ? 'border-brick/40 bg-brick-soft/50 text-brick'
                      : 'border-line/80 bg-white text-ink-mute hover:text-ink'
                  "
                  title="需改进"
                  @click="setFeedback(msg, 'down')"
                >
                  <ThumbsDown class="h-3 w-3" />
                </button>
                <button
                  type="button"
                  class="inline-flex h-6 items-center gap-1 rounded-md border border-line/80 bg-white px-2 text-[10.5px] text-ink-mute transition hover:text-ink"
                  title="复制回复内容"
                  @click="copyMessage(msg)"
                >
                  <Check v-if="msg.copied" class="h-3 w-3 text-sage" />
                  <Copy v-else class="h-3 w-3" />
                  <span v-if="msg.copied" class="text-sage">已复制</span>
                </button>
              </div>

              <!-- 7. Task Completion Banner (Agent Mode with Tool Executions) -->
              <div
                v-if="
                  msg.mode !== 'chat' &&
                  msg.status === 'COMPLETED' &&
                  !msg.streaming &&
                  !msg.aborted &&
                  !msg.confirmationCard &&
                  ((msg.toolCards && msg.toolCards.length) || (msg.toolSteps && msg.toolSteps.length))
                "
                class="flex items-center gap-1.5 rounded-xl border border-sage/25 bg-sage-soft/50 px-3 py-2 text-[11.5px] font-medium text-sage"
              >
                <CheckCircle2 class="h-3.5 w-3.5 shrink-0" />
                <span>本次对话任务执行完成</span>
              </div>
            </div>
          </template>
        </div>

        <!-- Refined Aliyun-Style Input Composer with Agent / Chat Toggle -->
        <form
          class="border-t border-line bg-white px-4 pb-2.5 pt-3"
          @submit.prevent="sendMessage()"
        >
          <div class="rounded-2xl border border-line bg-[#FAF9F6] px-3.5 pb-2 pt-2.5 transition focus-within:border-ink/40 focus-within:bg-white focus-within:shadow-[0_0_0_3px_rgba(26,25,23,0.04)]">
            <textarea
              v-model="inputText"
              rows="2"
              :disabled="sending"
              :placeholder="
                chatMode === 'chat'
                  ? 'Chat 只读咨询：查演出、问退票/地铁规则、测算退票费率…'
                  : '咨询退改与地铁、筛选连座，或输入“锁座下单”…'
              "
              class="w-full resize-none bg-transparent text-xs leading-relaxed text-ink placeholder:text-ink-mute focus:outline-none disabled:opacity-50"
              @keydown.enter.exact.prevent="sendMessage()"
            />

            <div class="mt-1.5 flex items-center justify-between border-t border-line/60 pt-2">
              <!-- Mode Segmented Switch -->
              <div class="inline-flex items-center gap-1 rounded-xl border border-line bg-white p-0.5">
                <button
                  type="button"
                  :disabled="sending"
                  class="inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-medium transition disabled:opacity-50"
                  :class="
                    chatMode === 'agent'
                      ? 'bg-ink text-white shadow-sm'
                      : 'text-ink-soft hover:text-ink'
                  "
                  title="Agent 模式：支持工具调用、选座/下单选择框与分布式锁座确认"
                  @click="chatMode = 'agent'"
                >
                  <Sparkles class="h-3 w-3" />
                  <span>Agent</span>
                </button>
                <button
                  type="button"
                  :disabled="sending"
                  class="inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-medium transition disabled:opacity-50"
                  :class="
                    chatMode === 'chat'
                      ? 'bg-ink text-white shadow-sm'
                      : 'text-ink-soft hover:text-ink'
                  "
                  title="Chat 模式：只读咨询与规章检索，真流式秒回，不触发写操作"
                  @click="chatMode = 'chat'"
                >
                  <Headphones class="h-3 w-3" />
                  <span>Chat</span>
                </button>
              </div>

              <!-- Stop or Send Button -->
              <button
                v-if="sending"
                type="button"
                class="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink text-white transition hover:bg-black"
                aria-label="停止生成"
                title="停止当前生成"
                @click="stopStreaming"
              >
                <Square class="h-3 w-3 fill-current" />
              </button>
              <button
                v-else
                type="submit"
                :disabled="!inputText.trim()"
                class="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-30"
                aria-label="发送消息"
              >
                <ArrowUp class="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          <p class="mt-1.5 text-center text-[10px] text-ink-mute">
            内容由 AI 生成，仅供参考；锁座与票价均以 Java 服务端实时核价为准
          </p>
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
