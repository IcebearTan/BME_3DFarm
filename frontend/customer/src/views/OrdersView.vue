<script setup>
import { ref, onMounted } from 'vue'
import {
  Dialog, DialogPanel, DialogTitle,
  TransitionRoot, TransitionChild,
} from '@headlessui/vue'
import { useRouter, useRoute } from 'vue-router'
import OrderCard from '@/components/OrderCard.vue'
import AppButton from '@/components/AppButton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ordersApi } from '@/api/orders'
import { toast } from '@/composables/useToast'

const router = useRouter()
const route = useRoute()
const orders = ref([])
const loading = ref(false)
const detail = ref(null)
const acting = ref(false)

async function load() {
  loading.value = true
  try {
    const res = await ordersApi.list({ page: 1, per_page: 50 })
    orders.value = res.data.items
  } finally {
    loading.value = false
  }
}

async function openDetail(o) {
  try {
    detail.value = (await ordersApi.detail(o.id)).data
  } catch {
    /* 静默 */
  }
}

async function refreshDetail() {
  if (detail.value) {
    detail.value = (await ordersApi.detail(detail.value.id)).data
  }
}

async function doConfirm(o) {
  acting.value = true
  try {
    const res = await ordersApi.confirm(o.id)
    if (res.code === 200) {
      toast.success('已确认，额度已冻结')
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

async function doCancel(o) {
  if (!window.confirm('确认取消该订单？')) return
  acting.value = true
  try {
    const res = await ordersApi.cancel(o.id)
    if (res.code === 200) {
      toast.success('订单已取消')
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

onMounted(async () => {
  await load()
  // 通知点击跳来时带 ?order=ID，自动展开该订单详情
  const oid = Number(route.query.order)
  if (oid) {
    const o = orders.value.find(x => x.id === oid)
    if (o) openDetail(o)
  }
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">我的订单</h1>
        <p class="text-sm text-zinc-400 mt-1">查看订单状态与进度</p>
      </div>
      <AppButton @click="router.push({ name: 'new-order' })">新建订单</AppButton>
    </div>

    <div v-if="loading" class="py-20 text-center text-sm text-zinc-400">加载中…</div>
    <div v-else-if="!orders.length" class="py-20 text-center">
      <p class="text-sm text-zinc-400">还没有订单</p>
      <AppButton class="mt-4" @click="router.push({ name: 'new-order' })">去下单</AppButton>
    </div>
    <div v-else class="grid sm:grid-cols-2 gap-4">
      <OrderCard
        v-for="o in orders" :key="o.id" :order="o"
        @click="openDetail(o)" @confirm="doConfirm(o)" @cancel="doCancel(o)"
      />
    </div>

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
              class="w-full max-w-md rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-100 dark:border-zinc-800 shadow-xl p-6"
            >
              <DialogTitle class="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
                订单详情
              </DialogTitle>
              <div v-if="detail" class="mt-4 space-y-3 text-sm">
                <div class="flex justify-between">
                  <span class="text-zinc-400">订单号</span>
                  <span class="font-mono text-zinc-700 dark:text-zinc-300">{{ detail.order_no }}</span>
                </div>
                <div class="flex justify-between items-center">
                  <span class="text-zinc-400">状态</span>
                  <StatusBadge :status="detail.public_status" />
                </div>
                <div class="flex justify-between">
                  <span class="text-zinc-400">材料</span>
                  <span>{{ detail.material }}</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-zinc-400">数量</span>
                  <span>{{ detail.quantity }} 件</span>
                </div>
                <div v-if="detail.estimated_credit" class="flex justify-between">
                  <span class="text-zinc-400">报价</span>
                  <span class="font-medium">{{ detail.estimated_credit }} credit</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-zinc-400">冻结</span>
                  <span>{{ detail.frozen_credit }}</span>
                </div>
                <div v-if="detail.files?.length" class="flex justify-between">
                  <span class="text-zinc-400">文件</span>
                  <span class="truncate ml-4">{{ detail.files[0].original_filename }}</span>
                </div>
                <div v-if="detail.fail_reason" class="rounded-lg bg-rose-50 dark:bg-rose-950/30 px-3 py-2 text-sm text-rose-700 dark:text-rose-300">
                  <p class="text-xs text-rose-500 mb-0.5">失败原因</p>
                  {{ detail.fail_reason }}
                </div>
                <div v-if="detail.completion_note" class="rounded-lg bg-emerald-50 dark:bg-emerald-950/30 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
                  <p class="text-xs text-emerald-500 mb-0.5">取件 / 发货</p>
                  {{ detail.completion_note }}
                </div>
              </div>
              <div class="mt-6 flex justify-end gap-2">
                <AppButton
                  v-if="detail?.status === 'WAITING_CONFIRM'" :loading="acting"
                  @click="doConfirm(detail)"
                >
                  确认下单
                </AppButton>
                <AppButton variant="ghost" @click="detail = null">关闭</AppButton>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </TransitionRoot>
  </div>
</template>
