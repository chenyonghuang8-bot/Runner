<script setup lang="ts">
import type { RunEvidenceData } from '../api'
import { label } from '../metricLabels'
defineProps<{evidence:RunEvidenceData}>()
function pace(seconds:number){const s=Math.round(seconds);return `${Math.floor(s/60)}′${String(s%60).padStart(2,'0')}″ / km`}
</script>
<template>
<details class="run-evidence">
 <summary>最近一次 · 配速与已核对分段</summary>
 <p class="tiny">{{evidence.date}} · {{evidence.title}}<br>{{(evidence.distance_m/1000).toFixed(2)}} 公里 · {{Math.round(evidence.duration_seconds/60)}} 分钟</p>
 <dl class="tiny"><dt>距离与运动时间计算配速</dt><dd>{{pace(evidence.calculated_pace_seconds_per_km)}}</dd><dt>设备平均配速 · 人工核对</dt><dd>{{evidence.device_average_pace_seconds_per_km===null?'未提供已确认读数':pace(evidence.device_average_pace_seconds_per_km)}}</dd><dt>主观用力 / 10</dt><dd>{{evidence.effort??'未填写'}}</dd><dt>不适描述</dt><dd>{{evidence.pain_notes||'未填写；不代表无痛'}}</dd></dl>
 <p v-if="evidence.elapsed_minus_duration_seconds!==null" class="tiny muted">总时间减运动时间：{{evidence.elapsed_minus_duration_seconds}} 秒 · 仅为读数之差，不自动解释为暂停。</p>
 <p v-for="n in evidence.notices" :key="n" class="evidence-note tiny">{{n}}</p>
 <h4>完整公里分段</h4>
 <p class="tiny">记录距离含 {{evidence.splits.expected_full_kilometers}} 个完整公里 · 已核对 {{evidence.splits.confirmed_count}} 个{{evidence.splits.complete?' · 分段齐全':''}}</p>
 <p v-if="!evidence.splits.expected_full_kilometers" class="tiny muted">本次不足一公里，不将尾段当作整公里。</p>
 <p v-if="evidence.splits.missing_kilometers.length" class="tiny muted">尚未确认的公里：{{evidence.splits.missing_kilometers.join('、')}}。不自动补齐。</p>
 <p v-if="evidence.splits.expected_full_kilometers>evidence.splits.supported_kilometers_limit" class="tiny muted">当前最多支持前 {{evidence.splits.supported_kilometers_limit}} 公里分段，本次分段未覆盖全程。</p>
 <p v-if="evidence.splits.fastest_confirmed_seconds!==null" class="tiny">已核对分段中的最快：{{pace(evidence.splits.fastest_confirmed_seconds)}}<br><span v-if="evidence.splits.mean_full_kilometer_seconds!==null">全部完整公里平均耗时：{{evidence.splits.mean_full_kilometer_seconds}} 秒/公里（不含尾段）</span></p>
 <details v-if="evidence.splits.rows.length"><summary>查看 {{evidence.splits.rows.length}} 个已核对公里</summary><table><thead><tr><th>公里序号</th><th>该公里耗时</th></tr></thead><tbody><tr v-for="s in evidence.splits.rows" :key="s.kilometer"><td>{{s.kilometer}}</td><td>{{s.seconds}} 秒 · {{pace(s.seconds)}}</td></tr></tbody></table></details>
 <details v-if="Object.keys(evidence.device_metrics).length"><summary>查看已核对设备读数（{{Object.keys(evidence.device_metrics).length}} 项）</summary><dl class="tiny"><template v-for="(m,key) in evidence.device_metrics" :key="key"><dt>{{label(String(key))}}</dt><dd>{{m.value}} {{m.unit}} · {{m.origin==='device_estimate'?'设备估计':'设备读数'}}</dd></template></dl></details>
 <p v-for="l in evidence.limitations" :key="l" class="tiny muted">{{l}}</p>
 <p class="tiny muted evidence-id">依据训练记录：{{evidence.id}}</p>
</details>
</template>
<style scoped>
.run-evidence{border-top:1px solid #e5e8df;padding-top:14px;margin-top:14px}.run-evidence dl{margin:12px 0}.run-evidence dt{color:#6b776c;margin-top:10px}.run-evidence dd{margin:4px 0;overflow-wrap:anywhere}.run-evidence details{margin:12px 0}.run-evidence table{width:100%;table-layout:fixed;font-size:12px;text-align:left}.run-evidence td,.run-evidence th{padding:8px 4px;border-bottom:1px solid #e5e8df;overflow-wrap:anywhere}.evidence-id{overflow-wrap:anywhere}
</style>
