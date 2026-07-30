<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import {
  Dialog, DialogPanel, DialogTitle,
  TransitionRoot, TransitionChild,
} from '@headlessui/vue'
import AppButton from '@/components/AppButton.vue'
import AppInput from '@/components/AppInput.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppCard from '@/components/AppCard.vue'
import PreviewImage from '@/components/PreviewImage.vue'
import PrinterSelect from '@/components/PrinterSelect.vue'
import AmsSlotGrid from '@/components/AmsSlotGrid.vue'
import { normTray } from '@/utils/ams'
import { adminApi } from '@/api/admin'
import { toast } from '@/composables/useToast'

const STATUS_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'QUOTING', label: '报价中' },
  { value: 'WAITING_CONFIRM', label: '待确认' },
  { value: 'CREDIT_RESERVED', label: '准备打印' },
  { value: 'READY_TO_PRINT', label: '排队中' },
  { value: 'PRINTING', label: '打印中' },
  { value: 'PRINT_COMPLETED', label: '已打印' },
  { value: 'PRINT_FAILED', label: '失败' },
  { value: 'CLOSED', label: '已完成' },
  { value: 'CANCELLED', label: '已取消' },
  { value: 'REFUNDED', label: '已退款' },
]

const POST_PRINT = ['PRINTING', 'PRINT_COMPLETED', 'QC_PENDING', 'CLOSED', 'CANCELLED', 'REFUNDED']

const orders = ref([])
const loading = ref(false)
const filter = reactive({ status: '', page: 1, per_page: 20, total: 0 })
const detail = ref(null)
const acting = ref(false)

// 金额操作 dialog（quote / complete / refund 共用）
const amountDialog = reactive({ open: false, action: '', order: null, amount: '', title: '', note: '' })

// Bambuddy 任务（dispatch 自动建，这里只读展示状态）
const bambuddyJob = ref(null)

async function load() {
  loading.value = true
  try {
    const params = { page: filter.page, per_page: filter.per_page }
    if (filter.status) params.status = filter.status
    const res = await adminApi.orders(params)
    orders.value = res.data.items
    filter.total = res.data.total
  } finally {
    loading.value = false
  }
}

function onFilter() {
  filter.page = 1
  load()
}

async function openDetail(o) {
  try {
    detail.value = (await adminApi.orderDetail(o.id)).data
    bambuddyJob.value = (await adminApi.getBambuddyJob(o.id)).data
  } catch {
    /* 静默 */
  }
}
async function refreshDetail() {
  if (!detail.value) return
  detail.value = (await adminApi.orderDetail(detail.value.id)).data
  bambuddyJob.value = (await adminApi.getBambuddyJob(detail.value.id)).data
}

// 下发 Bambuddy（打印机富下拉 + AMS 四格点选）
const dispatchDialog = reactive({
  open: false, order: null,
  printerId: '',
  printers: [],
  preview: null,        // {printer, expected_filaments, match_result}
  manualPicks: {},      // extruder_id -> tray_id（点选覆盖；预填系统推荐）
  currentEid: null,     // 当前在配的 extruder（多 extruder 切换）
  previewLoading: false,
})
const dispatching = ref(false)

// 选中打印机的 AMS 四格（normTray，tray_id = 下标+1，与 ams_mapping 同体系）
const selectedPrinter = computed(() =>
  dispatchDialog.printers.find((p) => p.bambuddy_printer_id === dispatchDialog.printerId) || null
)
const amsTrays = computed(() => {
  const raw = selectedPrinter.value?.status_detail?.ams?.[0]?.tray || []
  return raw.map((t, i) => normTray(t, i))
})
const expectedFilaments = computed(() => dispatchDialog.preview?.expected_filaments || [])
const mappings = computed(() => dispatchDialog.preview?.match_result?.mappings || [])
const currentMapping = computed(() =>
  mappings.value.find((m) => String(m.extruder_id) === String(dispatchDialog.currentEid)) || null
)
// 第一个还没配料的 extruder（点选后自动跳过去）
const firstUnassignedEid = computed(() => {
  for (const m of mappings.value) {
    if (dispatchDialog.manualPicks[m.extruder_id] == null) return m.extruder_id
  }
  return null
})
// 当前 extruder 已选的格
const selectedSlotId = computed(() => {
  const v = dispatchDialog.manualPicks[dispatchDialog.currentEid]
  return v != null ? Number(v) : null
})
// 当前 extruder 的系统推荐格（柔和提示）
const recommendedSlotId = computed(() => currentMapping.value?.actual_tray_id ?? null)
// 其他 extruder 占用的格 → 角标 'E#'（当前 extruder 用 ring 高亮，不重复角标）
const assignedLabels = computed(() => {
  const labels = {}
  for (const m of mappings.value) {
    if (String(m.extruder_id) === String(dispatchDialog.currentEid)) continue
    const tid = dispatchDialog.manualPicks[m.extruder_id]
    if (tid != null) labels[tid] = 'E' + m.extruder_id
  }
  return labels
})

