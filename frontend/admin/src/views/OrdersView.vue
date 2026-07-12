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

// Bambuddy 任务绑定（Phase 2）
const bambuddyJob = ref(null)
const bindForm = reactive({ bambuddy_printer_id: '', bambuddy_archive_id: '' })
const binding = ref(false)

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
async function doBind() {
  if (!bindForm.bambuddy_printer_id) {
    toast.error('请填 printer_id')
    return
  }
  binding.value = true
  try {
    const res = await adminApi.bindBambuddy(detail.value.id, {
      bambuddy_printer_id: Number(bindForm.bambuddy_printer_id),
      bambuddy_archive_id: bindForm.bambuddy_archive_id
        ? Number(bindForm.bambuddy_archive_id)
        : undefined,
    })
    if (res.code === 200) {
      toast.success('已绑定')
      bambuddyJob.value = res.data
      bindForm.bambuddy_printer_id = ''
      bindForm.bambuddy_archive_id = ''
    } else {
      toast.error(res.message || '绑定失败')
    }
  } catch (e) {
    toast.error(e.response?.data?.message || '失败')
  } finally {
    binding.value = false
  }
}

// 下发 Bambuddy（Phase 3）
const dispatchDialog = reactive({ open: false, order: null, printer_id: '' })
const dispatching = ref(false)
function openDispatch(o) {
  dispatchDialog.order = o
  dispatchDialog.printer_id = ''
  dispatchDialog.open = true
}
async function doDispatch() {
  if (!dispatchDialog.printer_id) {
    toast.error('请填 printer_id')
    return
  }
  dispatching.value = true
  try {
    const res = await adminApi.dispatchOrder(
      dispatchDialog.order.id,
      Number(dispatchDialog.printer_id)
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
              <th class="px-3 py-2 font-medium">状态</th>
              <th class="px-3 py-2 font-medium text-right">报价/冻结</th>
              <th class="px-3 py-2 font-medium">创建</th>
              <th class="px-5 py-2 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-zinc-50 dark:divide-zinc-800/60">
            <tr v-if="loading">
              <td colspan="7" class="py-10 text-center text-zinc-400">加载中…</td>
            </tr>
            <tr v-else-if="!orders.length">
              <td colspan="7" class="py-10 text-center text-zinc-400">暂无订单</td>
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
              <td class="px-3 py-3 text-zinc-500">#{{ o.user_id }}</td>
              <td class="px-3 py-3">
                {{ o.material
                }}<span class="text-zinc-400"> · {{ o.quantity }}</span>
              </td>
              <td class="px-3 py-3"><StatusBadge :status="o.public_status" /></td>
              <td class="px-3 py-3 text-right text-zinc-600 dark:text-zinc-400">
                <span v-if="o.estimated_credit">{{ o.estimated_credit }}</span>
                <span v-if="o.frozen_credit && o.frozen_credit !== '0.00' && o.frozen_credit !== '0'">
                  / 冻{{ o.frozen_credit }}
                </span>
              </td>
              <td class="px-3 py-3 text-xs text-zinc-400 whitespace-nowrap">{{ fmtDate(o.created_at) }}</td>
              <td class="px-5 py-3">
                <div class="flex flex-wrap gap-1 justify-end">
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
              <div v-if="detail" class="space-y-2 text-sm">
                <div class="flex justify-between"><span class="text-zinc-400">订单号</span><span class="font-mono">{{ detail.order_no }}</span></div>
                <div class="flex justify-between"><span class="text-zinc-400">客户 ID</span><span>#{{ detail.user_id }}</span></div>
                <div class="flex justify-between items-center"><span class="text-zinc-400">状态</span><StatusBadge :status="detail.public_status" /></div>
                <div class="flex justify-between"><span class="text-zinc-400">材料/数量</span><span>{{ detail.material }} · {{ detail.quantity }}件</span></div>
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
                <!-- Bambuddy 任务绑定（Phase 2）-->
                <div class="pt-2 border-t border-zinc-100 dark:border-zinc-800">
                  <span class="text-zinc-400">Bambuddy 任务</span>
                  <p v-if="bambuddyJob" class="mt-1 text-xs text-zinc-500">
                    printer #{{ bambuddyJob.bambuddy_printer_id }}
                    <span v-if="bambuddyJob.bambuddy_archive_id">· archive {{ bambuddyJob.bambuddy_archive_id }}</span>
                    · {{ bambuddyJob.mapping_confidence }}
                  </p>
                  <p v-else class="mt-1 text-xs text-zinc-400">未绑定（Poller/Webhook 需先绑定才能匹配）</p>
                  <div class="mt-2 flex gap-2">
                    <input
                      v-model="bindForm.bambuddy_printer_id" placeholder="printer_id"
                      class="w-28 h-8 px-2 rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-xs focus:outline-none"
                    />
                    <input
                      v-model="bindForm.bambuddy_archive_id" placeholder="archive_id"
                      class="w-28 h-8 px-2 rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-xs focus:outline-none"
                    />
                    <AppButton size="xs" :loading="binding" @click="doBind">绑定</AppButton>
                  </div>
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

    <!-- 下发 Bambuddy Dialog（Phase 3） -->
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
              class="w-full max-w-sm rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-xl p-6"
            >
              <DialogTitle class="text-lg font-semibold mb-1">下发 Bambuddy 打印</DialogTitle>
              <p class="text-xs text-zinc-400 mb-4">系统自动上传文件到 archive + 加入打印队列</p>
              <AppInput
                v-model="dispatchDialog.printer_id" type="number" label="Bambuddy printer_id"
                placeholder="打印机 ID" required
              />
              <div class="flex justify-end gap-2 mt-5">
                <AppButton variant="ghost" @click="dispatchDialog.open = false">取消</AppButton>
                <AppButton :loading="dispatching" @click="doDispatch">下发</AppButton>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </TransitionRoot>
  </div>
</template>
