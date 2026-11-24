<script setup lang="ts">
/**
 * 【运维专属 · 架构可观测与事务/MQ/Agent 遥测页（TelemetryView）】
 * 可视化展示一笔下单经过的 4 道架构防线，并实时拉取 Seata 全局事务审计、RabbitMQ 消息事件以及 Python 智能体 Trace 日志。
 */
import { computed, ref } from 'vue'
import { ChevronDown, RotateCw } from 'lucide-vue-next'
import { loadProfile, loadTelemetry, state } from '../store'
import { mqEventLabel, shortTime } from '../utils'

const refreshing = ref(false)
const expandedAudit = ref<number | null>(null)
const expandedMq = ref<number | null>(null)
const expandedAgent = ref<number | null>(null)

async function refresh() {
  refreshing.value = true
  await Promise.all([loadTelemetry(), loadProfile()])
  refreshing.value = false
}

const chain = [
  { no: '01', title: '网关鉴权', desc: '校验 JWT，清洗伪造请求头，向下游注入用户身份' },
  { no: '02', title: '分布式锁', desc: '用户防重锁 + 座位联锁，锁的范围包住整个事务' },
  { no: '03', title: '全局事务', desc: 'Seata AT 跨库提交，异常时按 undo_log 自动回滚' },
  { no: '04', title: '消息队列', desc: '死信队列超时关单，支付广播由唯一索引保证幂等' }
]

const stats = computed(() => {
  const committed = state.seataAudits.filter(a => a.txStatus === 'COMMITTED').length
  const rolledBack = state.seataAudits.filter(a => a.txStatus === 'ROLLED_BACK').length
  return [
    { label: '全局事务', value: state.seataAudits.length },
    { label: '已提交', value: committed, tone: 'text-sage' },
    { label: '已回滚', value: rolledBack, tone: 'text-brick' },
    { label: '消息事件', value: state.mqLogs.length }
  ]
})

const services = [
  { name: 'ticket-gateway', port: 8200 },
  { name: 'user-service', port: 8201 },
  { name: 'show-service', port: 8202 },
  { name: 'order-service', port: 8203 },
  { name: 'ticket-agent', port: 8204 }
]
</script>

