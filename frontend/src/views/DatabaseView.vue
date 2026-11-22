<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { BarChart3, ChevronLeft, ChevronRight, Database, HardDrive, Layers, RotateCw, Search } from 'lucide-vue-next'
import { loadAdminDatabases, probePenetration, state } from '../store'
import { categoryLabel, money, parseList, shortTime } from '../utils'

type DbKey = 'ticket_user' | 'ticket_show' | 'ticket_order'
type TableKey =
  | 'user_account'
  | 'points_log'
  | 'user_message'
  | 'user_undo_log'
  | 'show_info'
  | 'show_session'
  | 'seat_inventory'
  | 'show_undo_log'
  | 'ticket_order'
  | 'seata_tx_audit'
  | 'mq_event_log'
  | 'order_undo_log'

const refreshing = ref(false)
const activeDb = ref<DbKey>('ticket_user')
const activeTable = ref<TableKey>('user_account')
const keyword = ref('')
const seatSessionFilter = ref<number>(0)
const seatStatusFilter = ref<'ALL' | 'SOLD' | 'LOCKED' | 'AVAILABLE'>('ALL')
const orderStatusFilter = ref<'ALL' | 'PAID' | 'UNPAID' | 'CLOSED'>('ALL')
const page = ref(1)
const PAGE_SIZE = 25

onMounted(() => {
  if (!state.userDb || !state.showDb || !state.orderDb) {
    refresh()
  }
})

async function refresh() {
  refreshing.value = true
  try {
    await loadAdminDatabases()
  } finally {
    refreshing.value = false
  }
}

async function runPenetrationAndRefresh() {
  await probePenetration()
  await loadAdminDatabases()
}

interface TableDef {
  key: TableKey
  db: DbKey
  rawName: string
  title: string
  desc: string
}

const tableDefs: TableDef[] = [
  {
    key: 'user_account',
    db: 'ticket_user',
    rawName: 'user_account',
    title: '用户账户与会员主表',
    desc: '存储实名手机号、BCrypt 密码哈希、RBAC 角色权限与可用积分余额'
  },
  {
    key: 'points_log',
    db: 'ticket_user',
    rawName: 'points_log',
    title: '会员积分变动流水表',
    desc: '唯一索引 uk_order_biz (order_no, biz_type) 保障 MQ 消费 100% 幂等'
  },
  {
    key: 'user_message',
    db: 'ticket_user',
    rawName: 'user_message',
    title: '出票通知与站内信表',
    desc: '存储注册欢迎信与出票核验码通知，唯一索引防重投递'
  },
  {
    key: 'user_undo_log',
    db: 'ticket_user',
    rawName: 'undo_log',
    title: 'Seata AT 回滚镜像表 (User)',
    desc: '记录分支事务前后镜像，全局提交后异步清理'
  },
  {
    key: 'show_info',
    db: 'ticket_show',
    rawName: 'show_info',
    title: '演出剧目主信息表',
    desc: '剧目详情主表，配合 Redis 互斥锁、随机 TTL 抖动与空值哨兵防护'
  },
  {
    key: 'show_session',
    db: 'ticket_show',
    rawName: 'show_session',
    title: '演出排期场次表',
    desc: '排期场次与实时总座/可售/锁定/已售汇总计数器'
  },
  {
    key: 'seat_inventory',
    db: 'ticket_show',
    rawName: 'seat_inventory',
    title: '场馆座位实时库存表',
    desc: '全平台 240 个可视化选座席位，Redisson MultiLock + MySQL CAS 双重并发防护'
  },
  {
    key: 'show_undo_log',
    db: 'ticket_show',
    rawName: 'undo_log',
    title: 'Seata AT 回滚镜像表 (Show)',
    desc: '跨服务锁座 RM 分支事务回滚镜像表'
  },
  {
    key: 'ticket_order',
    db: 'ticket_order',
    rawName: 'ticket_order',
    title: '演出选座订单主表',
    desc: '记录锁座订单、Seata XID、防伪电子核验码、延迟秒数与 CAS 版本号'
  },
  {
    key: 'seata_tx_audit',
    db: 'ticket_order',
    rawName: 'seata_tx_audit',
    title: 'Seata 分布式事务审计表',
    desc: '独立 REQUIRES_NEW 事务持久化全局提交与故障回滚轨迹'
  },
  {
    key: 'mq_event_log',
    db: 'ticket_order',
    rawName: 'mq_event_log',
    title: 'RabbitMQ 消息全链路追踪表',
    desc: '追踪死信延迟关单投递、超时自动关单与支付成功 Fanout 广播'
  },
  {
    key: 'order_undo_log',
    db: 'ticket_order',
    rawName: 'undo_log',
    title: 'Seata AT 回滚镜像表 (Order)',
    desc: '订单库分支事务 before/after 镜像表'
  }
]

