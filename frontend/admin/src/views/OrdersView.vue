<script setup>
import { ref, reactive, onMounted } from 'vue'
import {
  Dialog, DialogPanel, DialogTitle,
  TransitionRoot, TransitionChild,
} from '@headlessui/vue'
import AppButton from '@/components/AppButton.vue'
import AppInput from '@/components/AppInput.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppCard from '@/components/AppCard.vue'
import PreviewImage from '@/components/PreviewImage.vue'
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
const amountDialog = reactive({ open: false, action: '', order: null, amount: '', title: '' })

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

// 下发 Bambuddy（Phase 4：打印机下拉 + AMS 校验 + ams_mapping）
const dispatchDialog = reactive({
  open: false, order: null,
  printerId: '',
  printers: [],
  preview: null,        // {printer, expected_filaments, match_result}
  manualPicks: {},      // extruder_id -> tray_id（admin 手选覆盖不匹配项）
  previewLoading: false,
})
const dispatching = ref(false)

async function openDispatch(o) {
  dispatchDialog.order = o
  dispatchDialog.printerId = ''
  dispatchDialog.preview = null
  dispatchDialog.manualPicks = {}
  dispatchDialog.printers = []
  dispatchDialog.open = true
  // 拉启用打印机列表（下拉用）
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
  if (!dispatchDialog.printerId) return
  dispatchDialog.previewLoading = true
  try {
    const res = await adminApi.dispatchPreview(
      dispatchDialog.order.id, Number(dispatchDialog.printerId)
    )
    if (res.code === 200) dispatchDialog.preview = res.data
    else toast.error(res.message || '预览失败')
  } catch (e) {
    toast.error(e.response?.data?.message || '预览失败')
  } finally {
    dispatchDialog.previewLoading = false
  }
}

