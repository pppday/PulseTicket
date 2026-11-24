<script setup lang="ts">
/**
 * 【运维专属 · 演出目录与阿里云 OSS 海报管理弹窗（ShowManageModal）】
 * 支持管理员新增/编辑演出与 48 座场次、上下架、调整展示排序，并直接上传海报到阿里云 OSS 或从历史图库挑选。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  ArrowDown,
  ArrowUp,
  ArrowUpToLine,
  Check,
  CloudUpload,
  Database,
  ExternalLink,
  FolderOpen,
  Image as ImageIcon,
  Plus,
  Power,
  RefreshCw,
  Sparkles,
  Trash2,
  X
} from 'lucide-vue-next'
import { api } from '../api'
import { loadCatalog, refreshAll, selectShow, toast } from '../store'
import type { AdminShowOptions, CatalogItem, OssBucketInfo, ShowInfo, VenueOptionItem } from '../types'
import { categoryLabel, posterTheme } from '../utils'

const props = defineProps<{
  open: boolean
  initialShow?: ShowInfo | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const adminCatalog = ref<CatalogItem[]>([])
const ossInfo = ref<OssBucketInfo | null>(null)
const dbOptions = ref<AdminShowOptions>({
  cities: ['上海', '北京', '深圳', '杭州', '广州', '成都'],
  venues: [
    { city: '上海', venueName: '梅赛德斯-奔驰文化中心 · 主场馆', hallName: '中央全息环形演艺厅', categoryHint: 'CONCERT' },
    { city: '北京', venueName: '天桥艺术中心 · 大剧场', hallName: '1600座歌剧大剧场', categoryHint: 'MUSICAL' },
    { city: '深圳', venueName: '万象城 IMAX GT 巨幕影厅', hallName: '1号 激光 IMAX GT 巨幕厅', categoryHint: 'IMAX_MOVIE' },
    { city: '杭州', venueName: '大麦 66 Livehouse', hallName: '全息声学主舞台', categoryHint: 'LIVEHOUSE' }
  ],
  halls: ['中央全息环形演艺厅', '1600座歌剧大剧场', '1号 激光 IMAX GT 巨幕厅', '全息声学主舞台'],
  badges: ['年度必看爆款', '官方直售 · 电子票', '全球巡演驻场原版', '1.43:1 满画幅顶配', '户外草坪微醺现场']
})

const customCityMode = ref(false)
const customVenueMode = ref(false)
const customHallMode = ref(false)
const customBadgeMode = ref(false)

const uploading = ref(false)
const saving = ref(false)
const reordering = ref(false)
const loadingList = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

function defaultStartTimeLocal(daysAhead = 14): string {
  const d = new Date(Date.now() + daysAhead * 86400000)
  d.setHours(19, 30, 0, 0)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const form = reactive({
  id: 0 as number,
  title: '',
  subtitle: '',
  category: 'CONCERT',
  city: '上海',
  venueName: '梅赛德斯-奔驰文化中心 · 主场馆',
  posterUrl: '',
  badgeText: '年度必看爆款',
  rating: 9.8,
  minPrice: 280,
  maxPrice: 980,
  hotScore: 9950,
  targetRank: 1,
  status: 'ON_SALE',
  description: '',
  sessionTitle: '',
  startTime: defaultStartTimeLocal(),
  hallName: '中央全息环形演艺厅'
})

const categories = [
  { key: 'CONCERT', label: '演唱会' },
  { key: 'MUSICAL', label: '音乐剧' },
  { key: 'IMAX_MOVIE', label: 'IMAX 电影' },
  { key: 'LIVEHOUSE', label: 'Livehouse' }
]

const isNewMode = computed(() => !form.id || form.id <= 0)

// 从数据库字典过滤当前选中城市下的全部真实场馆
const venuesForSelectedCity = computed<VenueOptionItem[]>(() => {
  const list = dbOptions.value.venues.filter(v => v.city === form.city)
  if (list.length > 0) {
    // 确保当前正在编辑的 venueName 也在下拉列表中
    if (form.venueName && !list.some(v => v.venueName === form.venueName)) {
      return [
        ...list,
        { city: form.city, venueName: form.venueName, hallName: form.hallName || '中央全息环形演艺厅' }
      ]
    }
    return list
  }
  return dbOptions.value.venues
})

// 统计每个城市在数据库中的场馆数量
function venueCountForCity(city: string): number {
  return dbOptions.value.venues.filter(v => v.city === city).length
}

const availableCities = computed<string[]>(() => {
  const set = new Set(dbOptions.value.cities)
  if (form.city) set.add(form.city)
  return Array.from(set)
})

const availableHalls = computed<string[]>(() => {
  const set = new Set<string>()
  // 优先展示当前城市关联的演艺厅
  for (const v of venuesForSelectedCity.value) {
    if (v.hallName) set.add(v.hallName)
  }
  for (const h of dbOptions.value.halls) {
    if (h) set.add(h)
  }
  if (form.hallName) set.add(form.hallName)
  return Array.from(set)
})

const availableBadges = computed<string[]>(() => {
  const set = new Set(dbOptions.value.badges)
  if (form.badgeText) set.add(form.badgeText)
  return Array.from(set)
})

const rankOptions = computed(() => {
  const total = Math.max(adminCatalog.value.length + (isNewMode.value ? 1 : 0), 1)
  const arr: { rank: number; label: string; score: number }[] = []
  for (let i = 1; i <= total; i++) {
    const score = Math.max(100, 10000 - (i - 1) * 100)
    arr.push({
      rank: i,
      label: i === 1 ? `第 1 位 · 首页C位置顶 (权重 ${score})` : `第 ${i} 位 · 顺位展示 (权重 ${score})`,
      score
    })
  }
  return arr
})

function onCitySelectChange() {
  const cityVenues = dbOptions.value.venues.filter(v => v.city === form.city)
  if (cityVenues.length > 0) {
    form.venueName = cityVenues[0].venueName
    if (cityVenues[0].hallName) {
      form.hallName = cityVenues[0].hallName
    }
  }
}

function onVenueSelectChange() {
  const matched = dbOptions.value.venues.find(
    v => v.city === form.city && v.venueName === form.venueName
  ) || dbOptions.value.venues.find(v => v.venueName === form.venueName)
  if (matched?.hallName) {
    form.hallName = matched.hallName
  }
}

function onRankSelectChange() {
  const found = rankOptions.value.find(r => r.rank === form.targetRank)
  if (found) {
    form.hotScore = found.score
  }
}

function fillFormFromShow(s?: ShowInfo | null) {
  customCityMode.value = false
  customVenueMode.value = false
  customHallMode.value = false
  customBadgeMode.value = false

  if (!s) {
    const defaultCity = dbOptions.value.cities[0] || '上海'
    const defaultVenue = dbOptions.value.venues.find(v => v.city === defaultCity) || dbOptions.value.venues[0]
    form.id = 0
    form.title = ''
    form.subtitle = '官方正版授权 · 沉浸式声学现场'
    form.category = 'CONCERT'
    form.city = defaultCity
    form.venueName = defaultVenue?.venueName || '梅赛德斯-奔驰文化中心 · 主场馆'
    form.posterUrl = ''
    form.badgeText = dbOptions.value.badges[0] || '年度必看爆款'
    form.rating = 9.8
    form.minPrice = 280
    form.maxPrice = 880
    form.targetRank = 1
    form.hotScore = 10000
    form.status = 'ON_SALE'
    form.description = '融合先锋声学空间与高规格舞美视效，支持在线实时选座与电子票核验。'
    form.sessionTitle = '首演黄金场'
    form.startTime = defaultStartTimeLocal(14)
    form.hallName = defaultVenue?.hallName || '中央全息环形演艺厅'
    return
  }
  form.id = s.id
  form.title = s.title || ''
  form.subtitle = s.subtitle || ''
  form.category = s.category || 'CONCERT'
  form.city = s.city || '上海'
  form.venueName = s.venueName || ''
  form.posterUrl = s.posterUrl || ''
  form.badgeText = s.badgeText || '年度必看爆款'
  form.rating = Number(s.rating || 9.8)
  form.minPrice = Number(s.minPrice || 280)
  form.maxPrice = Number(s.maxPrice || 880)
  form.hotScore = Number(s.hotScore || 9500)
  const idx = adminCatalog.value.findIndex(item => item.show.id === s.id)
  form.targetRank = idx >= 0 ? idx + 1 : 1
  form.status = s.status || 'ON_SALE'
  form.description = s.description || ''
  form.sessionTitle = ''
  form.startTime = defaultStartTimeLocal(14)
  const matchedVenue = dbOptions.value.venues.find(v => v.venueName === s.venueName)
  form.hallName = matchedVenue?.hallName || '中央全息环形演艺厅'
}

async function loadAdminData() {
  loadingList.value = true
  try {
    const [listRes, ossRes, optRes] = await Promise.all([
      api<CatalogItem[]>('/api/show/admin/list'),
      api<OssBucketInfo>('/api/show/admin/oss/list'),
      api<AdminShowOptions>('/api/show/admin/options')
    ])
    if (listRes.code === 200 && listRes.data) {
      adminCatalog.value = listRes.data
    }
    if (ossRes.code === 200 && ossRes.data) {
      ossInfo.value = ossRes.data
    }
    if (optRes.code === 200 && optRes.data) {
      dbOptions.value = optRes.data
    }
    if (form.id > 0) {
      const idx = adminCatalog.value.findIndex(item => item.show.id === form.id)
      if (idx >= 0) form.targetRank = idx + 1
    }
  } finally {
    loadingList.value = false
  }
}

watch(
  () => [props.open, props.initialShow] as const,
  async ([isOpen, initShow]) => {
    if (isOpen) {
      await loadAdminData()
      fillFormFromShow(initShow)
    }
  },
  { immediate: true }
)

onMounted(() => {
  if (props.open) loadAdminData()
})

function triggerFileSelect() {
  fileInput.value?.click()
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await uploadFile(file)
  input.value = ''
}

async function onDrop(e: DragEvent) {
  e.preventDefault()
  const file = e.dataTransfer?.files?.[0]
  if (file) await uploadFile(file)
}

async function uploadFile(file: File) {
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const res = await api<{ url: string; objectKey: string; size: number }>('/api/show/admin/oss/upload', {
      method: 'POST',
      body: fd
    })
    if (res.code === 200 && res.data?.url) {
      form.posterUrl = res.data.url
      toast('success', '阿里云 OSS 直传成功', `已存入 ticket-ppday: ${res.data.objectKey}`)
      await loadAdminData()
    } else {
      toast('error', 'OSS 上传失败', res.message)
    }
  } finally {
    uploading.value = false
  }
}

async function moveShowOrder(index: number, action: 'TOP' | 'UP' | 'DOWN') {
  if (reordering.value) return
  const list = [...adminCatalog.value]
  if (index < 0 || index >= list.length) return

  let targetIndex = index
  if (action === 'TOP') targetIndex = 0
  else if (action === 'UP') targetIndex = index - 1
  else if (action === 'DOWN') targetIndex = index + 1

  if (targetIndex < 0 || targetIndex >= list.length || targetIndex === index) return

  const [moved] = list.splice(index, 1)
  list.splice(targetIndex, 0, moved)

  reordering.value = true
  try {
    const orderedIds = list.map(item => item.show.id)
    const res = await api<CatalogItem[]>('/api/show/admin/reorder', {
      method: 'POST',
      body: JSON.stringify({ orderedIds })
    })
    if (res.code === 200 && res.data) {
      adminCatalog.value = res.data
      await Promise.all([loadCatalog(), refreshAll()])
      const actionText = action === 'TOP' ? '已置顶至第 1 位' : action === 'UP' ? '已上移一位' : '已下移一位'
      toast('success', '演出排序已更新', `「${moved.show.title}」${actionText}`)
      if (form.id > 0) {
        const current = adminCatalog.value.find(c => c.show.id === form.id)
        const idx = adminCatalog.value.findIndex(c => c.show.id === form.id)
        if (current) {
          form.hotScore = Number(current.show.hotScore || 9500)
          form.targetRank = idx + 1
        }
      }
    } else {
      toast('error', '排序保存失败', res.message)
    }
  } finally {
    reordering.value = false
  }
}

async function saveShow() {
  if (!form.title.trim()) {
    toast('info', '请填写演出名称')
    return
  }
  saving.value = true
  try {
    const res = await api<{ show: ShowInfo; isNew: boolean }>('/api/show/admin/save', {
      method: 'POST',
      body: JSON.stringify({
        id: form.id || null,
        title: form.title,
        subtitle: form.subtitle,
        category: form.category,
        city: form.city,
        venueName: form.venueName,
        posterUrl: form.posterUrl,
        badgeText: form.badgeText,
        rating: form.rating,
        minPrice: form.minPrice,
        maxPrice: form.maxPrice,
        hotScore: form.hotScore,
        status: form.status,
        description: form.description,
        sessionTitle: form.sessionTitle,
        startTime: form.startTime,
        hallName: form.hallName
      })
    })
    if (res.code === 200 && res.data?.show) {
      const savedId = res.data.show.id
      // 如果管理员在下拉框选择了特定展示顺位，同步执行精确重排
      const currentIds = adminCatalog.value.map(c => c.show.id).filter(id => id !== savedId)
      const insertIdx = Math.max(0, Math.min(form.targetRank - 1, currentIds.length))
      currentIds.splice(insertIdx, 0, savedId)
      await api('/api/show/admin/reorder', {
        method: 'POST',
        body: JSON.stringify({ orderedIds: currentIds })
      })

      toast('success', res.data.isNew ? '新演出已发布上架' : '演出与排序已更新', res.message)
      await Promise.all([loadCatalog(), loadAdminData(), refreshAll()])
      fillFormFromShow(res.data.show)
      if (res.data.show.status !== 'OFF_SHELF') {
        await selectShow(savedId)
      }
      emit('close')
    } else {
      toast('error', '保存失败', res.message)
    }
  } finally {
    saving.value = false
  }
}

async function toggleStatus(item: ShowInfo) {
  const nextStatus = item.status === 'OFF_SHELF' ? 'ON_SALE' : 'OFF_SHELF'
  const res = await api<ShowInfo>(`/api/show/admin/status/${item.id}`, {
    method: 'POST',
    body: JSON.stringify({ status: nextStatus })
  })
  if (res.code === 200) {
    toast('success', nextStatus === 'ON_SALE' ? '演出已重新上架' : '演出已下架隐藏', res.message)
    await Promise.all([loadCatalog(), loadAdminData(), refreshAll()])
    if (form.id === item.id && res.data) {
      form.status = res.data.status || nextStatus
    }
  } else {
    toast('error', '状态切换失败', res.message)
  }
}

async function removeShow(item: ShowInfo) {
  const res = await api(`/api/show/admin/${item.id}`, { method: 'DELETE' })
  if (res.code === 200) {
    toast('success', '演出已删除', res.message)
    await Promise.all([loadCatalog(), loadAdminData(), refreshAll()])
    if (form.id === item.id) {
      fillFormFromShow(null)
    }
  } else {
    toast('info', '无法直接硬删除', res.message)
  }
}
</script>

<template>
  <Transition
    enter-active-class="transition duration-200"
    enter-from-class="opacity-0"
    leave-active-class="transition duration-150"
    leave-to-class="opacity-0"
  >
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 p-4 backdrop-blur-sm"
      @click.self="emit('close')"
    >
      <div class="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-[28px] bg-white shadow-float">
        <!-- Modal Header -->
        <div class="flex flex-wrap items-center justify-between gap-4 border-b border-line bg-paper px-7 py-5">
          <div>
            <div class="flex items-center gap-2">
              <span class="rounded-full bg-brick-soft px-2.5 py-0.5 text-[11px] font-semibold text-brick">
                ADMIN · 阿里云 OSS 动态演艺与排序工作台
              </span>
              <span class="font-mono text-xs text-ink-mute">
                Bucket: {{ ossInfo?.bucket || 'ticket-ppday' }} · {{ ossInfo?.region || '华北2 (北京)' }}
              </span>
            </div>
            <h3 class="mt-1.5 text-lg font-semibold text-ink">
              动态演出管理、展示顺位排序与阿里云 OSS 海报云存储
            </h3>
          </div>

          <div class="flex items-center gap-2.5">
            <button
              type="button"
              class="btn-ghost h-9 px-3.5 text-xs"
              @click="fillFormFromShow(null)"
            >
              <Plus class="h-3.5 w-3.5" />
              新增演出
            </button>
            <button
              type="button"
              class="grid h-9 w-9 place-items-center rounded-full border border-line bg-white text-ink-soft transition hover:border-ink/40 hover:text-ink"
              @click="emit('close')"
            >
              <X class="h-4 w-4" />
            </button>
          </div>
        </div>

        <!-- Modal Body -->
        <div class="grid flex-1 grid-cols-1 divide-y divide-line overflow-y-auto lg:grid-cols-[370px_1fr] lg:divide-x lg:divide-y-0">
          <!-- Left Column: Show Sorting Manager + Aliyun OSS Upload & Gallery -->
          <div class="space-y-6 bg-paper/45 p-6">
            <!-- Existing Shows List & Real-time Reorder Controls -->
            <div>
              <div class="flex items-center justify-between">
                <div>
                  <p class="text-xs font-semibold uppercase tracking-wider text-ink">
                    演出顺位与目录管理 ({{ adminCatalog.length }})
                  </p>
                  <p class="mt-0.5 text-[11px] text-ink-mute">
                    支持一键置顶、上下移动调整 C 端展示顺序
                  </p>
                </div>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 text-xs text-ink-soft hover:text-ink"
                  @click="loadAdminData"
                >
                  <RefreshCw class="h-3 w-3" :class="{ 'animate-spin': loadingList || reordering }" />
                  刷新
                </button>
              </div>

              <div class="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
                <div
                  v-for="(item, idx) in adminCatalog"
                  :key="item.show.id"
                  class="group flex items-center justify-between gap-2 rounded-2xl border p-2.5 text-left transition cursor-pointer"
                  :class="
                    form.id === item.show.id
                      ? 'border-ink bg-white shadow-soft'
                      : 'border-line bg-white/75 hover:border-ink/30 hover:bg-white'
                  "
                  @click="fillFormFromShow(item.show)"
                >
                  <div class="flex min-w-0 items-center gap-2.5">
                    <!-- Rank Number Badge -->
                    <span
                      class="grid h-6 w-6 shrink-0 place-items-center rounded-lg font-mono text-[11px] font-bold"
                      :class="
                        idx === 0
                          ? 'bg-brick text-white'
                          : idx < 3
                            ? 'bg-ink text-white'
                            : 'bg-paper text-ink-soft'
                      "
                      :title="`当前排位 #${idx + 1} · 权重 ${item.show.hotScore}`"
                    >
                      {{ idx + 1 }}
                    </span>

                    <div
                      class="relative h-10 w-12 shrink-0 overflow-hidden rounded-lg text-white"
                      :style="
                        item.show.posterUrl
                          ? {
                              backgroundImage: `url('${item.show.posterUrl}')`,
                              backgroundSize: 'cover',
                              backgroundPosition: 'center'
                            }
                          : { background: posterTheme(item.show.id) }
                      "
                    />

                    <div class="min-w-0">
                      <p class="truncate text-xs font-semibold text-ink">{{ item.show.title }}</p>
                      <div class="mt-0.5 flex items-center gap-1.5 text-[11px] text-ink-mute">
                        <span>{{ item.show.city }}</span>
                        <span>·</span>
                        <span class="font-mono text-[10px]">权重 {{ item.show.hotScore }}</span>
                        <span
                          class="rounded px-1.5 py-0.2 text-[10px] font-medium"
                          :class="
                            item.show.status === 'OFF_SHELF'
                              ? 'bg-stone-200 text-stone-600'
                              : 'bg-sage-soft text-sage'
                          "
                        >
                          {{ item.show.status === 'OFF_SHELF' ? '已下架' : '在售' }}
                        </span>
                      </div>
                    </div>
                  </div>

                  <!-- Sorting & Status Action Buttons -->
                  <div class="flex shrink-0 items-center gap-0.5" @click.stop>
                    <button
                      type="button"
                      :disabled="idx === 0 || reordering"
                      class="rounded-lg p-1 text-ink-mute transition hover:bg-paper hover:text-ink disabled:opacity-25"
                      title="一键置顶至第 1 位"
                      @click="moveShowOrder(idx, 'TOP')"
                    >
                      <ArrowUpToLine class="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      :disabled="idx === 0 || reordering"
                      class="rounded-lg p-1 text-ink-mute transition hover:bg-paper hover:text-ink disabled:opacity-25"
                      title="上移一位"
                      @click="moveShowOrder(idx, 'UP')"
                    >
                      <ArrowUp class="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      :disabled="idx === adminCatalog.length - 1 || reordering"
                      class="rounded-lg p-1 text-ink-mute transition hover:bg-paper hover:text-ink disabled:opacity-25"
                      title="下移一位"
                      @click="moveShowOrder(idx, 'DOWN')"
                    >
                      <ArrowDown class="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      class="rounded-lg p-1 text-ink-mute transition hover:bg-paper hover:text-ink"
                      :title="item.show.status === 'OFF_SHELF' ? '点击上架' : '点击下架'"
                      @click="toggleStatus(item.show)"
                    >
                      <Power class="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      class="rounded-lg p-1 text-ink-mute transition hover:bg-brick-soft hover:text-brick"
                      title="删除无订单的新演出"
                      @click="removeShow(item.show)"
                    >
                      <Trash2 class="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <!-- OSS Poster Preview & Direct Upload -->
            <div class="border-t border-line pt-5">
              <p class="text-xs font-semibold uppercase tracking-wider text-ink-mute">
                阿里云 OSS 海报直传
              </p>

              <!-- Live Poster Card Preview -->
              <div
                class="relative mt-3 flex aspect-[4/3] flex-col justify-between overflow-hidden rounded-2xl border border-line p-4 text-white shadow-soft"
                :style="
                  form.posterUrl
                    ? {
                        backgroundImage: `linear-gradient(180deg, rgba(20,19,17,0.42) 0%, rgba(20,19,17,0.18) 45%, rgba(20,19,17,0.82) 100%), url('${form.posterUrl}')`,
                        backgroundSize: 'cover',
                        backgroundPosition: 'center',
                        backgroundRepeat: 'no-repeat'
                      }
                    : { background: posterTheme(form.id || 1) }
                "
                @dragover.prevent
                @drop="onDrop"
              >
                <div class="relative flex items-center justify-between text-[11px] tracking-wider text-white/85">
                  <span>{{ categoryLabel[form.category] || form.category }}</span>
                  <span class="rounded-full bg-black/40 px-2 py-0.5 font-mono text-[10px] backdrop-blur-sm">
                    {{ form.posterUrl ? 'OSS 已托管' : '默认主题' }}
                  </span>
                </div>

                <div class="relative">
                  <p class="line-clamp-2 text-base font-semibold leading-snug">
                    {{ form.title || '新演出海报实时预览' }}
                  </p>
                  <p class="mt-1 truncate text-[11px] text-white/75">
                    {{ form.city }} · {{ form.venueName }}
                  </p>
                </div>
              </div>

              <input
                ref="fileInput"
                type="file"
                accept="image/jpeg,image/png,image/webp,image/svg+xml,image/gif"
                class="hidden"
                @change="onFileChange"
              />

              <button
                type="button"
                :disabled="uploading"
                class="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-ink/35 bg-white px-4 py-3 text-xs font-semibold text-ink transition hover:border-ink hover:bg-paper disabled:opacity-50"
                @click="triggerFileSelect"
              >
                <CloudUpload class="h-4 w-4 text-brick" />
                {{ uploading ? '正在上传至 ticket-ppday.oss-cn-beijing...' : '选择本地图片上传至阿里云 OSS' }}
              </button>

              <!-- OSS Bucket Gallery -->
              <div v-if="ossInfo?.files?.length" class="mt-4">
                <p class="flex items-center gap-1.5 text-[11px] font-medium text-ink-soft">
                  <FolderOpen class="h-3.5 w-3.5 text-gold" />
                  ticket-ppday 云端图库（点击直接选用）：
                </p>
                <div class="mt-2 grid grid-cols-4 gap-2">
                  <button
                    v-for="f in ossInfo.files.slice(0, 8)"
                    :key="f.objectKey"
                    type="button"
                    class="group relative aspect-[4/3] overflow-hidden rounded-lg border transition"
                    :class="
                      form.posterUrl === f.url
                        ? 'border-brick ring-2 ring-brick/30'
                        : 'border-line hover:border-ink/40'
                    "
                    :title="f.objectKey"
                    @click="form.posterUrl = f.url"
                  >
                    <img :src="f.url" class="h-full w-full object-cover" alt="" />
                    <span
                      v-if="form.posterUrl === f.url"
                      class="absolute right-1 top-1 grid h-4 w-4 place-items-center rounded-full bg-brick text-white"
                    >
                      <Check class="h-2.5 w-2.5" />
                    </span>
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- Right Column: Dynamic Show, Database Dropdowns & Session Editor -->
          <div class="p-6 md:p-8">
            <div class="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-4">
              <div>
                <span
                  class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium"
                  :class="isNewMode ? 'bg-sage-soft text-sage' : 'bg-paper text-ink'"
                >
                  <Sparkles class="h-3 w-3" />
                  {{ isNewMode ? '当前模式：发布全新演出 + 自动生成 48 座选座厅' : `当前模式：编辑演出 #${form.id}` }}
                </span>
              </div>
              <a
                v-if="form.posterUrl"
                :href="form.posterUrl"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-1 font-mono text-[11px] text-brick hover:underline"
              >
                <ImageIcon class="h-3 w-3" />
                查看 OSS 原图链接
                <ExternalLink class="h-3 w-3" />
              </a>
            </div>

            <div class="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div class="sm:col-span-2">
                <label class="block text-xs font-medium text-ink-soft">演出主标题 *</label>
                <input
                  v-model="form.title"
                  type="text"
                  placeholder="例如：「星际回响 · PulseLive」2026 全息交响电音巡演"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2.5 text-sm text-ink outline-none transition focus:border-ink focus:bg-white"
                />
              </div>

              <div class="sm:col-span-2">
                <label class="block text-xs font-medium text-ink-soft">副标题 / 演出亮点</label>
                <input
                  v-model="form.subtitle"
                  type="text"
                  placeholder="例如：沉浸式 360° 环形激光穹顶 · 柏林电子乐团联袂呈现"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink focus:bg-white"
                />
              </div>

              <div>
                <label class="block text-xs font-medium text-ink-soft">演出类别</label>
                <select
                  v-model="form.category"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink focus:bg-white"
                >
                  <option v-for="c in categories" :key="c.key" :value="c.key">
                    {{ c.label }} ({{ c.key }})
                  </option>
                </select>
              </div>

              <div>
                <label class="block text-xs font-medium text-ink-soft">上架状态</label>
                <select
                  v-model="form.status"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink focus:bg-white"
                >
                  <option value="ON_SALE">ON_SALE · 正在热售（C端展示）</option>
                  <option value="OFF_SHELF">OFF_SHELF · 下架归档（C端隐藏）</option>
                </select>
              </div>

              <!-- Real Database Dropdown: City -->
              <div>
                <div class="flex items-center justify-between">
                  <label class="flex items-center gap-1 text-xs font-medium text-ink-soft">
                    <Database class="h-3 w-3 text-sage" />
                    所在城市（数据库下拉）
                  </label>
                  <button
                    type="button"
                    class="text-[11px] text-ink-mute hover:text-ink"
                    @click="customCityMode = !customCityMode"
                  >
                    {{ customCityMode ? '返回下拉选择' : '+ 自定义新城市' }}
                  </button>
                </div>
                <input
                  v-if="customCityMode"
                  v-model="form.city"
                  type="text"
                  placeholder="输入新城市名称（保存后自动写入数据库字典）"
                  class="mt-1.5 w-full rounded-xl border border-line bg-white px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink"
                />
                <select
                  v-else
                  v-model="form.city"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink focus:bg-white"
                  @change="onCitySelectChange"
                >
                  <option v-for="c in availableCities" :key="c" :value="c">
                    {{ c }}（{{ venueCountForCity(c) }} 座关联场馆）
                  </option>
                </select>
              </div>

              <!-- Real Database Dropdown: Venue (Cascaded by City) -->
              <div>
                <div class="flex items-center justify-between">
                  <label class="flex items-center gap-1 text-xs font-medium text-ink-soft">
                    <Database class="h-3 w-3 text-sage" />
                    场馆名称（{{ form.city }}真实场馆库）
                  </label>
                  <button
                    type="button"
                    class="text-[11px] text-ink-mute hover:text-ink"
                    @click="customVenueMode = !customVenueMode"
                  >
                    {{ customVenueMode ? '返回下拉选择' : '+ 自定义新场馆' }}
                  </button>
                </div>
                <input
                  v-if="customVenueMode"
                  v-model="form.venueName"
                  type="text"
                  placeholder="输入新场馆名称（保存后自动写入数据库字典）"
                  class="mt-1.5 w-full rounded-xl border border-line bg-white px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink"
                />
                <select
                  v-else
                  v-model="form.venueName"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink focus:bg-white"
                  @change="onVenueSelectChange"
                >
                  <option v-for="v in venuesForSelectedCity" :key="v.venueName" :value="v.venueName">
                    {{ v.venueName }}
                  </option>
                </select>
              </div>

              <div>
                <label class="block text-xs font-medium text-ink-soft">看台最低票价 (¥)</label>
                <input
                  v-model.number="form.minPrice"
                  type="number"
                  min="1"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink tabular outline-none transition focus:border-ink focus:bg-white"
                />
              </div>

              <div>
                <label class="block text-xs font-medium text-ink-soft">VIP 最高票价 (¥)</label>
                <input
                  v-model.number="form.maxPrice"
                  type="number"
                  min="1"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink tabular outline-none transition focus:border-ink focus:bg-white"
                />
              </div>

              <!-- Real Database Dropdown: Badge Text -->
              <div>
                <div class="flex items-center justify-between">
                  <label class="flex items-center gap-1 text-xs font-medium text-ink-soft">
                    <Database class="h-3 w-3 text-sage" />
                    推荐角标文案
                  </label>
                  <button
                    type="button"
                    class="text-[11px] text-ink-mute hover:text-ink"
                    @click="customBadgeMode = !customBadgeMode"
                  >
                    {{ customBadgeMode ? '返回下拉选择' : '+ 自定义角标' }}
                  </button>
                </div>
                <input
                  v-if="customBadgeMode"
                  v-model="form.badgeText"
                  type="text"
                  placeholder="输入自定义推荐角标"
                  class="mt-1.5 w-full rounded-xl border border-line bg-white px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink"
                />
                <select
                  v-else
                  v-model="form.badgeText"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink focus:bg-white"
                >
                  <option v-for="b in availableBadges" :key="b" :value="b">
                    {{ b }}
                  </option>
                </select>
              </div>

              <!-- Show Display Rank & Rating -->
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="block text-xs font-medium text-ink-soft">展示顺位（排序）</label>
                  <select
                    v-model.number="form.targetRank"
                    class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-2.5 py-2 text-xs text-ink outline-none transition focus:border-ink focus:bg-white"
                    @change="onRankSelectChange"
                  >
                    <option v-for="r in rankOptions" :key="r.rank" :value="r.rank">
                      {{ r.label }}
                    </option>
                  </select>
                </div>
                <div>
                  <label class="block text-xs font-medium text-ink-soft">评分</label>
                  <input
                    v-model.number="form.rating"
                    type="number"
                    step="0.1"
                    min="1"
                    max="10"
                    class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3 py-2 text-sm text-ink tabular outline-none transition focus:border-ink focus:bg-white"
                  />
                </div>
              </div>

              <div class="sm:col-span-2">
                <label class="block text-xs font-medium text-ink-soft">
                  阿里云 OSS 海报 URL（上传后自动填入，也可粘贴外部图片 URL）
                </label>
                <input
                  v-model="form.posterUrl"
                  type="text"
                  placeholder="https://ticket-ppday.oss-cn-beijing.aliyuncs.com/posters/..."
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 font-mono text-xs text-ink outline-none transition focus:border-ink focus:bg-white"
                />
              </div>

              <div class="sm:col-span-2">
                <label class="block text-xs font-medium text-ink-soft">演出详情描述</label>
                <textarea
                  v-model="form.description"
                  rows="2"
                  class="mt-1.5 w-full rounded-xl border border-line bg-paper/40 px-3.5 py-2 text-sm text-ink outline-none transition focus:border-ink focus:bg-white"
                />
              </div>

              <!-- Dynamic Session Generator with Database Hall Dropdown -->
              <div class="rounded-2xl border border-line bg-paper/50 p-4 sm:col-span-2">
                <p class="text-xs font-semibold text-ink">
                  {{ isNewMode ? '首演场次配置（保存时自动初始化 48 个分区座位库存）' : '追加新场次（留空则仅修改上方演出资料，填写则新增一场 48 座排期）' }}
                </p>
                <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div>
                    <label class="block text-[11px] text-ink-mute">开演日期时间（真实落库）</label>
                    <input
                      v-model="form.startTime"
                      type="datetime-local"
                      class="mt-1 w-full rounded-xl border border-line bg-white px-2.5 py-1.5 text-xs text-ink outline-none focus:border-ink"
                    />
                  </div>
                  <div>
                    <label class="block text-[11px] text-ink-mute">场次标题后缀（自动拼接日期）</label>
                    <input
                      v-model="form.sessionTitle"
                      type="text"
                      placeholder="例如：跨年零点专场 / 首演黄金场"
                      class="mt-1 w-full rounded-xl border border-line bg-white px-3 py-1.5 text-xs text-ink outline-none focus:border-ink"
                    />
                  </div>
                  <div>
                    <div class="flex items-center justify-between">
                      <label class="flex items-center gap-1 text-[11px] text-ink-mute">
                        <Database class="h-2.5 w-2.5 text-sage" />
                        演艺厅名称（随场馆联动）
                      </label>
                      <button
                        type="button"
                        class="text-[10px] text-ink-mute hover:text-ink"
                        @click="customHallMode = !customHallMode"
                      >
                        {{ customHallMode ? '返回下拉' : '+ 自定义' }}
                      </button>
                    </div>
                    <input
                      v-if="customHallMode"
                      v-model="form.hallName"
                      type="text"
                      placeholder="输入自定义演艺厅名称"
                      class="mt-1 w-full rounded-xl border border-line bg-white px-3 py-1.5 text-xs text-ink outline-none focus:border-ink"
                    />
                    <select
                      v-else
                      v-model="form.hallName"
                      class="mt-1 w-full rounded-xl border border-line bg-white px-3 py-1.5 text-xs text-ink outline-none focus:border-ink"
                    >
                      <option v-for="h in availableHalls" :key="h" :value="h">
                        {{ h }}
                      </option>
                    </select>
                  </div>
                </div>
              </div>
            </div>

            <div class="mt-6 flex flex-wrap items-center justify-end gap-3 border-t border-line pt-5">
              <button
                type="button"
                class="btn-ghost px-5 py-2.5 text-xs"
                @click="emit('close')"
              >
                关闭窗口
              </button>
              <button
                type="button"
                :disabled="saving"
                class="btn-primary px-6 py-2.5 text-xs"
                @click="saveShow"
              >
                {{ saving ? '正在保存并同步排序...' : isNewMode ? '立即发布新演出并生成座位' : '保存修改与展示顺位' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>
