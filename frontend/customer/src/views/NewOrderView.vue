<script setup>
import { ref, reactive, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppInput from '@/components/AppInput.vue'
import AppButton from '@/components/AppButton.vue'
import AppCard from '@/components/AppCard.vue'
import FileDrop from '@/components/FileDrop.vue'
import { ordersApi } from '@/api/orders'
import { useForm } from '@/composables/useForm'
import { toast } from '@/composables/useToast'
import { useOrderDraftStore } from '@/stores/orderDraft'

const router = useRouter()
const draft = useOrderDraftStore()

// gcode 是唯一真相：不再手填 material/color/layer/nozzle，只留数量 + 备注
const form = reactive({ quantity: 1, customer_note: '' })
const file = ref(null)
const loading = ref(false)

// 首页上传框带过来的文件，挂载时取走（赋给 file 触发下方 watch 自动解析）
onMounted(() => {
  const pending = draft.take()
  if (pending) file.value = pending
})

// 文件预览状态（.gcode.3mf 自动报价）
const previewing = ref(false)
const preview = ref(null)       // preview 接口返回：filaments/material/quote/...
const parseError = ref('')

const { errors, validate } = useForm(form, {
  quantity: [(v) => Number(v) >= 1 || '至少 1 件'],
})

// 选文件后解析 gcode（暂只支持 .gcode.3mf，无需按扩展名分支）
watch(file, async (nv) => {
  preview.value = null
  parseError.value = ''
  if (!nv) return
  previewing.value = true
  try {
    const fd = new FormData()
    fd.append('file', nv.file, nv.name)
    const res = await ordersApi.preview(fd)
    if (res.code === 200) {
      preview.value = res.data
    } else {
      parseError.value = res.message || '解析失败'
    }
  } catch (e) {
    parseError.value = e.response?.data?.message || '无法解析该 gcode 文件'
  } finally {
    previewing.value = false
  }
})

function fmtWeight(g) {
  if (g == null) return '-'
  return Number(g).toFixed(1) + ' g'
}
function fmtTime(s) {
  if (s == null) return '-'
  const m = Math.round(s / 60)
  if (m < 60) return m + ' 分钟'
  return Math.floor(m / 60) + 'h ' + (m % 60) + 'm'
}
// AMS tray_color 可能是 8 位 RGBA，截前 6 位 + 补 #
function swatchColor(c) {
  if (!c) return 'transparent'
  const s = String(c).replace('#', '').slice(0, 6).toUpperCase()
  return '#' + (s || 'transparent')
}

async function submit() {
  if (!validate()) return
  loading.value = true
  try {
    const fd = new FormData()
    fd.append('quantity', form.quantity)
    if (form.customer_note) fd.append('customer_note', form.customer_note)
    if (file.value) fd.append('file', file.value.file, file.value.name)

    const res = await ordersApi.create(fd)
    if (res.code === 200) {
      toast.success(res.message || '订单已创建')
      router.push({ name: 'orders' })
    } else {
      toast.error(res.message || '创建失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '网络错误')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-2xl mx-auto space-y-6">
    <div>
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">新建打印订单</h1>
      <p class="text-sm text-zinc-400 mt-1">
        上传模型文件，系统按 gcode 自动识别材料/颜色并报价（gcode 是唯一真相，无需手填材料）
      </p>
    </div>

    <AppCard>
      <form @submit.prevent="submit" class="space-y-5">
        <div class="grid sm:grid-cols-2 gap-4">
          <AppInput
            v-model="form.quantity" type="number" label="数量" :error="errors.quantity"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">备注</label>
          <textarea
            v-model="form.customer_note" rows="3" placeholder="给管理员的说明（颜色偏好、特殊要求等）…"
            class="w-full px-3.5 py-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10 focus:border-zinc-400"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">模型文件</label>
          <FileDrop v-model="file" />
          <p class="mt-1.5 text-xs text-zinc-400">.gcode.3mf 自动报价</p>
        </div>

        <!-- 解析中 -->
        <div v-if="previewing" class="text-sm text-zinc-400 flex items-center gap-2">
          <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-300 border-t-zinc-600 animate-spin" />
          正在解析 gcode…
        </div>

        <!-- 解析失败 -->
        <div v-if="parseError" class="text-sm text-rose-500 bg-rose-50 dark:bg-rose-950/40 rounded-lg px-3 py-2">
          {{ parseError }}（仍可提交，将进入人工报价）
        </div>

        <!-- gcode 预览卡 -->
        <div v-if="preview" class="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/40 p-4 space-y-3">
          <img
            v-if="preview.preview_image"
            :src="preview.preview_image" alt="模型预览"
            class="w-full max-h-64 object-contain rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700"
          />
          <div class="flex items-center justify-between">
            <span class="text-sm font-medium text-zinc-700 dark:text-zinc-300">报价预览</span>
            <span class="text-xs text-zinc-400">主材料：{{ preview.material || '-' }}</span>
          </div>

          <!-- 多色 filament -->
          <div v-if="preview.filaments?.length" class="space-y-1.5">
            <div
              v-for="f in preview.filaments" :key="f.id || f.type"
              class="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400"
            >
              <span
                class="inline-block w-4 h-4 rounded-full border border-zinc-300 dark:border-zinc-600"
                :style="{ background: swatchColor(f.color) }"
              />
              <span class="font-medium">{{ f.type || '未知材料' }}</span>
              <span class="text-zinc-400">{{ fmtWeight(f.used_g) }}</span>
              <span class="text-zinc-300 dark:text-zinc-600 font-mono">{{ f.color }}</span>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="flex justify-between"><span class="text-zinc-400">总克重</span><span>{{ fmtWeight(preview.filament_used_g) }}</span></div>
            <div class="flex justify-between"><span class="text-zinc-400">时长</span><span>{{ fmtTime(preview.print_time_s) }}</span></div>
          </div>

          <!-- 报价明细 -->
          <div v-if="preview.quote" class="border-t border-zinc-200 dark:border-zinc-700 pt-2 space-y-1 text-xs">
            <div class="flex justify-between text-zinc-500"><span>基础费</span><span>{{ preview.quote.base_fee }}</span></div>
            <div class="flex justify-between text-zinc-500"><span>材料费</span><span>{{ preview.quote.material_cost }}</span></div>
            <div class="flex justify-between text-zinc-500"><span>机时费</span><span>{{ preview.quote.machine_cost }}</span></div>
            <div class="flex justify-between text-base font-semibold text-zinc-900 dark:text-zinc-100">
              <span>合计</span><span>{{ preview.quote.credit }} credit</span>
            </div>
          </div>
        </div>

        <div class="flex justify-end gap-2 pt-2">
          <AppButton variant="ghost" type="button" @click="router.back()">取消</AppButton>
          <AppButton type="submit" :loading="loading">提交订单</AppButton>
        </div>
      </form>
    </AppCard>
  </div>
</template>