async function openDispatch(o) {
  dispatchDialog.order = o
  dispatchDialog.printerId = ''
  dispatchDialog.preview = null
  dispatchDialog.manualPicks = {}
  dispatchDialog.currentEid = null
  dispatchDialog.printers = []
  dispatchDialog.open = true
  // 拉启用打印机列表（富下拉用；含 status_detail 供 AMS 色点）
  try {
    const res = await adminApi.getPrinters()
    dispatchDialog.printers = (res.data?.items || []).filter((p) => p.enabled)
  } catch {
    /* 静默 */
  }
}

async function onPickPrinter() {
  dispatchDialog.preview = null
  dispatchDialog.manualPicks = {}
  dispatchDialog.currentEid = null
  if (!dispatchDialog.printerId) return
  dispatchDialog.previewLoading = true
  try {
    const res = await adminApi.dispatchPreview(
      dispatchDialog.order.id, Number(dispatchDialog.printerId)
    )
    if (res.code === 200) {
      dispatchDialog.preview = res.data
      // 预填系统推荐：匹配上的单子打开即可直接下发，无需手点
      const picks = {}
      for (const m of (res.data.match_result?.mappings || [])) {
        if (m.actual_tray_id != null) picks[m.extruder_id] = m.actual_tray_id
      }
      dispatchDialog.manualPicks = picks
      dispatchDialog.currentEid = firstUnassignedEid.value
        ?? (res.data.expected_filaments?.[0]?.extruder_id ?? null)
    } else {
      toast.error(res.message || '预览失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '预览失败')
  } finally {
    dispatchDialog.previewLoading = false
  }
}

function onPickSlot(trayId) {
  dispatchDialog.manualPicks = {
    ...dispatchDialog.manualPicks,
    [dispatchDialog.currentEid]: trayId,
  }
  const next = firstUnassignedEid.value
  if (next != null) dispatchDialog.currentEid = next
}
function selectExtruder(eid) {
  dispatchDialog.currentEid = eid
}

// 组装下发的 ams_mapping（extruder 升序的 tray_id 数组，1-based，透传给 Bambuddy）
function effectiveMapping() {
  const ms = mappings.value
  if (!ms.length) return null
  const picks = {}
  for (const m of ms) {
    const t = dispatchDialog.manualPicks[m.extruder_id]
    if (t == null) return null  // 有 extruder 未配齐
    picks[m.extruder_id] = t
  }
  return ms
    .slice()
    .sort((a, b) => Number(a.extruder_id) - Number(b.extruder_id))
    .map((m) => picks[m.extruder_id])
}
function canDispatch() {
  if (!dispatchDialog.printerId) return false
  if (!expectedFilaments.value.length) return true  // 无期望 → 不校验，直接下发
  return effectiveMapping() !== null
}

async function doDispatch() {
  if (!dispatchDialog.printerId) {
    toast.error('请选择打印机')
    return
  }
  const mapping = effectiveMapping()
  dispatching.value = true
  try {
    const res = await adminApi.dispatchOrder(
      dispatchDialog.order.id,
      Number(dispatchDialog.printerId),
      mapping
    )
    if (res.code === 200) {
      toast.success('已下发 Bambuddy（upload + queue）')
      dispatchDialog.open = false
      await load()
      await refreshDetail()
    } else {
      toast.error(res.message || '下发失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '失败')
  } finally {
    dispatching.value = false
  }
}

// 文件下载 + 切片产物上传（Phase 4 路径 B）
const slicedFile = ref(null)
const uploading = ref(false)
async function downloadFile(fileId, name) {
  try {
    const blob = await adminApi.downloadOrderFile(detail.value.id, fileId)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    toast.error('下载失败')
  }
}
function onPickSliced(e) {
  slicedFile.value = e.target.files?.[0] || null
}
async function doUploadSliced() {
  if (!slicedFile.value) {
    toast.error('请先选择 .gcode.3mf')
    return
  }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', slicedFile.value)
    const res = await adminApi.uploadSliced(detail.value.id, fd)
    if (res.code === 200) {
      toast.success(res.message)
      slicedFile.value = null
      await load()
      await refreshDetail()
    } else {
      toast.error(res.message || '上传失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function simpleAction(o, label, fn) {
  acting.value = true
  try {
    const res = await fn(o.id)
    if (res.code === 200) {
      toast.success(label + '成功')
      await load()
      await refreshDetail()
    } else {
      toast.error(res.message || label + '失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '操作失败')
  } finally {
    acting.value = false
  }
}

function approve(o) {
  return simpleAction(o, '审核', (id) => adminApi.approve(id))
}
function start(o) {
  return simpleAction(o, '开始打印', (id) => adminApi.start(id))
}
// 文本操作 dialog（fail 失败原因 / cancel 取消原因 / delivery-note 交付说明）。
// 这些操作都附带「通知客户」的文本，不再一键直发。
const noteDialog = reactive({
  open: false, action: '', order: null, text: '', title: '', label: '', required: false,
})

function openNote(o, action, title, label, required) {
  Object.assign(noteDialog, { open: true, action, order: o, text: '', title, label, required })
}
function openFail(o) {
  openNote(o, 'fail', '标记打印失败', '失败原因（将通知客户）', true)
}
function openCancel(o) {
  openNote(o, 'cancel', '取消订单', '取消原因（可选，将通知客户）', false)
}
function openDeliveryNote(o) {
  openNote(o, 'delivery', '补充交付说明', '取件 / 发货说明（将通知客户）', true)
}

async function submitNote() {
  const text = noteDialog.text.trim()
  if (noteDialog.required && !text) {
    toast.error(noteDialog.label + '不能为空')
    return
  }
  acting.value = true
  try {
    const id = noteDialog.order.id
    let res
    if (noteDialog.action === 'fail') {
      res = await adminApi.fail(id, { reason: text })
    } else if (noteDialog.action === 'cancel') {
      res = await adminApi.cancel(id, text ? { reason: text } : null)
    } else {
      res = await adminApi.deliveryNote(id, { completion_note: text })
    }
    if (res.code === 200) {
      toast.success(noteDialog.title + '成功')
      noteDialog.open = false
      await load()
      await refreshDetail()
    } else {
      toast.error(res.message || '操作失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '操作失败')
  } finally {
    acting.value = false
  }
}

function openAmount(o, action, title, defaultAmount) {
  amountDialog.action = action
  amountDialog.order = o
  amountDialog.amount = defaultAmount != null ? String(defaultAmount) : ''
  amountDialog.title = title
  amountDialog.note = ''
  amountDialog.open = true
}
function openQuote(o) {
  openAmount(o, 'quote', '报价', null)
}
function openComplete(o) {
  openAmount(o, 'complete', '完成实扣', o.frozen_credit)
}
function openRefund(o) {
  openAmount(o, 'refund', '退款', null)
}

async function submitAmount() {
  const amt = Number(amountDialog.amount)
  if (!amt || amt <= 0) {
    toast.error('请输入正数金额')
    return
  }
  acting.value = true
  try {
    let res
    const note = amountDialog.note.trim()
    if (amountDialog.action === 'quote') {
      res = await adminApi.quote(amountDialog.order.id, { estimated_credit: amt })
    } else if (amountDialog.action === 'complete') {
      res = await adminApi.complete(amountDialog.order.id, {
        actual_credit: amt, ...(note ? { completion_note: note } : {}),
      })
    } else {
      res = await adminApi.refund(amountDialog.order.id, {
        amount: amt, ...(note ? { reason: note } : {}),
      })
    }
    if (res.code === 200) {
      toast.success(amountDialog.title + '成功')
      amountDialog.open = false
      await load()
      await refreshDetail()
    } else {
      toast.error(res.message || '操作失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '操作失败')
  } finally {
    acting.value = false
  }
}

function fmtDate(s) {
  if (!s) return ''
  const d = new Date(s)
  return isNaN(d) ? s : d.toLocaleString('zh-CN')
}
function fmtDuration(sec) {
  if (sec == null || sec < 0) return '-'
  const m = Math.round(sec / 60)
  if (m < 1) return '<1m'
  const h = Math.floor(m / 60)
  const mm = m % 60
  return h > 0 ? `${h}h${String(mm).padStart(2, '0')}m` : `${mm}m`
}
function canCancel(s) {
  return !POST_PRINT.includes(s)
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">订单管理</h1>
      <p class="text-sm text-zinc-400 mt-1">报价 / 审核 / 打印 / 实扣 / 退款</p>
    </div>

    <AppCard>
      <div class="flex items-center gap-3 mb-4 flex-wrap">
        <label class="text-sm text-zinc-500">状态筛选</label>
        <select
          v-model="filter.status"
          @change="onFilter"
          class="h-9 px-3 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10"
        >
          <option v-for="o in STATUS_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <span class="text-sm text-zinc-400 ml-auto">共 {{ filter.total }} 条</span>
      </div>

      <div class="overflow-x-auto -mx-5">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-xs text-zinc-400 border-b border-zinc-100 dark:border-zinc-800">
              <th class="px-5 py-2 font-medium">订单号</th>
              <th class="px-3 py-2 font-medium">客户</th>
              <th class="px-3 py-2 font-medium">材料</th>
              <th class="px-3 py-2 font-medium">重量/时长</th>
              <th class="px-3 py-2 font-medium">状态</th>
              <th class="px-3 py-2 font-medium">消耗</th>
              <th class="px-3 py-2 font-medium">创建</th>
              <th class="px-5 py-2 font-medium">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-zinc-50 dark:divide-zinc-800/60">
            <tr v-if="loading">
              <td colspan="8" class="py-10 text-center text-zinc-400">加载中…</td>
            </tr>
            <tr v-else-if="!orders.length">
              <td colspan="8" class="py-10 text-center text-zinc-400">暂无订单</td>
            </tr>
            <tr v-for="o in orders" :key="o.id" class="hover:bg-zinc-50 dark:hover:bg-zinc-900/40">
              <td class="px-5 py-3">
                <button
                  class="font-mono text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
                  @click="openDetail(o)"
                >
                  {{ o.order_no }}
                </button>
              </td>
              <td class="px-3 py-3">
                <span class="text-zinc-700 dark:text-zinc-300">{{ o.username || '—' }}</span>
                <span class="block text-xs text-zinc-400">#{{ o.user_id }}</span>
              </td>
              <td class="px-3 py-3">
                {{ o.material
                }}<span class="text-zinc-400"> · {{ o.quantity }}</span>
              </td>
              <td class="px-3 py-3 text-xs text-zinc-500 whitespace-nowrap">
                <span>{{ o.estimate_weight_g ? o.estimate_weight_g + 'g' : '-' }}</span>
                <span class="block text-zinc-400">{{ fmtDuration(o.estimate_print_seconds) }}</span>
              </td>
              <td class="px-3 py-3"><StatusBadge :status="o.public_status" /></td>
              <td class="px-3 py-3 text-zinc-600 dark:text-zinc-400 whitespace-nowrap">
                <span v-if="o.actual_credit">{{ o.actual_credit }}</span>
                <span v-else-if="o.estimated_credit">{{ o.estimated_credit }}</span>
                <span v-if="o.frozen_credit && o.frozen_credit !== '0.00' && o.frozen_credit !== '0'" class="text-zinc-400">/ 冻{{ o.frozen_credit }}</span>
              </td>
              <td class="px-3 py-3 text-xs text-zinc-400 whitespace-nowrap">{{ fmtDate(o.created_at) }}</td>
              <td class="px-5 py-3">
                <div class="flex flex-wrap gap-1">
                  <AppButton v-if="o.status === 'QUOTING'" variant="primary" size="xs" @click="openQuote(o)">报价</AppButton>
                  <AppButton
                    v-if="o.status === 'CREDIT_RESERVED'" variant="success" size="xs" :loading="acting"
                    @click="approve(o)"
                  >审核</AppButton>
                  <AppButton
                    v-if="o.status === 'READY_TO_PRINT'" variant="primary" size="xs"
                    @click="openDispatch(o)"
                  >下发</AppButton>
                  <AppButton
                    v-if="o.status === 'READY_TO_PRINT'" variant="ghost" size="xs" :loading="acting"
                    @click="start(o)"
                  >手动开始</AppButton>
                  <AppButton v-if="o.status === 'PRINTING'" variant="success" size="xs" @click="openComplete(o)">完成</AppButton>
                  <AppButton
                    v-if="o.status === 'PRINTING'" variant="warning" size="xs"
                    @click="openFail(o)"
                  >失败</AppButton>
                  <AppButton
                    v-if="['PRINT_COMPLETED', 'QC_PENDING'].includes(o.status)"
                    variant="primary" size="xs" @click="openDeliveryNote(o)"
                  >交付说明</AppButton>
                  <AppButton
                    v-if="['PRINT_COMPLETED', 'QC_PENDING', 'NEED_REVIEW'].includes(o.status)"
                    variant="ghost" size="xs" @click="openRefund(o)"
                  >退款</AppButton>
                  <AppButton v-if="canCancel(o.status)" variant="ghost" size="xs" @click="openCancel(o)">取消</AppButton>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="filter.total > filter.per_page" class="flex justify-center mt-4 items-center gap-3">
        <AppButton variant="ghost" size="sm" :disabled="filter.page <= 1" @click="filter.page--; load()">
          上一页
        </AppButton>
        <span class="text-sm text-zinc-500">第 {{ filter.page }} 页</span>
        <AppButton
          variant="ghost" size="sm" :disabled="filter.page * filter.per_page >= filter.total"
          @click="filter.page++; load()"
        >下一页</AppButton>
      </div>
    </AppCard>

    <!-- 详情 Dialog -->
    <TransitionRoot appear :show="!!detail" as="template">
      <Dialog as="div" class="relative z-40" @close="detail = null">
        <TransitionChild
          enter="duration-200" enter-from="opacity-0" enter-to="opacity-100"
          leave="duration-150" leave-from="opacity-100" leave-to="opacity-0"
        >
          <div class="fixed inset-0 bg-black/30 backdrop-blur-sm" />
        </TransitionChild>
        <div class="fixed inset-0 flex items-center justify-center p-4">
          <TransitionChild
            enter="duration-200" enter-from="opacity-0 scale-95" enter-to="opacity-100 scale-100"
            leave="duration-150" leave-from="opacity-100 scale-100" leave-to="opacity-0 scale-95"
          >
            <DialogPanel
              class="w-full max-w-lg rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-xl p-6 max-h-[85vh] overflow-y-auto"
            >
              <DialogTitle class="text-lg font-semibold mb-4">订单详情</DialogTitle>
              <div v-if="detail" class="mb-4 max-w-[180px]">
                <PreviewImage :loader="() => adminApi.orderPreview(detail.id)" />
              </div>
              <div v-if="detail" class="space-y-2 text-sm">
                <div class="flex justify-between"><span class="text-zinc-400">订单号</span><span class="font-mono">{{ detail.order_no }}</span></div>
                <div class="flex justify-between"><span class="text-zinc-400">客户</span><span>{{ detail.username || '—' }} <span class="text-zinc-400">#{{ detail.user_id }}</span></span></div>
                <div class="flex justify-between items-center"><span class="text-zinc-400">状态</span><StatusBadge :status="detail.public_status" /></div>
                <div class="flex justify-between"><span class="text-zinc-400">材料/数量</span><span>{{ detail.material }} · {{ detail.quantity }}件</span></div>
                <div class="flex justify-between"><span class="text-zinc-400">重量</span><span>{{ detail.estimate_weight_g ? detail.estimate_weight_g + 'g' : '-' }}</span></div>
                <div class="flex justify-between"><span class="text-zinc-400">打印时长</span><span>{{ fmtDuration(detail.estimate_print_seconds) }}</span></div>
                <div v-if="detail.public_progress > 0" class="flex justify-between"><span class="text-zinc-400">进度</span><span class="tabular-nums">{{ detail.public_progress }}%<span v-if="detail.remaining_seconds"> · 剩 {{ fmtDuration(detail.remaining_seconds) }}</span></span></div>
                <div v-if="detail.color" class="flex justify-between"><span class="text-zinc-400">颜色</span><span>{{ detail.color }}</span></div>
                <div class="flex justify-between"><span class="text-zinc-400">报价</span><span>{{ detail.estimated_credit || '-' }}</span></div>
                <div class="flex justify-between"><span class="text-zinc-400">冻结</span><span>{{ detail.frozen_credit }}</span></div>
                <div class="flex justify-between"><span class="text-zinc-400">实扣</span><span>{{ detail.actual_credit || '-' }}</span></div>
                <div v-if="detail.customer_note" class="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <span class="text-zinc-400">客户备注</span>
                  <p class="mt-1 text-zinc-700 dark:text-zinc-300">{{ detail.customer_note }}</p>
                </div>
                <div v-if="detail.admin_note" class="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <span class="text-zinc-400">管理备注</span>
                  <p class="mt-1 text-zinc-700 dark:text-zinc-300">{{ detail.admin_note }}</p>
                </div>
                <div v-if="detail.fail_reason" class="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <span class="text-rose-500">失败原因</span>
                  <p class="mt-1 text-rose-700 dark:text-rose-300">{{ detail.fail_reason }}</p>
                </div>
                <div v-if="detail.completion_note" class="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <span class="text-emerald-500">取件 / 发货</span>
                  <p class="mt-1 text-emerald-700 dark:text-emerald-300">{{ detail.completion_note }}</p>
                </div>
                <div v-if="detail.files?.length" class="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <span class="text-zinc-400">文件</span>
                  <div v-for="f in detail.files" :key="f.id" class="mt-1 flex items-center justify-between gap-2">
                    <p class="font-mono text-xs break-all">{{ f.original_filename }}（{{ f.file_type }}）</p>
                    <button @click="downloadFile(f.id, f.original_filename)" class="text-xs text-indigo-600 hover:underline shrink-0">下载</button>
                  </div>
                </div>
                <!-- 上传切片产物（Phase 4 路径 B）-->
                <div v-if="detail.status === 'QUOTING'" class="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <span class="text-zinc-400">上传切片产物</span>
                  <p class="mt-1 text-xs text-zinc-400">本地 Bambu Studio 切片后上传 .gcode.3mf，系统自动报价</p>
                  <div class="mt-2 flex gap-2 items-center">
                    <input type="file" accept=".gcode.3mf" @change="onPickSliced" class="text-xs flex-1" />
                    <AppButton size="xs" :loading="uploading" :disabled="!slicedFile" @click="doUploadSliced">上传+报价</AppButton>
                  </div>
                </div>
                <!-- Bambuddy 任务（dispatch 自动建，只读展示）-->
                <div class="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <span class="text-zinc-400">Bambuddy 任务</span>
                  <p v-if="bambuddyJob && bambuddyJob.bambuddy_printer_id" class="mt-1 text-xs text-zinc-500">
                    已下发到 printer #{{ bambuddyJob.bambuddy_printer_id }}
                    <span v-if="bambuddyJob.bambuddy_queue_id">· 队列 #{{ bambuddyJob.bambuddy_queue_id }}</span>
                  </p>
                  <p v-else class="mt-1 text-xs text-zinc-400">未下发（点「下发」按钮自动创建）</p>
                </div>
              </div>
              <div class="flex justify-end mt-5">
                <AppButton variant="ghost" @click="detail = null">关闭</AppButton>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </TransitionRoot>

    <!-- 金额操作 Dialog -->
    <TransitionRoot appear :show="amountDialog.open" as="template">
      <Dialog as="div" class="relative z-50" @close="amountDialog.open = false">
        <TransitionChild
          enter="duration-200" enter-from="opacity-0" enter-to="opacity-100"
          leave="duration-150" leave-from="opacity-100" leave-to="opacity-0"
        >
          <div class="fixed inset-0 bg-black/30 backdrop-blur-sm" />
        </TransitionChild>
        <div class="fixed inset-0 flex items-center justify-center p-4">
          <TransitionChild
            enter="duration-200" enter-from="opacity-0 scale-95" enter-to="opacity-100 scale-100"
            leave="duration-150" leave-from="opacity-100 scale-100" leave-to="opacity-0 scale-95"
          >
            <DialogPanel
              class="w-full max-w-sm rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-xl p-6"
            >
              <DialogTitle class="text-lg font-semibold mb-4">{{ amountDialog.title }}</DialogTitle>
              <AppInput
                v-model="amountDialog.amount" type="number" label="credit 金额"
                placeholder="正数" required
              />
              <p v-if="amountDialog.action === 'complete'" class="mt-2 text-xs text-zinc-400">
                实扣金额；低于冻结额会退差价，高于冻结额会补扣
              </p>
              <div v-if="['complete', 'refund'].includes(amountDialog.action)" class="mt-3">
                <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">
                  {{ amountDialog.action === 'complete' ? '完成备注（取件/发货，可选，将通知客户）' : '退款原因（可选，将通知客户）' }}
                </label>
                <textarea
                  v-model="amountDialog.note" rows="2"
                  class="w-full px-3 py-2 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10"
                ></textarea>
              </div>
              <div class="flex justify-end gap-2 mt-5">
                <AppButton variant="ghost" @click="amountDialog.open = false">取消</AppButton>
                <AppButton :loading="acting" @click="submitAmount">确认</AppButton>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </TransitionRoot>

    <!-- 文本操作 Dialog（失败原因 / 取消原因 / 交付说明，均带通知客户） -->
    <TransitionRoot appear :show="noteDialog.open" as="template">
      <Dialog as="div" class="relative z-50" @close="noteDialog.open = false">
        <TransitionChild
          enter="duration-200" enter-from="opacity-0" enter-to="opacity-100"
          leave="duration-150" leave-from="opacity-100" leave-to="opacity-0"
        >
          <div class="fixed inset-0 bg-black/30 backdrop-blur-sm" />
        </TransitionChild>
        <div class="fixed inset-0 flex items-center justify-center p-4">
          <TransitionChild
            enter="duration-200" enter-from="opacity-0 scale-95" enter-to="opacity-100 scale-100"
            leave="duration-150" leave-from="opacity-100 scale-100" leave-to="opacity-0 scale-95"
          >
            <DialogPanel
              class="w-full max-w-sm rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-xl p-6"
            >
              <DialogTitle class="text-lg font-semibold mb-4">{{ noteDialog.title }}</DialogTitle>
              <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">
                {{ noteDialog.label }}
              </label>
              <textarea
                v-model="noteDialog.text" rows="3"
                class="w-full px-3 py-2 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10"
              ></textarea>
              <div class="flex justify-end gap-2 mt-5">
                <AppButton variant="ghost" @click="noteDialog.open = false">取消</AppButton>
                <AppButton :loading="acting" @click="submitNote">确认</AppButton>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </TransitionRoot>

    <!-- 下发 Bambuddy Dialog（Phase 4：打印机下拉 + AMS 校验） -->
    <TransitionRoot appear :show="dispatchDialog.open" as="template">
      <Dialog as="div" class="relative z-50" @close="dispatchDialog.open = false">
        <TransitionChild
          enter="duration-200" enter-from="opacity-0" enter-to="opacity-100"
          leave="duration-150" leave-from="opacity-100" leave-to="opacity-0"
        >
          <div class="fixed inset-0 bg-black/30 backdrop-blur-sm" />
        </TransitionChild>
        <div class="fixed inset-0 flex items-center justify-center p-4">
          <TransitionChild
            enter="duration-200" enter-from="opacity-0 scale-95" enter-to="opacity-100 scale-100"
            leave="duration-150" leave-from="opacity-100 scale-100" leave-to="opacity-0 scale-95"
          >
            <DialogPanel
              class="w-full max-w-2xl rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-xl p-6 max-h-[85vh] overflow-y-auto"
            >
              <DialogTitle class="text-lg font-semibold mb-1">下发 Bambuddy 打印</DialogTitle>
              <p class="text-xs text-zinc-400 mb-4">选择打印机后，系统对比 gcode 期望料盘与 AMS 实际料盘</p>

              <!-- 打印机选择（富下拉：状态徽章 + 4 色点 + 队列） -->
              <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">打印机</label>
              <PrinterSelect
                v-model="dispatchDialog.printerId"
                :printers="dispatchDialog.printers"
                @change="onPickPrinter"
              />

              <!-- 预览中 -->
              <div v-if="dispatchDialog.previewLoading" class="mt-4 text-sm text-zinc-400 flex items-center gap-2">
                <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-300 border-t-zinc-600 animate-spin" />
                正在对比 AMS 料盘…
              </div>

              <!-- AMS 选料（复用监控页四格，点选） -->
              <div v-if="dispatchDialog.preview" class="mt-4">
                <!-- 无期望：直接下发，四格仅展示 -->
                <div v-if="!expectedFilaments.length" class="text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/30 rounded-lg px-3 py-2 mb-2">
                  gcode 无 AMS 料盘信息，将直接下发（不校验料盘）。下方为该机当前 AMS：
                </div>
                <template v-else>
                  <!-- 本次需要的 extruder（可点切换；单 extruder 时仅一个） -->
                  <div class="flex items-center gap-1.5 flex-wrap mb-2">
                    <span class="text-xs text-zinc-400">本次需要</span>
                    <button
                      v-for="m in mappings" :key="m.extruder_id" type="button"
                      @click="selectExtruder(m.extruder_id)"
                      :class="[
                        'flex items-center gap-1 px-2 py-0.5 rounded-lg border text-xs transition',
                        String(m.extruder_id) === String(dispatchDialog.currentEid)
                          ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300'
                          : 'border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-300 hover:border-zinc-300'
                      ]"
                    >
                      <span class="font-medium">E{{ m.extruder_id }}</span>
                      <span class="inline-block w-2.5 h-2.5 rounded-full border border-black/10 dark:border-white/15"
                            :style="{ background: m.expected.color || 'transparent' }"></span>
                      <span>{{ m.expected.type || '?' }}</span>
                      <span v-if="m.expected.used_g" class="text-zinc-400">~{{ Math.round(m.expected.used_g) }}g</span>
                      <span v-if="dispatchDialog.manualPicks[m.extruder_id] != null" class="text-emerald-500">✓</span>
                      <span v-else class="text-amber-500">待选</span>
                    </button>
                  </div>
                  <!-- 总览 -->
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-xs text-zinc-500">点选 AMS 料盘<span v-if="mappings.length > 1"> · 正在为 E{{ dispatchDialog.currentEid }} 选</span></span>
                    <span v-if="effectiveMapping()" class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">全部配齐，可下发</span>
                    <span v-else class="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">有待选料盘</span>
                  </div>
                </template>

                <!-- 四格点选 -->
                <AmsSlotGrid
                  :trays="amsTrays"
                  :selectable="expectedFilaments.length > 0"
                  :selected-id="selectedSlotId"
                  :recommended-id="recommendedSlotId"
                  :assigned-labels="assignedLabels"
                  @pick="onPickSlot"
                />
                <p class="mt-1.5 text-[10px] text-zinc-400 leading-relaxed">
                  实色 = 有料可选 · 虚线 = 空槽 · <span class="text-sky-500">蓝框✓ = 推荐匹配</span> · 框内为余量百分比，悬停看克数
                </p>
              </div>

              <div class="flex justify-end gap-2 mt-5">
                <AppButton variant="ghost" @click="dispatchDialog.open = false">取消</AppButton>
                <AppButton :loading="dispatching" :disabled="!canDispatch()" @click="doDispatch">下发</AppButton>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </TransitionRoot>
  </div>
</template>
