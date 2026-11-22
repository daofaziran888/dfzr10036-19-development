import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 延迟分段与恢复编排专用接口 ----

export type LagQuery = {
  data_stream?: string
  sample_time?: string
  pos_from?: number
  pos_to?: number
  generation?: number
}

function toQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.set(key, String(value))
  })
  return search.toString()
}

export async function listLagObservations(query: LagQuery): Promise<{
  ok: boolean
  items: EntryRow[]
  total: number
  coverage: { covered_positions: number[]; missing_positions: number[]; complete: boolean } | null
}> {
  const response = await request(`/api/lag-observations?${toQuery(query)}`)
  if (!response.ok) throw new Error('历史延迟观测读取失败')
  return await response.json()
}

export async function listLagSegments(query: LagQuery): Promise<{ ok: boolean; items: EntryRow[]; total: number }> {
  const response = await request(`/api/lag-segments?${toQuery(query)}`)
  if (!response.ok) throw new Error('延迟分段明细读取失败')
  return await response.json()
}

export async function listSegmentObservations(segmentId: number): Promise<{ ok: boolean; items: EntryRow[]; total: number }> {
  const response = await request(`/api/lag-segments/${segmentId}/observations`)
  if (!response.ok) throw new Error('分段历史观测读取失败')
  return await response.json()
}

export async function confirmSegment(segmentId: number, conclusion: string): Promise<ActionResult & { entry: EntryRow | null }> {
  const response = await request(`/api/lag-segments/${segmentId}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ conclusion }),
  })
  return await response.json()
}

export async function returnSegment(segmentId: number, reason: string): Promise<ActionResult & { entry: EntryRow | null }> {
  const response = await request(`/api/lag-segments/${segmentId}/return`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
  return await response.json()
}

export async function listRecoveries(): Promise<{ ok: boolean; items: EntryRow[]; total: number }> {
  const response = await request('/api/lag-recoveries')
  if (!response.ok) throw new Error('恢复批次读取失败')
  return await response.json()
}

export async function getRecovery(recoveryId: number): Promise<{ ok: boolean; entry: EntryRow; segments: EntryRow[] }> {
  const response = await request(`/api/lag-recoveries/${recoveryId}`)
  if (!response.ok) throw new Error('恢复批次读取失败')
  return await response.json()
}

export async function selectRecoverySegments(recoveryId: number, segmentIds: number[]): Promise<ActionResult> {
  const response = await request(`/api/lag-recoveries/${recoveryId}/select`, {
    method: 'POST',
    body: JSON.stringify({ segment_ids: segmentIds }),
  })
  return await response.json()
}

export async function interruptRecovery(recoveryId: number): Promise<ActionResult> {
  const response = await request(`/api/lag-recoveries/${recoveryId}/interrupt`, { method: 'POST' })
  return await response.json()
}

export async function advanceRecoveryWindow(
  recoveryId: number,
  newLower: number,
  newUpper?: number,
): Promise<ActionResult> {
  const response = await request(`/api/lag-recoveries/${recoveryId}/window`, {
    method: 'POST',
    body: JSON.stringify({ new_lower: newLower, new_upper: newUpper }),
  })
  return await response.json()
}

export async function resumeRecovery(recoveryId: number): Promise<{
  ok: boolean
  message: string
  report: {
    registered: Array<Record<string, unknown>>
    returned: Array<Record<string, unknown>>
    invalid: Array<Record<string, unknown>>
    duplicated: Array<Record<string, unknown>>
  }
}> {
  const response = await request(`/api/lag-recoveries/${recoveryId}/resume`, { method: 'POST' })
  if (!response.ok) throw new Error('恢复续办失败')
  return await response.json()
}

export async function getSegmentDossier(segmentId: number): Promise<{
  ok: boolean
  segment: EntryRow
  observations: EntryRow[]
  invalid_range: { lower: number | null; upper: number | null; note: string }
  dispatch: EntryRow | null
}> {
  const response = await request(`/api/lag-recoveries/segments/${segmentId}/dossier`)
  if (!response.ok) throw new Error('复核档案读取失败')
  return await response.json()
}

export async function listLagReplays(query: { data_stream?: string; generation?: number } = {}): Promise<{
  ok: boolean
  items: EntryRow[]
  total: number
}> {
  const response = await request(`/api/lag-replays?${toQuery(query)}`)
  if (!response.ok) throw new Error('重放派工读取失败')
  return await response.json()
}
