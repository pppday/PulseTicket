<script setup lang="ts">
/**
 * 【登录与注册页（AuthView）】
 * 负责普通观众/管理员登录以及新用户注册（强制校验 11 位中国大陆手机号），成功后通过 store 持久化 JWT 并进入大厅。
 */
import { ref } from 'vue'
import { Eye, EyeOff, ShieldCheck, Smartphone, Ticket, UserCheck } from 'lucide-vue-next'
import { login, register } from '../store'

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const nickname = ref('')
const phone = ref('')
const showPwd = ref(false)
const submitting = ref(false)
const errorMsg = ref('')

function switchMode(next: 'login' | 'register') {
  mode.value = next
  errorMsg.value = ''
}

function sanitizePhoneInput() {
  phone.value = phone.value.replace(/\D/g, '').slice(0, 11)
}

async function handleSubmit() {
  errorMsg.value = ''
  if (!username.value.trim() || !password.value.trim()) {
    errorMsg.value = '请输入用户名与登录密码'
    return
  }

  if (mode.value === 'register') {
    const cleanPhone = phone.value.trim()
    if (!cleanPhone) {
      errorMsg.value = '手机号为必填项，请输入您的 11 位真实手机号'
      return
    }
    if (!/^1[3-9]\d{9}$/.test(cleanPhone)) {
      errorMsg.value = '请输入正确的 11 位中国大陆手机号'
      return
    }
  }

  submitting.value = true
  try {
    const res =
      mode.value === 'login'
        ? await login(username.value.trim(), password.value.trim())
        : await register({
            username: username.value.trim(),
            password: password.value.trim(),
            phone: phone.value.trim(),
            nickname: nickname.value.trim()
          })
    if (!res.ok) {
      errorMsg.value = res.message || '请求未成功，请重试'
    }
  } finally {
    submitting.value = false
  }
}