const dbCards = computed(() => {
  const uMeta = state.userDb?.tablesMeta || []
  const sMeta = state.showDb?.tablesMeta || []
  const oMeta = state.orderDb?.tablesMeta || []
  return [
    {
      key: 'ticket_user' as DbKey,
      name: 'ticket_user',
      title: '用户与会员积分库',
      service: state.userDb?.service || 'user-service (:8201)',
      tone: 'text-brick bg-brick-soft',
      tables: uMeta,
      totalRows: uMeta.reduce((a, b) => a + Number(b.rowCount || 0), 0),
      totalBytes: uMeta.reduce((a, b) => a + Number(b.totalBytes || 0), 0),
      defaultTable: 'user_account' as TableKey
    },
    {
      key: 'ticket_show' as DbKey,
      name: 'ticket_show',
      title: '演出场次与座位库存库',
      service: state.showDb?.service || 'show-service (:8202)',
      tone: 'text-sage bg-sage-soft',
      tables: sMeta,
      totalRows: sMeta.reduce((a, b) => a + Number(b.rowCount || 0), 0),
      totalBytes: sMeta.reduce((a, b) => a + Number(b.totalBytes || 0), 0),
      defaultTable: 'seat_inventory' as TableKey
    },
    {
      key: 'ticket_order' as DbKey,
      name: 'ticket_order',
      title: '票务订单与事务审计库',
      service: state.orderDb?.service || 'order-service (:8203)',
      tone: 'text-gold bg-gold-soft',
      tables: oMeta,
      totalRows: oMeta.reduce((a, b) => a + Number(b.rowCount || 0), 0),
      totalBytes: oMeta.reduce((a, b) => a + Number(b.totalBytes || 0), 0),
      defaultTable: 'ticket_order' as TableKey
    }
  ]
})

const totalClusterRows = computed(() => dbCards.value.reduce((acc, d) => acc + d.totalRows, 0))

function selectDb(db: DbKey) {
  activeDb.value = db
  const card = dbCards.value.find(c => c.key === db)
  if (card) activeTable.value = card.defaultTable
}

function jumpToTable(db: DbKey, rawTableName: string) {
  activeDb.value = db
  const found = tableDefs.find(t => t.db === db && t.rawName === rawTableName)
  if (found) activeTable.value = found.key
}

const currentDbTables = computed(() => tableDefs.filter(t => t.db === activeDb.value))
const currentTableDef = computed(() => tableDefs.find(t => t.key === activeTable.value) || tableDefs[0])

function getTableRowCount(t: TableDef): number {
  const source =
    t.db === 'ticket_user'
      ? state.userDb?.tablesMeta
      : t.db === 'ticket_show'
        ? state.showDb?.tablesMeta
        : state.orderDb?.tablesMeta
  return Number(source?.find(m => m.tableName === t.rawName)?.rowCount ?? 0)
}

watch([activeTable, keyword, seatSessionFilter, seatStatusFilter, orderStatusFilter], () => {
  page.value = 1
})

const rawRows = computed<any[]>(() => {
  switch (activeTable.value) {
    case 'user_account':
      return state.userDb?.userAccounts || []
    case 'points_log':
      return state.userDb?.pointsLogs || []
    case 'user_message':
      return state.userDb?.userMessages || []
    case 'user_undo_log':
      return state.userDb?.undoLogs || []
    case 'show_info':
      return state.showDb?.shows || []
    case 'show_session':
      return state.showDb?.sessions || []
    case 'seat_inventory':
      return state.showDb?.seatInventory || []
    case 'show_undo_log':
      return state.showDb?.undoLogs || []
    case 'ticket_order':
      return state.orderDb?.ticketOrders || []
    case 'seata_tx_audit':
      return state.orderDb?.seataTxAudits || []
    case 'mq_event_log':
      return state.orderDb?.mqEventLogs || []
    case 'order_undo_log':
      return state.orderDb?.undoLogs || []
    default:
      return []
  }
})

