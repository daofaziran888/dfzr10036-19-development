<template>
  <section class="page lag-recovery-page">
    <header class="page-head">
      <div>
        <h2>延迟分段与恢复编排</h2>
        <p class="page-desc">
          按数据流、采样时间和日志位点筛选历史观测与延迟分段，核对与复核均按恢复代次切分；
          缺采样不跨代填平，断点恢复续办只登记仍在有效恢复范围内的分段，同段重送只派一次。
        </p>
      </div>
    </header>

    <div class="tab-row">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="btn"
        :class="{ primary: activeTab === tab.key }"
        type="button"
        :data-testid="`tab-${tab.key}`"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <p v-if="errorMessage" class="error-text" data-testid="error-banner">{{ errorMessage }}</p>
    <p v-if="infoMessage" class="info-text" data-testid="info-banner">{{ infoMessage }}</p>

    <!-- 1. 历史延迟观测 -->
    <div v-if="activeTab === 'observations'" class="panel">
      <form class="filter-bar" @submit.prevent="loadObservations">
        <label class="filter-item">
          <span>数据流</span>
          <input v-model="obsQuery.data_stream" data-testid="obs-filter-stream" placeholder="如 order_events_cdc" />
        </label>
        <label class="filter-item">
          <span>采样时间</span>
          <input v-model="obsQuery.sample_time" data-testid="obs-filter-time" placeholder="2026-11-22 09:30:00" />
        </label>
        <label class="filter-item">
          <span>恢复代次</span>
          <input v-model.number="obsQuery.generation" data-testid="obs-filter-generation" type="number" min="1" />
        </label>
        <label class="filter-item">
          <span>日志位点下界</span>
          <input v-model.number="obsQuery.pos_from" data-testid="obs-filter-from" type="number" />
        </label>
        <label class="filter-item">
          <span>日志位点上界</span>
          <input v-model.number="obsQuery.pos_to" data-testid="obs-filter-to" type="number" />
        </label>
        <button class="btn" data-testid="obs-query" type="submit">查询历史观测</button>
      </form>

      <div v-if="obsCoverage" class="coverage-card" data-testid="obs-coverage">
        <span>覆盖位点：{{ obsCoverage.covered_positions.join('、') || '无' }}</span>
        <span :class="{ 'warn-text': !obsCoverage.complete }">
          缺样位点：{{ obsCoverage.missing_positions.join('、') || '无' }}
        </span>
        <span :class="{ 'warn-text': !obsCoverage.complete }">
          完整性：{{ obsCoverage.complete ? '本代完整' : '存在缺样，不跨代填平' }}
        </span>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in observationColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in observations" :key="String(row.id)" :data-testid="`obs-row-${row.id}`">
            <td v-for="column in observationColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!observations.length">
            <td :colspan="observationColumns.length" class="empty-state">该代次/区间没有历史观测，不会用其他代次数据填平</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 2. 延迟分段核对 -->
    <div v-if="activeTab === 'segments'" class="panel">
      <form class="filter-bar" @submit.prevent="loadSegments">
        <label class="filter-item">
          <span>数据流</span>
          <input v-model="segQuery.data_stream" data-testid="seg-filter-stream" />
        </label>
        <label class="filter-item">
          <span>采样时间</span>
          <input v-model="segQuery.sample_time" data-testid="seg-filter-time" />
        </label>
        <label class="filter-item">
          <span>恢复代次</span>
          <input v-model.number="segQuery.generation" data-testid="seg-filter-generation" type="number" />
        </label>
        <label class="filter-item">
          <span>位点下界</span>
          <input v-model.number="segQuery.pos_from" data-testid="seg-filter-from" type="number" />
        </label>
        <label class="filter-item">
          <span>位点上界</span>
          <input v-model.number="segQuery.pos_to" data-testid="seg-filter-to" type="number" />
        </label>
        <button class="btn" data-testid="seg-query" type="submit">查询延迟分段</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in segmentColumns" :key="column">{{ column }}</th>
            <th>当前状态</th>
            <th>核对操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in segments" :key="String(row.id)" :data-testid="`seg-row-${row.id}`" :data-status="String(row.status)">
            <td v-for="column in segmentColumns" :key="column">{{ row[column] || '—' }}</td>
            <td>{{ row.status }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`seg-obs-${row.id}`" @click="openObservations(row)">
                当时观测
              </button>
              <button
                class="link"
                type="button"
                :data-testid="`seg-confirm-${row.id}`"
                :disabled="row.status === '已登记' || row.status === '已失效' || row.status === '已核对'"
                @click="openConfirm(row)"
              >
                确认核对
              </button>
              <button
                class="link"
                type="button"
                :data-testid="`seg-return-${row.id}`"
                :disabled="row.status === '已登记'"
                @click="openReturn(row)"
              >
                退回待核
              </button>
            </td>
          </tr>
          <tr v-if="!segments.length">
            <td :colspan="segmentColumns.length + 2" class="empty-state">没有匹配的延迟分段</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 3. 恢复编排续办 -->
    <div v-if="activeTab === 'recovery'" class="panel">
      <article v-if="recovery" class="recovery-card" data-testid="recovery-card">
        <header class="recovery-head">
          <div>
            <strong>{{ recovery.entry['编排编号'] }} · {{ recovery.entry['数据流'] }} · 第{{ recovery.entry['恢复代次'] }}代</strong>
            <p>检查点 {{ recovery.entry['检查点编号'] }}（位点 {{ recovery.entry['检查点位点'] }}）
              ｜有效恢复范围 {{ recovery.entry['有效下界'] }}-{{ recovery.entry['有效上界'] }}
              ｜状态：{{ recovery.entry['status'] }}{{ recovery.entry['中断标记'] ? '（已中断）' : '' }}
            </p>
          </div>
        </header>
        <p class="info-text">{{ recovery.entry['最近决定'] }}</p>

        <div v-if="recovery.segments.length" class="select-list">
          <h4>已选择分段（中断后读回）</h4>
          <label v-for="seg in recovery.segments" :key="String(seg.id)" class="select-item">
            <input type="checkbox" :value="Number(seg.id)" v-model="selectedIds" :data-testid="`pick-${seg.id}`" />
            <span>{{ seg['分段编号'] }}｜第{{ seg['恢复代次'] }}代｜{{ seg['日志下界'] }}-{{ seg['日志上界'] }}
              ｜{{ seg.status }}｜{{ seg['核对结论'] || '未核对' }}</span>
          </label>
        </div>
        <p v-else class="empty-state">批次尚未选择分段，可先到「延迟分段」页完成核对再回来选择。</p>

        <div class="page-actions">
          <button class="btn primary" data-testid="recovery-save-select" type="button" @click="saveSelection">
            保存选择
          </button>
          <button class="btn" data-testid="recovery-interrupt" type="button" @click="doInterrupt">登记前中断</button>
          <label class="filter-item">
            <span>窗口下界推进到</span>
            <input v-model.number="windowLower" data-testid="window-lower" type="number" />
          </label>
          <button class="btn" data-testid="window-advance" type="button" @click="doAdvanceWindow">推进保留窗口</button>
          <button class="btn primary" data-testid="recovery-resume" type="button" @click="doResume">沿用原选择续办</button>
        </div>

        <div v-if="resumeReport" class="report-card" data-testid="resume-report">
          <h4>续办结果</h4>
          <p>直接登记：{{ resumeReport.registered.length }} 段；退回待核：{{ resumeReport.returned.length }} 段；
            失效：{{ resumeReport.invalid.length }} 段；重送去重：{{ resumeReport.duplicated.length }} 段</p>
          <ul>
            <li v-for="(item, idx) in resumeReport.registered" :key="`r-${idx}`" class="ok-text">
              登记 {{ item['分段编号'] }}：派工 {{ item['派工编号'] }}，登记位点 {{ item['登记位点'] }}
            </li>
            <li v-for="(item, idx) in resumeReport.returned" :key="`t-${idx}`" class="warn-text">
              退回 {{ item['分段编号'] }}：失效范围 {{ item['失效范围'] }}，待重放 {{ item['待重放区间'] }}，
              原观测 {{ item['核对观测'] }}
            </li>
            <li v-for="(item, idx) in resumeReport.duplicated" :key="`d-${idx}`" class="info-text">
              重送去重 {{ item['分段编号'] }}：沿用派工 {{ item['派工编号'] }}（{{ item['登记位点'] }}）
            </li>
          </ul>
        </div>
      </article>
    </div>

    <!-- 4. 重放派工与复核档案 -->
    <div v-if="activeTab === 'replays'" class="panel">
      <div class="page-actions">
        <button class="btn" data-testid="replay-refresh" type="button" @click="loadReplays">刷新派工</button>
        <label class="filter-item">
          <span>分段ID查复核档案</span>
          <input v-model.number="dossierId" data-testid="dossier-id" type="number" />
        </label>
        <button class="btn" data-testid="dossier-query" type="button" @click="loadDossier">查询复核档案</button>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in replayColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in replays" :key="String(row.id)" :data-testid="`replay-row-${row.id}`">
            <td v-for="column in replayColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!replays.length">
            <td :colspan="replayColumns.length" class="empty-state">尚无重放派工登记</td>
          </tr>
        </tbody>
      </table>

      <article v-if="dossier" class="recovery-card" data-testid="dossier-card">
        <h4>分段 {{ dossier.segment['分段编号'] }} 复核档案</h4>
        <p>状态：{{ dossier.segment.status }}｜核对结论：{{ dossier.segment['核对结论'] || '—' }}</p>
        <p :class="{ 'warn-text': dossier.invalid_range.lower !== null }">
          失效范围：{{ dossier.invalid_range.lower === null ? '无' : `${dossier.invalid_range.lower}-${dossier.invalid_range.upper}` }}
          {{ dossier.invalid_range.note }}
        </p>
        <p>实际登记位点：{{ dossier.dispatch ? dossier.dispatch['登记位点'] : '未登记' }}
          ｜派工编号：{{ dossier.dispatch ? dossier.dispatch['派工编号'] : '—' }}</p>
        <p>当时观测：{{ dossier.observations.map((o) => o['观测编号']).join('、') || '无本代观测' }}</p>
      </article>
    </div>

    <!-- 分段当时观测弹窗 -->
    <div v-if="obsModal.segment" class="modal-backdrop" data-testid="obs-modal">
      <div class="modal">
        <h3>{{ obsModal.segment['分段编号'] }} 的当时历史观测（第{{ obsModal.segment['恢复代次'] }}代）</h3>
        <table class="data-table">
          <thead>
            <tr><th v-for="column in observationColumns" :key="column">{{ column }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in obsModal.items" :key="String(row.id)">
              <td v-for="column in observationColumns" :key="column">{{ row[column] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn" type="button" @click="obsModal = { segment: null, items: [] }">关闭</button>
        </div>
      </div>
    </div>

    <!-- 核对结论弹窗 -->
    <div v-if="confirmTarget" class="modal-backdrop" data-testid="confirm-modal">
      <form class="modal" @submit.prevent="submitConfirm">
        <h3>确认核对 · {{ confirmTarget['分段编号'] }}</h3>
        <p class="page-desc">仅固化本代次观测与结论；缺采样分段无法通过核对。</p>
        <label class="create-field">
          <span>核对结论</span>
          <textarea v-model="confirmText" data-testid="confirm-text" rows="3" required></textarea>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="confirmTarget = null">取消</button>
          <button class="btn primary" data-testid="confirm-submit" type="submit">固化结论</button>
        </div>
      </form>
    </div>

    <!-- 退回待核弹窗 -->
    <div v-if="returnTarget" class="modal-backdrop" data-testid="return-modal">
      <form class="modal" @submit.prevent="submitReturn">
        <h3>退回待核 · {{ returnTarget['分段编号'] }}</h3>
        <p class="page-desc">退回后保留当时观测定位，可用原筛选条件回到该分段继续处理。</p>
        <label class="create-field">
          <span>退回原因</span>
          <textarea v-model="returnText" data-testid="return-text" rows="3"></textarea>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="returnTarget = null">取消</button>
          <button class="btn primary" data-testid="return-submit" type="submit">带原观测退回</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  advanceRecoveryWindow,
  confirmSegment,
  getRecovery,
  getSegmentDossier,
  interruptRecovery,
  listLagObservations,
  listLagReplays,
  listLagSegments,
  listRecoveries,
  listSegmentObservations,
  resumeRecovery,
  returnSegment,
  selectRecoverySegments,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const tabs = [
  { key: 'observations', label: '历史延迟观测' },
  { key: 'segments', label: '延迟分段核对' },
  { key: 'recovery', label: '恢复编排续办' },
  { key: 'replays', label: '派工与复核档案' },
] as const

type TabKey = (typeof tabs)[number]['key']
const activeTab = ref<TabKey>('observations')
const errorMessage = ref('')
const infoMessage = ref('')

const observationColumns = ['观测编号', '数据流', '恢复代次', '检查点编号', '检查点位点', '采样时间', '日志位点', '批次号', '延迟秒数', '观测完整性']
const segmentColumns = ['分段编号', '数据流', '恢复代次', '检查点编号', '日志下界', '日志上界', '采样时间', '观测完整性', '缺失位点', '核对结论', '登记位点']
const replayColumns = ['派工编号', '编排编号', '分段编号', '数据流', '恢复代次', '检查点编号', '重放下界', '重放上界', '登记位点', '核对结论']

// ---- 历史观测 ----
const observations = ref<EntryRow[]>([])
const obsCoverage = ref<{ covered_positions: number[]; missing_positions: number[]; complete: boolean } | null>(null)
const obsQuery = ref<Record<string, string | number | undefined>>({
  data_stream: 'order_events_cdc',
  sample_time: '2026-11-22 09:30:00',
  generation: 1,
  pos_from: 120,
  pos_to: 150,
})

async function loadObservations() {
  errorMessage.value = ''
  try {
    const data = await listLagObservations(obsQuery.value)
    observations.value = data.items
    obsCoverage.value = data.coverage
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史观测查询失败'
  }
}

// ---- 延迟分段 ----
const segments = ref<EntryRow[]>([])
const segQuery = ref<Record<string, string | number | undefined>>({
  data_stream: 'order_events_cdc',
  sample_time: '2026-11-22 09:30:00',
  pos_from: 120,
  pos_to: 150,
})

async function loadSegments() {
  errorMessage.value = ''
  try {
    const data = await listLagSegments(segQuery.value)
    segments.value = data.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '延迟分段查询失败'
  }
}

const obsModal = ref<{ segment: EntryRow | null; items: EntryRow[] }>({ segment: null, items: [] })
async function openObservations(segment: EntryRow) {
  const data = await listSegmentObservations(Number(segment.id))
  obsModal.value = { segment, items: data.items }
}

const confirmTarget = ref<EntryRow | null>(null)
const confirmText = ref('')
function openConfirm(segment: EntryRow) {
  confirmTarget.value = segment
  confirmText.value = ''
}
async function submitConfirm() {
  if (!confirmTarget.value) return
  const result = await confirmSegment(Number(confirmTarget.value.id), confirmText.value)
  confirmTarget.value = null
  if (!result.ok) {
    errorMessage.value = result.message
    return
  }
  infoMessage.value = result.message
  await loadSegments()
}

const returnTarget = ref<EntryRow | null>(null)
const returnText = ref('')
function openReturn(segment: EntryRow) {
  returnTarget.value = segment
  returnText.value = ''
}
async function submitReturn() {
  if (!returnTarget.value) return
  const result = await returnSegment(Number(returnTarget.value.id), returnText.value)
  returnTarget.value = null
  if (!result.ok) {
    errorMessage.value = result.message
    return
  }
  infoMessage.value = result.message
  await loadSegments()
}

// ---- 恢复编排 ----
const recoveryId = ref(1)
const recovery = ref<{ ok: boolean; entry: EntryRow; segments: EntryRow[] } | null>(null)
const selectedIds = ref<number[]>([])
const windowLower = ref(130)
const resumeReport = ref<Awaited<ReturnType<typeof resumeRecovery>>['report'] | null>(null)

async function loadRecovery() {
  const data = await getRecovery(recoveryId.value)
  recovery.value = data
  selectedIds.value = data.segments.map((seg) => Number(seg.id))
  windowLower.value = Number(data.entry['有效下界'])
}

async function saveSelection() {
  errorMessage.value = ''
  const result = await selectRecoverySegments(recoveryId.value, selectedIds.value)
  if (!result.ok) {
    errorMessage.value = `${result.message}${'rejected' in result ? '' : ''}`
  } else {
    infoMessage.value = result.message
  }
  await loadRecovery()
}

async function doInterrupt() {
  const result = await interruptRecovery(recoveryId.value)
  infoMessage.value = result.message
  await loadRecovery()
}

async function doAdvanceWindow() {
  const result = await advanceRecoveryWindow(recoveryId.value, windowLower.value)
  if (!result.ok) errorMessage.value = result.message
  else infoMessage.value = result.message
  await loadRecovery()
}

async function doResume() {
  errorMessage.value = ''
  try {
    const result = await resumeRecovery(recoveryId.value)
    resumeReport.value = result.report
    infoMessage.value = result.message
    await loadRecovery()
    await loadReplays()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '续办失败'
  }
}

// ---- 派工与复核档案 ----
const replays = ref<EntryRow[]>([])
const dossierId = ref<number>(2)
const dossier = ref<Awaited<ReturnType<typeof getSegmentDossier>> | null>(null)

async function loadReplays() {
  const data = await listLagReplays()
  replays.value = data.items
}

async function loadDossier() {
  errorMessage.value = ''
  try {
    dossier.value = await getSegmentDossier(dossierId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核档案查询失败'
  }
}

function switchTab(tab: TabKey) {
  activeTab.value = tab
  errorMessage.value = ''
  if (tab === 'segments') void loadSegments()
  if (tab === 'recovery') void loadRecovery()
  if (tab === 'replays') void loadReplays()
}

onMounted(() => {
  void loadObservations()
  void listRecoveries()
})
</script>
