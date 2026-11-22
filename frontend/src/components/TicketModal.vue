<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import QRCode from 'qrcode'
import { ExternalLink, X } from 'lucide-vue-next'
import { state } from '../store'
import { groupSeatsByZone, money, posterTheme } from '../utils'

const order = computed(() => state.ticket)
const qrSvg = ref('')

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && state.ticket) state.ticket = null
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))

const groupedSeats = computed(() => groupSeatsByZone(order.value?.seatLabelsJson))

/**
 * 微信「扫一扫」仅直接打开 http/https 链接，拦截纯文本多行内容。
 * 线上域名部署时自动使用当前 Origin；本地 localhost 访问时自动替换为局域网 IPv4，
 * 同一 Wi-Fi 下手机微信扫码即可直接打开移动端电子票闸机核验页。
 */
const verifyUrl = computed(() => {
  const o = order.value
  if (!o?.ticketCode) return ''
  const loc = window.location
  const isLocal = loc.hostname === 'localhost' || loc.hostname === '127.0.0.1'
  const host =
    isLocal && state.lanIp && state.lanIp !== '127.0.0.1'
      ? `${state.lanIp}${loc.port ? `:${loc.port}` : ''}`
      : loc.host
  return `${loc.protocol}//${host}/?verify=${encodeURIComponent(o.ticketCode)}`
})

const localPreviewHref = computed(() =>
  order.value?.ticketCode ? `/?verify=${encodeURIComponent(order.value.ticketCode)}` : '#'
)

watch(
  verifyUrl,
  async url => {
    if (!url) {
      qrSvg.value = ''
      return
    }
    qrSvg.value = await QRCode.toString(url, {
      type: 'svg',
      margin: 1,
      errorCorrectionLevel: 'M',
      color: {
        dark: '#1A1917',
        light: '#00000000'
      }
    })
  },
  { immediate: true }
)
</script>

<template>
  <Transition
    enter-active-class="transition duration-200"
    enter-from-class="opacity-0"
    leave-active-class="transition duration-150"
    leave-to-class="opacity-0"
  >
    <div
      v-if="order"
      class="fixed inset-0 z-50 grid place-items-center bg-ink/45 p-4 backdrop-blur-sm"
      @click.self="state.ticket = null"
    >
      <div class="w-full max-w-[400px] overflow-hidden rounded-[28px] bg-white shadow-float">
        <div class="relative p-7 text-white" :style="{ background: posterTheme(order.showId) }">
          <button
            class="absolute right-4 top-4 grid h-8 w-8 place-items-center rounded-full text-white/70 transition hover:bg-white/10 hover:text-white"
            aria-label="关闭电子票"
            @click="state.ticket = null"
          >
            <X class="h-4 w-4" />
          </button>
          <p class="text-[11px] uppercase tracking-[0.16em] text-white/60">电子票 · E-Ticket</p>
          <h3 class="mt-4 pr-6 text-lg font-semibold leading-snug">{{ order.showTitle }}</h3>
          <p class="mt-2 text-xs text-white/75">{{ order.venueName }}</p>
        </div>

        <div class="p-7">
          <dl class="space-y-4 text-sm">
            <div class="flex items-baseline justify-between gap-4">
              <dt class="shrink-0 text-xs text-ink-mute">场次</dt>
              <dd class="text-right font-medium">{{ order.sessionTitle }}</dd>
            </div>

            <div class="flex items-start justify-between gap-4">
              <dt class="shrink-0 pt-0.5 text-xs text-ink-mute">席位</dt>
              <dd class="space-y-1.5 text-right">
                <div v-for="(g, idx) in groupedSeats" :key="idx">
                  <p v-if="g.zone" class="text-xs text-ink-soft">{{ g.zone }}</p>
                  <div class="mt-1 flex flex-wrap justify-end gap-1.5">
                    <span
                      v-for="s in g.seats"
                      :key="s"
                      class="whitespace-nowrap rounded-md bg-paper px-2 py-0.5 text-xs font-medium text-ink tabular"
                    >
                      {{ s }}
                    </span>
                  </div>
                </div>
              </dd>
            </div>

            <div class="flex items-baseline justify-between gap-4">
              <dt class="shrink-0 text-xs text-ink-mute">实付</dt>
              <dd class="text-right font-semibold tabular">¥{{ money(order.totalAmount) }}</dd>
            </div>
          </dl>

          <div class="my-6 border-t border-dashed border-line" />

          <div class="flex flex-col items-center rounded-2xl bg-paper px-5 py-6">
            <div
              v-if="qrSvg"
              class="h-40 w-40 rounded-xl bg-white p-2.5 shadow-sm [&>svg]:h-full [&>svg]:w-full"
              v-html="qrSvg"
            />
            <p class="mt-4 font-mono text-lg font-medium tracking-[0.2em]">{{ order.ticketCode }}</p>
            <p class="mt-1 font-mono text-[11px] text-ink-mute">{{ order.orderNo }}</p>
            <p class="mt-2.5 text-center text-[11px] text-ink-soft">
              微信扫一扫直达 H5 闸机核验页（同局域网或线上域名）
            </p>
            <a
              :href="localPreviewHref"
              target="_blank"
              rel="noopener"
              class="mt-2.5 inline-flex items-center gap-1 rounded-full border border-line bg-white px-3 py-1 text-[11px] font-medium text-ink-soft transition hover:border-ink/35 hover:text-ink"
            >
              预览扫码核验页
              <ExternalLink class="h-3 w-3" />
            </a>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>