const filteredRows = computed<any[]>(() => {
  let list = rawRows.value

  if (activeTable.value === 'seat_inventory') {
    if (seatSessionFilter.value > 0) {
      list = list.filter(r => r.sessionId === seatSessionFilter.value)
    }
    if (seatStatusFilter.value !== 'ALL') {
      list = list.filter(r => r.status === seatStatusFilter.value)
    }
  } else if (activeTable.value === 'ticket_order') {
    if (orderStatusFilter.value !== 'ALL') {
      list = list.filter(r => r.status === orderStatusFilter.value)
    }
  }

  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    list = list.filter(row => JSON.stringify(row).toLowerCase().includes(kw))
  }
  return list
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)))
const pagedRows = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return filteredRows.value.slice(start, start + PAGE_SIZE)
})

function formatKB(bytes: number): string {
  if (!bytes) return '16 KB'
  return `${Math.max(16, Math.round(bytes / 1024))} KB`
}
</script>

<template>
  <div class="space-y-10">
    <!-- Header -->
    <header class="flex flex-wrap items-end justify-between gap-6">
      <div class="max-w-2xl">
        <p class="eyebrow">Multi-Database Live Inspector · ADMIN</p>
        <h1 class="mt-2 text-[28px] font-semibold tracking-tight">分布式三库全量数据展示看板</h1>
        <p class="mt-2.5 text-sm leading-relaxed text-ink-soft">
          遵循微服务 <span class="font-medium text-ink">Database-per-Service</span> 隔离规范，实时检视
          <span class="font-mono text-xs text-ink">ticket_user</span>、
          <span class="font-mono text-xs text-ink">ticket_show</span>、
          <span class="font-mono text-xs text-ink">ticket_order</span>
          三个独立数据库的全部 12 张核心表（共 <strong class="font-mono text-ink tabular">{{ totalClusterRows }}</strong> 条实时记录）与 Redis 缓存键状态。
        </p>
      </div>
      <div class="flex items-center gap-2.5">
        <button class="btn-ghost h-10 text-xs" @click="state.tab = 'sales'">
          <BarChart3 class="h-3.5 w-3.5 text-brick" />
          票房销量面板
        </button>
        <button class="btn-primary h-10" :disabled="refreshing" @click="refresh">
          <RotateCw class="h-3.5 w-3.5" :class="refreshing ? 'animate-spin' : ''" />
          刷新全库数据
        </button>
      </div>
    </header>

    <!-- 1. Three Microservice Databases Topology Cards -->
    <section class="grid gap-5 lg:grid-cols-3">
      <div
        v-for="db in dbCards"
        :key="db.key"
        class="card cursor-pointer p-6 transition"
        :class="activeDb === db.key ? 'border-ink shadow-soft' : 'hover:border-ink/35'"
        @click="selectDb(db.key)"
      >
        <div class="flex items-start justify-between gap-3">
          <div>
            <div class="flex items-center gap-2">
              <Database class="h-4 w-4 text-ink" />
              <span class="font-mono text-base font-semibold text-ink">{{ db.name }}</span>
            </div>
            <p class="mt-1 text-xs text-ink-soft">{{ db.title }} · <span class="font-mono">{{ db.service }}</span></p>
          </div>
          <span class="rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium tabular" :class="db.tone">
            {{ db.totalRows }} 行
          </span>
        </div>

        <div class="mt-5 divide-y divide-line rounded-2xl border border-line bg-paper/50 px-3.5">
          <button
            v-for="tbl in db.tables"
            :key="tbl.tableName"
            type="button"
            class="flex w-full items-center justify-between py-2.5 text-left text-xs transition hover:text-ink"
            @click.stop="jumpToTable(db.key, tbl.tableName)"
          >
            <span class="flex items-center gap-2">
              <span
                class="h-1.5 w-1.5 rounded-full"
                :class="activeDb === db.key && currentTableDef.rawName === tbl.tableName ? 'bg-brick' : 'bg-ink-mute'"
              />
              <span class="font-mono font-medium text-ink">{{ tbl.tableName }}</span>
            </span>
            <span class="font-mono text-[11px] text-ink-mute tabular">
              {{ tbl.rowCount }} rows · {{ formatKB(tbl.totalBytes) }}
            </span>
          </button>
        </div>
      </div>
    </section>

    <!-- 2. Live Redis Cache Keys Inspector -->
    <section class="card p-6">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2.5">
          <HardDrive class="h-4 w-4 text-sage" />
          <h2 class="text-sm font-semibold tracking-tight">Redis 缓存层实时 Key 检视</h2>
          <span class="font-mono text-xs text-ink-mute">127.0.0.1:6379 · 防穿透空值哨兵 / 随机抖动 TTL</span>
        </div>
        <button class="btn-ghost h-8 px-3 text-xs" @click="runPenetrationAndRefresh">
          探测 #99999 空值哨兵并刷新
        </button>
      </div>

      <div class="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div
          v-for="rk in state.showDb?.redisCacheKeys || []"
          :key="rk.key"
          class="rounded-2xl border border-line bg-paper/60 p-3.5"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="truncate font-mono text-[11px] font-medium text-ink">{{ rk.key }}</span>
            <span
              class="shrink-0 rounded-full px-2 py-0.5 font-mono text-[10px] font-medium"
              :class="
                rk.cacheType === 'DATA_JSON'
                  ? 'bg-sage-soft text-sage'
                  : rk.cacheType === 'NULL_SENTINEL'
                    ? 'bg-brick-soft text-brick'
                    : 'bg-stone-100 text-ink-mute'
              "
            >
              {{ rk.cacheType }}
            </span>
          </div>
          <p class="mt-2 font-mono text-xs text-ink-soft tabular">
            <template v-if="rk.exists">TTL {{ rk.ttlSeconds }}s · {{ rk.payloadBytes }} B</template>
            <template v-else>未缓存 (等待首次查询重建)</template>
          </p>
        </div>
      </div>
    </section>

    <!-- 3. Interactive Table Data Explorer -->
    <section class="card overflow-hidden">
      <!-- Database + Table Selector Bar -->
      <div class="border-b border-line bg-paper/50 px-6 py-5">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <!-- DB Tabs -->
          <div class="inline-flex rounded-full bg-ink/[0.06] p-1 text-xs">
            <button
              v-for="db in dbCards"
              :key="db.key"
              type="button"
              class="rounded-full px-4 py-1.5 font-mono transition"
              :class="activeDb === db.key ? 'bg-white font-semibold text-ink shadow-sm' : 'text-ink-soft hover:text-ink'"
              @click="selectDb(db.key)"
            >
              {{ db.name }}
            </button>
          </div>

          <!-- Search Box -->
          <div class="relative w-full sm:w-72">
            <Search class="pointer-events-none absolute left-3.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-mute" />
            <input
              v-model="keyword"
              type="text"
              placeholder="搜索手机号、用户名、订单号、座位…"
              class="h-9 w-full rounded-full border border-line bg-white pl-9 pr-4 text-xs text-ink outline-none transition placeholder:text-ink-mute focus:border-ink"
            />
          </div>
        </div>

        <!-- Table Pills inside active DB -->
        <div class="mt-4 flex flex-wrap items-center gap-2">
          <button
            v-for="t in currentDbTables"
            :key="t.key"
            type="button"
            class="flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-xs transition"
            :class="
              activeTable === t.key
                ? 'border-ink bg-ink text-white'
                : 'border-line bg-white text-ink-soft hover:border-ink/35 hover:text-ink'
            "
            @click="activeTable = t.key"
          >
            <Layers class="h-3 w-3" />
            <span class="font-mono">{{ t.rawName }}</span>
            <span class="opacity-75">· {{ t.title }}</span>
            <span
              class="rounded-full px-1.5 py-0.5 font-mono text-[10px] tabular"
              :class="activeTable === t.key ? 'bg-white/20 text-white' : 'bg-paper text-ink-mute'"
            >
              {{ getTableRowCount(t) }}
            </span>
          </button>
        </div>

        <!-- Contextual Table Info & Secondary Filters -->
        <div class="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line/80 pt-3.5 text-xs">
          <p class="text-ink-soft">
            <span class="font-mono font-semibold text-ink">{{ activeDb }}.{{ currentTableDef.rawName }}</span>
            <span class="mx-2 text-line">|</span>
            {{ currentTableDef.desc }}
          </p>

          <!-- Extra Filters for seat_inventory -->
          <div v-if="activeTable === 'seat_inventory'" class="flex flex-wrap items-center gap-2">
            <select
              v-model.number="seatSessionFilter"
              class="h-8 rounded-full border border-line bg-white px-3 text-xs text-ink outline-none"
            >
              <option :value="0">全部场次 (240 座)</option>
              <option v-for="s in state.showDb?.sessions || []" :key="s.id" :value="s.id">
                #{{ s.id }} · {{ s.sessionTitle }}
              </option>
            </select>
            <div class="inline-flex rounded-full border border-line bg-white p-0.5 text-[11px]">
              <button
                v-for="st in (['ALL', 'SOLD', 'LOCKED', 'AVAILABLE'] as const)"
                :key="st"
                type="button"
                class="rounded-full px-2.5 py-1 transition"
                :class="seatStatusFilter === st ? 'bg-ink font-medium text-white' : 'text-ink-soft'"
                @click="seatStatusFilter = st"
              >
                {{ st === 'ALL' ? '全部状态' : st }}
              </button>
            </div>
          </div>

          <!-- Extra Filters for ticket_order -->
          <div v-else-if="activeTable === 'ticket_order'" class="inline-flex rounded-full border border-line bg-white p-0.5 text-[11px]">
            <button
              v-for="st in (['ALL', 'PAID', 'UNPAID', 'CLOSED'] as const)"
              :key="st"
              type="button"
              class="rounded-full px-2.5 py-1 transition"
              :class="orderStatusFilter === st ? 'bg-ink font-medium text-white' : 'text-ink-soft'"
              @click="orderStatusFilter = st"
            >
              {{ st === 'ALL' ? '全部状态' : st }}
            </button>
          </div>
        </div>
      </div>

      <!-- Data Grid -->
      <div class="overflow-x-auto">
        <!-- 1. ticket_user.user_account -->
        <table v-if="activeTable === 'user_account'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">username</th>
              <th class="px-3 py-3">nickname</th>
              <th class="px-3 py-3">phone (真实手机号)</th>
              <th class="px-3 py-3">role</th>
              <th class="px-3 py-3">vip_level</th>
              <th class="px-3 py-3">points_balance</th>
              <th class="px-3 py-3">total_orders</th>
              <th class="px-3 py-3">password_hash</th>
              <th class="px-5 py-3">created_at</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="u in pagedRows" :key="u.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ u.id }}</td>
              <td class="px-3 py-3.5 font-mono font-semibold text-ink">{{ u.username }}</td>
              <td class="px-3 py-3.5 text-ink">{{ u.nickname }}</td>
              <td class="px-3 py-3.5 font-mono font-semibold text-brick tabular">{{ u.phone }}</td>
              <td class="px-3 py-3.5">
                <span
                  class="rounded-full px-2 py-0.5 font-mono text-[10px] font-medium"
                  :class="u.role === 'ADMIN' ? 'bg-brick-soft text-brick' : 'bg-paper text-ink-soft'"
                >
                  {{ u.role }}
                </span>
              </td>
              <td class="px-3 py-3.5 font-mono text-[11px] text-ink-soft">{{ u.vipLevel }}</td>
              <td class="px-3 py-3.5 font-mono font-medium text-sage tabular">{{ money(u.pointsBalance) }}</td>
              <td class="px-3 py-3.5 font-mono tabular">{{ u.totalOrders }}</td>
              <td class="px-3 py-3.5 font-mono text-[11px] text-ink-mute">{{ u.passwordAlgo }}</td>
              <td class="whitespace-nowrap px-5 py-3.5 font-mono text-[11px] text-ink-mute tabular">{{ u.createdAt }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 2. ticket_user.points_log -->
        <table v-else-if="activeTable === 'points_log'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">user_id</th>
              <th class="px-3 py-3">order_no</th>
              <th class="px-3 py-3">biz_type</th>
              <th class="px-3 py-3">points_delta</th>
              <th class="px-3 py-3">balance_after</th>
              <th class="px-3 py-3">remark</th>
              <th class="px-5 py-3">created_at</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="p in pagedRows" :key="p.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ p.id }}</td>
              <td class="px-3 py-3.5 font-mono tabular">{{ p.userId }}</td>
              <td class="px-3 py-3.5 font-mono font-medium text-ink">{{ p.orderNo }}</td>
              <td class="px-3 py-3.5 font-mono text-[11px] text-ink-soft">{{ p.bizType }}</td>
              <td class="px-3 py-3.5 font-mono font-semibold text-sage tabular">+{{ p.pointsDelta }}</td>
              <td class="px-3 py-3.5 font-mono tabular">{{ p.balanceAfter }}</td>
              <td class="max-w-xs truncate px-3 py-3.5 text-ink-soft">{{ p.remark }}</td>
              <td class="whitespace-nowrap px-5 py-3.5 font-mono text-[11px] text-ink-mute tabular">{{ shortTime(p.createdAt) }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 3. ticket_user.user_message -->
        <table v-else-if="activeTable === 'user_message'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">user_id</th>
              <th class="px-3 py-3">order_no</th>
              <th class="px-3 py-3">msg_type</th>
              <th class="px-3 py-3">title</th>
              <th class="px-3 py-3">content</th>
              <th class="px-3 py-3">is_read</th>
              <th class="px-5 py-3">created_at</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="m in pagedRows" :key="m.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ m.id }}</td>
              <td class="px-3 py-3.5 font-mono tabular">{{ m.userId }}</td>
              <td class="px-3 py-3.5 font-mono text-ink">{{ m.orderNo }}</td>
              <td class="px-3 py-3.5 font-mono text-[11px] text-ink-soft">{{ m.msgType }}</td>
              <td class="max-w-[200px] truncate px-3 py-3.5 font-medium text-ink">{{ m.title }}</td>
              <td class="max-w-[340px] truncate px-3 py-3.5 text-ink-soft" :title="m.content">{{ m.content }}</td>
              <td class="px-3 py-3.5 font-mono">
                <span :class="m.isRead ? 'text-ink-mute' : 'font-medium text-brick'">{{ m.isRead ? '已读(1)' : '未读(0)' }}</span>
              </td>
              <td class="whitespace-nowrap px-5 py-3.5 font-mono text-[11px] text-ink-mute tabular">{{ shortTime(m.createdAt) }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 4. ticket_show.show_info -->
        <table v-else-if="activeTable === 'show_info'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">show_code</th>
              <th class="px-3 py-3">title</th>
              <th class="px-3 py-3">category</th>
              <th class="px-3 py-3">city / venue_name</th>
              <th class="px-3 py-3">price_range</th>
              <th class="px-3 py-3">rating</th>
              <th class="px-3 py-3">hot_score</th>
              <th class="px-5 py-3">status</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="s in pagedRows" :key="s.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ s.id }}</td>
              <td class="px-3 py-3.5 font-mono font-medium text-ink">{{ s.showCode }}</td>
              <td class="max-w-[260px] truncate px-3 py-3.5 font-medium text-ink">{{ s.title }}</td>
              <td class="px-3 py-3.5">{{ categoryLabel[s.category] ?? s.category }}</td>
              <td class="px-3 py-3.5 text-ink-soft">{{ s.city }} · {{ s.venueName }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono tabular">¥{{ s.minPrice }} ~ ¥{{ s.maxPrice }}</td>
              <td class="px-3 py-3.5 font-mono text-gold tabular">{{ s.rating }}</td>
              <td class="px-3 py-3.5 font-mono tabular">{{ s.hotScore }}</td>
              <td class="px-5 py-3.5">
                <span class="rounded-full bg-sage-soft px-2 py-0.5 font-mono text-[10px] font-medium text-sage">{{ s.status }}</span>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- 5. ticket_show.show_session -->
        <table v-else-if="activeTable === 'show_session'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">show_id</th>
              <th class="px-3 py-3">session_code</th>
              <th class="px-3 py-3">session_title</th>
              <th class="px-3 py-3">hall_name</th>
              <th class="px-3 py-3">start_time</th>
              <th class="px-3 py-3">total / avail / locked / sold</th>
              <th class="px-5 py-3">version</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="ss in pagedRows" :key="ss.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono font-semibold text-ink tabular">{{ ss.id }}</td>
              <td class="px-3 py-3.5 font-mono tabular">{{ ss.showId }}</td>
              <td class="px-3 py-3.5 font-mono text-ink-soft">{{ ss.sessionCode }}</td>
              <td class="px-3 py-3.5 font-medium text-ink">{{ ss.sessionTitle }}</td>
              <td class="px-3 py-3.5 text-ink-soft">{{ ss.hallName }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono text-[11px] tabular">{{ shortTime(ss.startTime) }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono tabular">
                {{ ss.totalSeats }} /
                <span class="text-ink">{{ ss.availableSeats }}</span> /
                <span class="text-gold">{{ ss.lockedSeats }}</span> /
                <span class="font-semibold text-sage">{{ ss.soldSeats }}</span>
              </td>
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">v{{ ss.version }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 6. ticket_show.seat_inventory -->
        <table v-else-if="activeTable === 'seat_inventory'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">session_id</th>
              <th class="px-3 py-3">seat_label</th>
              <th class="px-3 py-3">zone_code / zone_name</th>
              <th class="px-3 py-3">price</th>
              <th class="px-3 py-3">status</th>
              <th class="px-3 py-3">locked_order_no</th>
              <th class="px-3 py-3">locked_user_id</th>
              <th class="px-5 py-3">version</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="st in pagedRows" :key="st.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ st.id }}</td>
              <td class="px-3 py-3.5 font-mono tabular">#{{ st.sessionId }}</td>
              <td class="px-3 py-3.5 font-medium text-ink">{{ st.seatLabel }} ({{ st.rowNo }},{{ st.colNo }})</td>
              <td class="px-3 py-3.5 text-ink-soft">
                <span class="font-mono text-[11px] text-ink">{{ st.zoneCode }}</span> · {{ st.zoneName }}
              </td>
              <td class="px-3 py-3.5 font-mono font-medium tabular">¥{{ money(st.price) }}</td>
              <td class="px-3 py-3.5">
                <span
                  class="rounded-full px-2 py-0.5 font-mono text-[10px] font-medium"
                  :class="
                    st.status === 'SOLD'
                      ? 'bg-sage-soft text-sage'
                      : st.status === 'LOCKED'
                        ? 'bg-gold-soft text-gold'
                        : 'bg-paper text-ink-soft'
                  "
                >
                  {{ st.status }}
                </span>
              </td>
              <td class="px-3 py-3.5 font-mono text-[11px] text-ink">{{ st.lockedOrderNo || '—' }}</td>
              <td class="px-3 py-3.5 font-mono text-ink-mute tabular">{{ st.lockedUserId ?? '—' }}</td>
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">v{{ st.version }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 7. ticket_order.ticket_order -->
        <table v-else-if="activeTable === 'ticket_order'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">order_no</th>
              <th class="px-3 py-3">user_id / username</th>
              <th class="px-3 py-3">session / seats</th>
              <th class="px-3 py-3">total_amount</th>
              <th class="px-3 py-3">status / close_reason</th>
              <th class="px-3 py-3">ticket_code</th>
              <th class="px-3 py-3">delay / version</th>
              <th class="px-5 py-3">created_at</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="o in pagedRows" :key="o.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ o.id }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono font-semibold text-ink">{{ o.orderNo }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono">{{ o.userId }} · @{{ o.username }}</td>
              <td class="max-w-[240px] truncate px-3 py-3.5 text-ink-soft">
                #{{ o.sessionId }} · {{ parseList(o.seatLabelsJson).join('、') }}
              </td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono font-semibold text-ink tabular">¥{{ money(o.totalAmount) }}</td>
              <td class="whitespace-nowrap px-3 py-3.5">
                <span
                  class="rounded-full px-2 py-0.5 font-mono text-[10px] font-medium"
                  :class="
                    o.status === 'PAID'
                      ? 'bg-sage-soft text-sage'
                      : o.status === 'UNPAID'
                        ? 'bg-gold-soft text-gold'
                        : 'bg-stone-100 text-ink-soft'
                  "
                >
                  {{ o.status }}
                </span>
                <span v-if="o.closeReason" class="ml-1 font-mono text-[10px] text-ink-mute">{{ o.closeReason }}</span>
              </td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono text-[11px] text-brick">{{ o.ticketCode || '—' }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono text-[11px] text-ink-mute tabular">
                {{ o.delaySeconds }}s · v{{ o.version }}
              </td>
              <td class="whitespace-nowrap px-5 py-3.5 font-mono text-[11px] text-ink-mute tabular">{{ shortTime(o.createdAt) }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 8. ticket_order.seata_tx_audit -->
        <table v-else-if="activeTable === 'seata_tx_audit'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">order_no</th>
              <th class="px-3 py-3">xid</th>
              <th class="px-3 py-3">session / seat_ids</th>
              <th class="px-3 py-3">tx_status</th>
              <th class="px-3 py-3">latency_ms</th>
              <th class="px-3 py-3">undo_summary</th>
              <th class="px-5 py-3">created_at</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="a in pagedRows" :key="a.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ a.id }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono font-medium text-ink">{{ a.orderNo }}</td>
              <td class="max-w-[180px] truncate px-3 py-3.5 font-mono text-[11px] text-ink-mute" :title="a.xid">{{ a.xid }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono">#{{ a.sessionId }} · {{ a.seatIds }}</td>
              <td class="whitespace-nowrap px-3 py-3.5">
                <span
                  class="rounded-full px-2 py-0.5 font-mono text-[10px] font-medium"
                  :class="a.txStatus === 'ROLLED_BACK' ? 'bg-brick-soft text-brick' : 'bg-sage-soft text-sage'"
                >
                  {{ a.txStatus }}
                </span>
              </td>
              <td class="px-3 py-3.5 font-mono tabular">{{ a.latencyMs }}ms</td>
              <td class="max-w-[300px] truncate px-3 py-3.5 text-ink-soft" :title="a.undoSummary">{{ a.undoSummary }}</td>
              <td class="whitespace-nowrap px-5 py-3.5 font-mono text-[11px] text-ink-mute tabular">{{ shortTime(a.createdAt) }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 9. ticket_order.mq_event_log -->
        <table v-else-if="activeTable === 'mq_event_log'" class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">id</th>
              <th class="px-3 py-3">message_id</th>
              <th class="px-3 py-3">order_no</th>
              <th class="px-3 py-3">event_type</th>
              <th class="px-3 py-3">exchange / routing_key</th>
              <th class="px-3 py-3">status</th>
              <th class="px-3 py-3">payload_summary</th>
              <th class="px-5 py-3">created_at</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="m in pagedRows" :key="m.id" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ m.id }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono text-[11px] text-ink-mute">{{ m.messageId }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono font-medium text-ink">{{ m.orderNo }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono text-[11px] text-brick">{{ m.eventType }}</td>
              <td class="whitespace-nowrap px-3 py-3.5 font-mono text-[11px] text-ink-soft">
                {{ m.exchangeName }} → {{ m.routingKey }}
              </td>
              <td class="px-3 py-3.5 font-mono text-[11px] text-sage">{{ m.status }}</td>
              <td class="max-w-[280px] truncate px-3 py-3.5 text-ink-soft" :title="m.payloadSummary">{{ m.payloadSummary }}</td>
              <td class="whitespace-nowrap px-5 py-3.5 font-mono text-[11px] text-ink-mute tabular">{{ shortTime(m.createdAt) }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 10-12. undo_log across any of the 3 DBs -->
        <table v-else class="w-full text-left text-xs">
          <thead class="border-b border-line bg-paper/40 font-mono text-[11px] text-ink-soft">
            <tr>
              <th class="px-5 py-3">branch_id</th>
              <th class="px-3 py-3">xid</th>
              <th class="px-3 py-3">context</th>
              <th class="px-3 py-3">log_status</th>
              <th class="px-3 py-3">rollback_info_bytes</th>
              <th class="px-5 py-3">log_created</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="u in pagedRows" :key="`${u.xid}-${u.branchId}`" class="hover:bg-paper/40">
              <td class="px-5 py-3.5 font-mono tabular">{{ u.branchId }}</td>
              <td class="px-3 py-3.5 font-mono text-ink">{{ u.xid }}</td>
              <td class="px-3 py-3.5 font-mono text-ink-soft">{{ u.context }}</td>
              <td class="px-3 py-3.5 font-mono">{{ u.logStatus }}</td>
              <td class="px-3 py-3.5 font-mono tabular">{{ u.payloadBytes }} B</td>
              <td class="px-5 py-3.5 font-mono text-ink-mute tabular">{{ u.logCreated }}</td>
            </tr>
          </tbody>
        </table>

        <!-- Empty State -->
        <div v-if="!pagedRows.length" class="px-6 py-16 text-center text-sm text-ink-mute">
          <template v-if="activeTable.endsWith('undo_log')">
            当前 <span class="font-mono text-ink">{{ activeDb }}.undo_log</span> 为空（Seata AT 全局事务二阶段提交后已自动异步清理回滚镜像，表示无悬挂未决分支事务）
          </template>
          <template v-else>未匹配到符合条件的数据记录</template>
        </div>
      </div>

      <!-- Pagination Footer -->
      <div class="flex flex-wrap items-center justify-between gap-4 border-t border-line bg-paper/40 px-6 py-3.5 text-xs text-ink-soft">
        <span class="font-mono tabular">
          共 {{ filteredRows.length }} 条记录 · 第 {{ page }} / {{ totalPages }} 页
        </span>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="btn-ghost h-8 px-3 text-xs"
            :disabled="page <= 1"
            @click="page = Math.max(1, page - 1)"
          >
            <ChevronLeft class="h-3.5 w-3.5" />
            上一页
          </button>
          <button
            type="button"
            class="btn-ghost h-8 px-3 text-xs"
            :disabled="page >= totalPages"
            @click="page = Math.min(totalPages, page + 1)"
          >
            下一页
            <ChevronRight class="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