async function quickLogin(user: string, pwd: string) {
  errorMsg.value = ''
  username.value = user
  password.value = pwd
  mode.value = 'login'
  submitting.value = true
  try {
    const res = await login(user, pwd)
    if (!res.ok) errorMsg.value = res.message || '快捷登录失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen flex-col justify-between bg-paper px-6 py-8">
    <!-- Minimal editorial brand bar -->
    <header class="mx-auto flex w-full max-w-[1040px] items-center justify-between">
      <div class="flex items-center gap-2.5">
        <span class="grid h-8 w-8 place-items-center rounded-[10px] bg-ink">
          <Ticket class="h-4 w-4 text-paper" :stroke-width="2" />
        </span>
        <span class="text-[17px] font-semibold tracking-tight">PulseTicket</span>
      </div>
      <span class="text-xs text-ink-mute">演出与影院在线选座平台</span>
    </header>

    <!-- Main auth container -->
    <main class="mx-auto my-10 w-full max-w-[440px]">
      <div class="text-center">
        <p class="eyebrow">Account Access</p>
        <h1 class="mt-2.5 text-[28px] font-semibold tracking-tight">
          {{ mode === 'login' ? '欢迎回到现场' : '创建实名观演账号' }}
        </h1>
        <p class="mt-2 text-sm text-ink-soft">
          {{ mode === 'login' ? '登录后即可在线选座、锁座购票与查看电子票夹' : '绑定真实手机号开通实名票夹，即刻获赠 100 观演积分' }}
        </p>
      </div>

      <div class="card mt-8 p-7 shadow-soft md:p-9">
        <!-- Segmented switch -->
        <div class="grid grid-cols-2 rounded-full bg-ink/[0.05] p-1 text-sm">
          <button
            type="button"
            class="rounded-full py-2 transition"
            :class="mode === 'login' ? 'bg-white font-medium text-ink shadow-sm' : 'text-ink-soft hover:text-ink'"
            @click="switchMode('login')"
          >
            账号登录
          </button>
          <button
            type="button"
            class="rounded-full py-2 transition"
            :class="mode === 'register' ? 'bg-white font-medium text-ink shadow-sm' : 'text-ink-soft hover:text-ink'"
            @click="switchMode('register')"
          >
            注册新账号
          </button>
        </div>

        <form class="mt-7 space-y-4" @submit.prevent="handleSubmit">
          <div>
            <label class="block text-xs font-medium text-ink-soft">
              用户名 <span v-if="mode === 'register'" class="text-brick">*</span>
            </label>
            <input
              v-model="username"
              type="text"
              autocomplete="username"
              :placeholder="mode === 'login' ? '输入用户名（如 zhaolan）' : '3~24 位字母、数字或下划线'"
              class="mt-1.5 h-11 w-full rounded-2xl border border-line bg-paper/60 px-4 text-sm text-ink outline-none transition placeholder:text-ink-mute focus:border-ink focus:bg-white"
            />
          </div>

          <template v-if="mode === 'register'">
            <div>
              <label class="flex items-center justify-between text-xs font-medium text-ink-soft">
                <span>手机号 <span class="text-brick">*</span></span>
                <span class="font-mono text-[11px] text-ink-mute">实名出票与主页展示</span>
              </label>
              <div class="relative mt-1.5">
                <Smartphone class="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-mute" />
                <input
                  v-model="phone"
                  type="tel"
                  inputmode="numeric"
                  maxlength="11"
                  required
                  autocomplete="tel"
                  placeholder="请输入 11 位真实中国大陆手机号"
                  class="h-11 w-full rounded-2xl border border-line bg-paper/60 pl-10 pr-4 font-mono text-sm text-ink outline-none transition placeholder:font-sans placeholder:text-ink-mute focus:border-ink focus:bg-white tabular"
                  @input="sanitizePhoneInput"
                />
              </div>
            </div>

            <div>
              <label class="block text-xs font-medium text-ink-soft">昵称 <span class="text-ink-mute">(选填)</span></label>
              <input
                v-model="nickname"
                type="text"
                placeholder="如：陈默（留空默认使用用户名）"
                class="mt-1.5 h-11 w-full rounded-2xl border border-line bg-paper/60 px-4 text-sm text-ink outline-none transition placeholder:text-ink-mute focus:border-ink focus:bg-white"
              />
            </div>
          </template>

          <div>
            <label class="block text-xs font-medium text-ink-soft">
              登录密码 <span v-if="mode === 'register'" class="text-brick">*</span>
            </label>
            <div class="relative mt-1.5">
              <input
                v-model="password"
                :type="showPwd ? 'text' : 'password'"
                :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
                :placeholder="mode === 'login' ? '输入登录密码' : '至少 6 位字符'"
                class="h-11 w-full rounded-2xl border border-line bg-paper/60 pl-4 pr-11 text-sm text-ink outline-none transition placeholder:text-ink-mute focus:border-ink focus:bg-white"
              />
              <button
                type="button"
                class="absolute right-3 top-1/2 -translate-y-1/2 rounded-full p-1 text-ink-mute transition hover:text-ink"
                :aria-label="showPwd ? '隐藏密码' : '显示密码'"
                @click="showPwd = !showPwd"
              >
                <EyeOff v-if="showPwd" class="h-4 w-4" />
                <Eye v-else class="h-4 w-4" />
              </button>
            </div>
          </div>

          <p
            v-if="errorMsg"
            class="rounded-xl bg-brick-soft px-3.5 py-2.5 text-xs font-medium text-brick"
          >
            {{ errorMsg }}
          </p>

          <button type="submit" class="btn-primary !mt-6 h-12 w-full" :disabled="submitting">
            <template v-if="submitting">正在验证身份…</template>
            <template v-else>{{ mode === 'login' ? '登录' : '完成实名注册并登录' }}</template>
          </button>
        </form>

        <p class="mt-5 text-center text-[11px] text-ink-mute">
          网关签发 JWT 令牌持久化保存 7 天 · 刷新浏览器自动保持登录状态
        </p>
      </div>

      <!-- Role-separated 1-click experience entry -->
      <div class="mt-6 rounded-3xl border border-line/90 bg-white/70 p-5">
        <div class="flex items-center justify-between">
          <p class="eyebrow">体验账号快捷通道</p>
          <span class="text-[11px] text-ink-mute">RBAC 角色视图隔离</span>
        </div>

        <div class="mt-3.5 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          <button
            type="button"
            class="group flex items-start gap-3 rounded-2xl border border-line bg-white p-3.5 text-left transition hover:border-ink/40 hover:shadow-soft"
            :disabled="submitting"
            @click="quickLogin('zhaolan', 'Ticket@2026')"
          >
            <span class="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-paper text-ink">
              <UserCheck class="h-4 w-4" :stroke-width="1.8" />
            </span>
            <span class="min-w-0">
              <span class="block text-sm font-medium text-ink">普通购票会员</span>
              <span class="mt-0.5 block text-[11px] leading-relaxed text-ink-soft">
                zhaolan · 13820240336 · 纯净商业选座与扫码电子票
              </span>
            </span>
          </button>

          <button
            type="button"
            class="group flex items-start gap-3 rounded-2xl border border-line bg-white p-3.5 text-left transition hover:border-ink/40 hover:shadow-soft"
            :disabled="submitting"
            @click="quickLogin('admin', 'Admin@2026')"
          >
            <span class="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-brick-soft text-brick">
              <ShieldCheck class="h-4 w-4" :stroke-width="1.8" />
            </span>
            <span class="min-w-0">
              <span class="block text-sm font-medium text-ink">架构运维专员</span>
              <span class="mt-0.5 block text-[11px] leading-relaxed text-ink-soft">
                admin · 票房销量面板、三库数据看板与架构压测
              </span>
            </span>
          </button>
        </div>
      </div>
    </main>

    <footer class="mx-auto flex w-full max-w-[1040px] flex-wrap items-center justify-between gap-2 text-xs text-ink-mute">
      <span>© 2026 PulseTicket Live &amp; Cinema</span>
      <span>Gateway JWT · BCrypt · RBAC</span>
    </footer>
  </div>
</template>
