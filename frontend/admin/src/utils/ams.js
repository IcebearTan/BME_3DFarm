/** AMS 料盘渲染工具（admin 端共用）：颜色解析 / 颜色名推断 / 全名 / tray 归一化。
 * 从 PrintersView 抽出，供 AmsSlotGrid / PrintersView / OrdersView 复用。 */

// Bambuddy tray_color 是无 # 的 RGB hex（可能带 FF 透明位），转 #rrggbb；无效返回 null
export function trayHex(c) {
  return c && c.length >= 6 ? '#' + c.slice(0, 6) : null
}

// Bambu 协议只有 hex、没有颜色名——用参考色最近邻（RGB 欧氏距离）推断中文名
const COLOR_REF = [
  ['FFFFFF', '白'], ['000000', '黑'], ['D9D9D9', '浅灰'], ['5A5A5A', '深灰'],
  ['DB2F2F', '红'], ['F26B1F', '橙'], ['FFCF3F', '黄'], ['D4A04B', '金'],
  ['1E9B54', '绿'], ['0B8AB8', '青'], ['1E9BFF', '蓝'], ['7C5BBA', '紫'],
  ['E66FA5', '粉'], ['8B5A2B', '棕'],
]
export function colorName(c) {
  if (!c || c.length < 6) return null
  const n = (s, o) => parseInt(s.slice(o, o + 2), 16)
  const r = n(c, 0), g = n(c, 2), b = n(c, 4)
  let best = null, bestD = Infinity
  for (const [ref, name] of COLOR_REF) {
    const d = (n(ref, 0) - r) ** 2 + (n(ref, 2) - g) ** 2 + (n(ref, 4) - b) ** 2
    if (d < bestD) { bestD = d; best = name }
  }
  return best
}

// 耗材全名：品牌 + 类型 + 子系列，如 "Bambu PLA Matte"
export function fullName(t) {
  const parts = []
  if (t.brand) parts.push(t.brand)
  const ts = [t.type, t.subtype].filter(Boolean).join(' ')
  if (ts) parts.push(ts)
  return parts.join(' ') || null
}

// 把 Bambuddy 原始 tray 字段归一化成统一渲染结构。
// 注意：tray_id（1-based 物理槽，与后端 ams_mapping 同体系）由调用方按数组下标 +1 给定，
// 不用 Bambuddy 的 tray.id（0-based）。
export function normTray(t, idx) {
  return {
    slot: t.id,
    tray_id: idx + 1,
    color: t.tray_color,
    type: t.tray_type,
    subtype: t.tray_sub_brands,
    brand: t.tray_brand,
    remain: t.remain != null && t.remain >= 0 ? t.remain : null,
    remain_g: t.remain_g != null ? t.remain_g : null,
    label_weight_g: t.label_weight_g != null ? t.label_weight_g : null,
  }
}