function matchBadge(mt) {
  return {
    exact: { label: '完全匹配', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' },
    color: { label: '色匹配', cls: 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300' },
    type: { label: '材料匹配', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' },
    none: { label: '不匹配', cls: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300' },
  }[mt] || { label: mt || '-', cls: 'bg-zinc-100 text-zinc-500' }
}

function trayOptions(m) {
  // 实际匹配 tray 排首位 + 候选，去重 by tray_id
  const opts = []
  const seen = new Set()
  if (m.actual_tray_id != null && m.actual_tray) {
    opts.push({
      tray_id: m.actual_tray_id, type: m.actual_tray.type,
      color: m.actual_tray.color, match_type: m.match_type,
    })
    seen.add(m.actual_tray_id)
  }
  for (const a of m.alternatives || []) {
    if (!seen.has(a.tray_id)) {
      opts.push(a)
      seen.add(a.tray_id)
    }
  }
  return opts
}
function selectedTrayId(m) {
  if (dispatchDialog.manualPicks[m.extruder_id] != null) return dispatchDialog.manualPicks[m.extruder_id]
  return m.actual_tray_id != null ? m.actual_tray_id : ''
}
function onPickTray(eid, val) {
  dispatchDialog.manualPicks = {
    ...dispatchDialog.manualPicks,
    [eid]: val === '' ? null : Number(val),
  }
}

function effectiveMapping() {
  // 全匹配用 match_result.ams_mapping；否则按手选组装；无期望返回 null（后端不校验）
  const mr = dispatchDialog.preview?.match_result
  if (!mr || !mr.mappings || !mr.mappings.length) return null
  const picks = {}
  for (const m of mr.mappings) {
    const t = selectedTrayId(m)
    if (t === '' || t == null) return null  // 有 extruder 未配齐
    picks[m.extruder_id] = t
  }
  const ordered = mr.mappings
    .slice()
    .sort((a, b) => Number(a.extruder_id) - Number(b.extruder_id))
  return ordered.map((m) => picks[m.extruder_id])
}
function canDispatch() {
  if (!dispatchDialog.printerId) return false
  const exp = dispatchDialog.preview?.expected_filaments || []
  if (!exp.length) return true  // 无期望 → 不校验，直接下发
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
function fail(o) {
  return simpleAction(o, '标记失败', (id) => adminApi.fail(id))
}
function cancel(o) {
  if (!window.confirm('确认取消该订单？冻结额度会释放')) return
  return simpleAction(o, '取消', (id) => adminApi.cancel(id))
}

function openAmount(o, action, title, defaultAmount) {
  amountDialog.action = action
  amountDialog.order = o
  amountDialog.amount = defaultAmount != null ? String(defaultAmount) : ''
  amountDialog.title = title
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
    if (amountDialog.action === 'quote') {
      res = await adminApi.quote(amountDialog.order.id, { estimated_credit: amt })
    } else if (amountDialog.action === 'complete') {
      res = await adminApi.complete(amountDialog.order.id, { actual_credit: amt })
    } else {
      res = await adminApi.refund(amountDialog.order.id, { amount: amt })
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
                    v-if="o.status === 'PRINTING'" variant="warning" size="xs" :loading="acting"
                    @click="fail(o)"
                  >失败</AppButton>
                  <AppButton
                    v-if="['PRINT_COMPLETED', 'QC_PENDING', 'NEED_REVIEW'].includes(o.status)"
                    variant="ghost" size="xs" @click="openRefund(o)"
                  >退款</AppButton>
                  <AppButton v-if="canCancel(o.status)" variant="ghost" size="xs" @click="cancel(o)">取消</AppButton>
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
              <div class="flex justify-end gap-2 mt-5">
                <AppButton variant="ghost" @click="amountDialog.open = false">取消</AppButton>
                <AppButton :loading="acting" @click="submitAmount">确认</AppButton>
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

              <!-- 打印机下拉 -->
              <label class="block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">打印机</label>
              <select
                v-model="dispatchDialog.printerId"
                @change="onPickPrinter"
                class="w-full h-10 px-3 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/10 dark:focus:ring-white/10"
              >
                <option value="">请选择…</option>
                <option v-for="p in dispatchDialog.printers" :key="p.id" :value="p.bambuddy_printer_id">
                  {{ p.public_name }}（bambuddy #{{ p.bambuddy_printer_id }}）
                </option>
              </select>

              <!-- 预览中 -->
              <div v-if="dispatchDialog.previewLoading" class="mt-4 text-sm text-zinc-400 flex items-center gap-2">
                <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-300 border-t-zinc-600 animate-spin" />
                正在对比 AMS 料盘…
              </div>

              <!-- AMS 对比 -->
              <div v-if="dispatchDialog.preview" class="mt-4">
                <div v-if="!(dispatchDialog.preview.expected_filaments || []).length" class="text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/30 rounded-lg px-3 py-2">
                  gcode 无 AMS 料盘信息，将直接下发（不校验料盘）。
                </div>
                <div v-else>
                  <div class="flex items-center justify-between mb-2">
                    <span class="text-sm font-medium text-zinc-700 dark:text-zinc-300">AMS 料盘对比</span>
                    <span
                      v-if="dispatchDialog.preview.match_result.matched"
                      class="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                    >全部匹配，可直接下发</span>
                    <span v-else class="text-xs px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300">有不匹配，请手选替代料盘</span>
                  </div>
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-zinc-400 border-b border-zinc-100 dark:border-zinc-800">
                        <th class="py-1.5 pr-2 font-medium">挤出机</th>
                        <th class="py-1.5 pr-2 font-medium">期望</th>
                        <th class="py-1.5 pr-2 font-medium">实际料盘</th>
                        <th class="py-1.5 pr-2 font-medium">匹配</th>
                        <th class="py-1.5 font-medium">选择料盘</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-zinc-50 dark:divide-zinc-800/60">
                      <tr v-for="m in dispatchDialog.preview.match_result.mappings" :key="m.extruder_id">
                        <td class="py-2 pr-2 text-zinc-500">#{{ m.extruder_id }}</td>
                        <td class="py-2 pr-2">
                          <div class="flex items-center gap-1.5">
                            <span class="inline-block w-3.5 h-3.5 rounded-full border border-zinc-300 dark:border-zinc-600" :style="{ background: m.expected.color || 'transparent' }" />
                            <span>{{ m.expected.type || '?' }}</span>
                          </div>
                        </td>
                        <td class="py-2 pr-2">
                          <div v-if="m.actual_tray" class="flex items-center gap-1.5">
                            <span class="inline-block w-3.5 h-3.5 rounded-full border border-zinc-300 dark:border-zinc-600" :style="{ background: m.actual_tray.color || 'transparent' }" />
                            <span>{{ m.actual_tray.type || '?' }}（槽{{ m.actual_tray.tray_id }}）</span>
                          </div>
                          <span v-else class="text-zinc-400">—</span>
                        </td>
                        <td class="py-2 pr-2">
                          <span class="px-1.5 py-0.5 rounded-full text-[10px]" :class="matchBadge(m.match_type).cls">{{ matchBadge(m.match_type).label }}</span>
                        </td>
                        <td class="py-2">
                          <select
                            :value="selectedTrayId(m)"
                            @change="onPickTray(m.extruder_id, $event.target.value)"
                            class="h-8 px-2 rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-xs focus:outline-none"
                          >
                            <option value="">（未选）</option>
                            <option v-for="t in trayOptions(m)" :key="t.tray_id" :value="t.tray_id">
                              槽{{ t.tray_id }} · {{ t.type || '?' }} · {{ t.match_type }}
                            </option>
                          </select>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
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
