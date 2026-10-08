<script setup lang="ts">
import type { Workout } from '../api'
import { label } from '../metricLabels'
defineProps<{workout:Workout}>()
</script>
<template>
<details v-if="workout.confirmed_metrics&&Object.keys(workout.confirmed_metrics).length" class="confirmed-metrics">
 <summary>已核对详细指标（{{Object.keys(workout.confirmed_metrics).length}} 项）</summary>
 <p class="tiny muted">来自你在截图草稿中的人工核对，随整次训练确认保存。设备估计仍保留设备口径，不代表医学或能力评估。</p>
 <dl><template v-for="(m,key) in workout.confirmed_metrics" :key="key"><dt>{{label(String(key))}}</dt><dd>{{m.value}} {{m.unit}}<span class="tiny muted"> · {{m.origin==='device_estimate'?'设备估计':'设备读数'}} · 人工核对</span></dd></template></dl>
</details>
</template>
<style scoped>
.confirmed-metrics{margin-top:16px}.confirmed-metrics dl{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0;font-size:13px}.confirmed-metrics dd{margin:0;overflow-wrap:anywhere}@media(max-width:560px){.confirmed-metrics dl{grid-template-columns:1fr}}
</style>