<template>
  <div class="space-y-12">
    <header class="flex flex-wrap items-end justify-between gap-6">
      <div class="max-w-xl">
        <p class="eyebrow">系统观测</p>
        <h1 class="mt-2 text-[28px] font-semibold tracking-tight">一次下单经过了什么</h1>
        <p class="mt-3 text-sm leading-relaxed text-ink-soft">
          每个订单都会依次穿过下面四层。下方实时读取事务审计表与消息日志，点击任意记录可展开详情。
        </p>
      </div>
      <button class="btn-ghost h-10" @click="refresh">
        <RotateCw class="h-3.5 w-3.5" :class="refreshing ? 'animate-spin' : ''" />
        刷新
      </button>
    </header>

    <!-- chain -->
    <section class="card grid divide-y divide-line md:grid-cols-4 md:divide-x md:divide-y-0">
      <div v-for="step in chain" :key="step.no" class="p-7">
        <span class="font-mono text-xs text-ink-mute">{{ step.no }}</span>
        <h3 class="mt-4 text-base font-semibold tracking-tight">{{ step.title }}</h3>
        <p class="mt-2 text-sm leading-relaxed text-ink-soft">{{ step.desc }}</p>
      </div>
    </section>

    <!-- stats -->
    <section>
      <p class="eyebrow">最近 15 条采样</p>
      <dl class="mt-5 grid grid-cols-2 gap-y-8 md:grid-cols-4">
        <div v-for="s in stats" :key="s.label">
          <dt class="text-xs text-ink-soft">{{ s.label }}</dt>
          <dd class="mt-2 text-4xl font-semibold tracking-tight tabular" :class="s.tone">{{ s.value }}</dd>
        </div>
      </dl>
    </section>

    <!-- tables -->
    <section class="grid gap-6 lg:grid-cols-2">
      <div class="card overflow-hidden">
        <div class="border-b border-line px-7 py-5">
          <h3 class="text-sm font-semibold">Seata 全局事务</h3>
          <p class="mt-0.5 text-xs text-ink-mute">seata_tx_audit · 点击展开回滚镜像摘要</p>
        </div>
        <ul class="max-h-[460px] divide-y divide-line overflow-y-auto">
          <li
            v-for="a in state.seataAudits"
            :key="a.id"
            class="cursor-pointer px-7 py-4 transition hover:bg-paper/70"
            @click="expandedAudit = expandedAudit === a.id ? null : a.id"
          >
            <div class="flex items-center justify-between gap-4">
              <span class="font-mono text-[13px]">{{ a.orderNo }}</span>
              <span class="flex items-center gap-3 text-xs">
                <span class="text-ink-mute tabular">{{ a.latencyMs }}ms</span>
                <span class="flex items-center gap-1.5" :class="a.txStatus === 'ROLLED_BACK' ? 'font-medium text-brick' : 'text-ink-soft'">
                  <span class="h-1.5 w-1.5 rounded-full" :class="a.txStatus === 'ROLLED_BACK' ? 'bg-brick' : 'bg-sage'" />
                  {{ a.txStatus === 'ROLLED_BACK' ? '已回滚' : '已提交' }}
                </span>
                <ChevronDown
                  class="h-3.5 w-3.5 text-ink-mute transition-transform duration-200"
                  :class="expandedAudit === a.id ? 'rotate-180' : ''"
                />
              </span>
            </div>
            <p class="mt-1.5 truncate font-mono text-[11px] text-ink-mute">XID {{ a.xid }} · 座位 {{ a.seatIds }}</p>
            <p
              v-if="expandedAudit === a.id"
              class="mt-3 rounded-xl bg-paper px-3.5 py-2.5 text-xs leading-relaxed text-ink-soft"
            >
              {{ a.undoSummary }}
            </p>
          </li>
          <li v-if="!state.seataAudits.length" class="px-7 py-12 text-center text-sm text-ink-mute">暂无记录</li>
        </ul>
      </div>

      <div class="card overflow-hidden">
        <div class="border-b border-line px-7 py-5">
          <h3 class="text-sm font-semibold">RabbitMQ 消息轨迹</h3>
          <p class="mt-0.5 text-xs text-ink-mute">mq_event_log · 点击展开路由与消费详情</p>
        </div>
        <ul class="max-h-[460px] divide-y divide-line overflow-y-auto">
          <li
            v-for="m in state.mqLogs"
            :key="m.id"
            class="cursor-pointer px-7 py-4 transition hover:bg-paper/70"
            @click="expandedMq = expandedMq === m.id ? null : m.id"
          >
            <div class="flex items-center justify-between gap-4">
              <span class="text-sm font-medium">{{ mqEventLabel[m.eventType] ?? m.eventType }}</span>
              <span class="flex items-center gap-2.5 text-xs text-ink-mute">
                <span class="tabular">{{ shortTime(m.createdAt) }}</span>
                <ChevronDown
                  class="h-3.5 w-3.5 transition-transform duration-200"
                  :class="expandedMq === m.id ? 'rotate-180' : ''"
                />
              </span>
            </div>
            <p class="mt-1.5 truncate font-mono text-[11px] text-ink-mute">
              {{ m.orderNo }} · {{ m.exchangeName }} · {{ m.status }}
            </p>
            <p
              v-if="expandedMq === m.id"
              class="mt-3 rounded-xl bg-paper px-3.5 py-2.5 text-xs leading-relaxed text-ink-soft"
            >
              <span class="block font-mono text-[11px] text-ink-mute">routingKey: {{ m.routingKey }}</span>
              <span class="mt-1 block">{{ m.payloadSummary }}</span>
            </p>
          </li>
          <li v-if="!state.mqLogs.length" class="px-7 py-12 text-center text-sm text-ink-mute">暂无记录</li>
        </ul>
      </div>
    </section>

    <!-- AI Multi-Agent Trace Observability -->
    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-line px-7 py-5">
        <div>
          <h3 class="text-sm font-semibold">AI Multi-Agent 主子图路由与安全审计轨迹</h3>
          <p class="mt-0.5 text-xs text-ink-mute">
            agent_trace_log (:8204) · 记录意图拆解、子图调度、RAG 条款引用、注入拦截与人工确认耗时
          </p>
        </div>
        <div v-if="state.agentTraceSummary" class="flex flex-wrap gap-5 text-xs tabular">
          <span>总调用 <strong class="text-ink">{{ state.agentTraceSummary.totalRuns }}</strong></span>
          <span>安全拦截 <strong class="text-brick">{{ state.agentTraceSummary.guardBlockedCount }}</strong></span>
          <span>确认挂起 <strong class="text-gold">{{ state.agentTraceSummary.interruptedCount }}</strong></span>
          <span>平均耗时 <strong class="text-ink">{{ state.agentTraceSummary.avgLatencyMs }}ms</strong></span>
        </div>
      </div>

      <ul class="max-h-[380px] divide-y divide-line overflow-y-auto">
        <li
          v-for="t in state.agentTraces"
          :key="t.id"
          class="cursor-pointer px-7 py-4 transition hover:bg-paper/70"
          @click="expandedAgent = expandedAgent === t.id ? null : t.id"
        >
          <div class="flex items-center justify-between gap-4">
            <div class="flex min-w-0 items-center gap-2.5">
              <span class="font-mono text-xs text-ink-mute">{{ t.traceId }}</span>
              <span class="truncate text-sm font-medium text-ink">{{ t.userMessage }}</span>
            </div>
            <span class="flex shrink-0 items-center gap-3 text-xs">
              <span class="text-ink-mute tabular">{{ t.latencyMs }}ms</span>
              <span
                class="rounded-full px-2 py-0.5 text-[11px] font-medium"
                :class="
                  t.guardBlocked
                    ? 'bg-brick-soft text-brick'
                    : t.interrupted
                      ? 'bg-gold-soft text-gold'
                      : 'bg-sage-soft text-sage'
                "
              >
                {{ t.status }}
              </span>
              <ChevronDown
                class="h-3.5 w-3.5 text-ink-mute transition-transform duration-200"
                :class="expandedAgent === t.id ? 'rotate-180' : ''"
              />
            </span>
          </div>
          <p class="mt-1.5 truncate font-mono text-[11px] text-ink-mute">
            用户 {{ t.username }} · 意图 [{{ t.intents.join(' → ') || 'guard' }}] · 子图 [{{ t.subgraphs.join(' → ') }}]
            <template v-if="t.citations.length"> · 引用 {{ t.citations.join(', ') }}</template>
          </p>
          <div
            v-if="expandedAgent === t.id"
            class="mt-3 space-y-1.5 rounded-xl bg-paper px-3.5 py-2.5 text-xs leading-relaxed text-ink-soft"
          >
            <p><strong class="text-ink">回复摘要：</strong>{{ t.replyPreview || '—' }}</p>
            <p class="font-mono text-[11px] text-ink-mute">
              步骤明细：{{ t.toolSteps.map(s => `${s.agent || ''}:${s.tool}`).join(' → ') || '无工具调用' }}
            </p>
          </div>
        </li>
        <li v-if="!state.agentTraces.length" class="px-7 py-10 text-center text-sm text-ink-mute">
          暂无 Agent 轨迹记录（启动 ticket-agent :8204 并在右下角对话后自动采集）
        </li>
      </ul>
    </section>

    <!-- points & context -->
    <section class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div class="card overflow-hidden">
        <div class="border-b border-line px-7 py-5">
          <h3 class="text-sm font-semibold">积分流水</h3>
          <p class="mt-0.5 text-xs text-ink-mute">points_log · 唯一索引 (order_no, biz_type) 防止重复发放</p>
        </div>
        <ul class="divide-y divide-line">
          <li v-for="p in state.pointsLogs" :key="p.id" class="flex items-center justify-between gap-4 px-7 py-4">
            <div class="min-w-0">
              <p class="truncate text-sm">{{ p.remark }}</p>
              <p class="mt-1 font-mono text-[11px] text-ink-mute">{{ p.orderNo }}</p>
            </div>
            <div class="shrink-0 text-right tabular">
              <p class="text-sm font-medium text-sage">+{{ p.pointsDelta }}</p>
              <p class="mt-1 text-[11px] text-ink-mute">余额 {{ p.balanceAfter }}</p>
            </div>
          </li>
          <li v-if="!state.pointsLogs.length" class="px-7 py-12 text-center text-sm text-ink-mute">暂无流水</li>
        </ul>
      </div>

      <div class="card p-7">
        <h3 class="text-sm font-semibold">当前请求上下文</h3>
        <p class="mt-0.5 text-xs text-ink-mute">由网关注入，下游写入 ThreadLocal</p>
        <dl class="mt-5 space-y-3 text-sm">
          <div class="flex justify-between gap-4"><dt class="text-ink-soft">X-User-Id</dt><dd class="font-mono">{{ state.context.userId }}</dd></div>
          <div class="flex justify-between gap-4"><dt class="text-ink-soft">X-Username</dt><dd class="font-mono">{{ state.context.username }}</dd></div>
          <div class="flex justify-between gap-4"><dt class="text-ink-soft">X-User-Role</dt><dd class="font-mono text-brick">{{ state.context.role }}</dd></div>
          <div class="flex justify-between gap-4"><dt class="text-ink-soft">X-Trace-Id</dt><dd class="truncate font-mono">{{ state.context.traceId }}</dd></div>
        </dl>

        <h3 class="mt-8 border-t border-line pt-6 text-sm font-semibold">服务节点</h3>
        <ul class="mt-4 space-y-3 text-sm">
          <li v-for="s in services" :key="s.name" class="flex items-center justify-between">
            <span class="flex items-center gap-2">
              <span class="h-1.5 w-1.5 rounded-full bg-sage" />
              <span class="font-mono text-[13px]">{{ s.name }}</span>
            </span>
            <span class="font-mono text-xs text-ink-mute">:{{ s.port }}</span>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>
