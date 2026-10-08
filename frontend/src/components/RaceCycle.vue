<script setup lang="ts">
import type { RaceCycleData } from '../api'
defineProps<{cycle:RaceCycleData}>()
defineEmits<{settings:[]}>()
</script>
<template>
<section class="race-cycle">
 <div class="sectionhead"><h3>比赛周期 · 逐周核对</h3><span class="badge">日期框架 · 未确认计划</span></div>
 <p v-if="cycle.status==='missing_date'" class="tiny muted">还没有比赛日期。可保留无期限目标，填写近期比赛后再查看周期。</p>
 <p v-else-if="cycle.status==='past_goal'" class="evidence-note">目标日期 {{cycle.goal_date}} 已过去，请核对目标；不会按过去日期生成备赛安排。</p>
 <template v-else>
  <p class="tiny">比赛日期 {{cycle.goal_date}} · {{cycle.days_remaining===0?'今天是目标比赛日期':`还有 ${cycle.days_remaining} 天`}} · {{cycle.goal_duration_seconds===null?'目标用时未设置':'目标用时已保存，可行性仍待评估'}}</p>
  <p v-if="cycle.blocked_reasons.length" class="evidence-note tiny">身体或资料仍需核对，阶段日期不会解除限制。当前不生成比赛强化训练。</p>
  <p class="tiny muted">比赛当日需单独核对是否参赛与安排；一般七天草稿不会自动在当天加跑步。</p>
  <p v-if="cycle.truncated" class="tiny muted">目标超过展示范围，仅列未来 {{cycle.horizon_days}} 天；未展示比赛周或赛后安排。</p>
  <details><summary>查看 {{cycle.weeks.length}} 个核对阶段</summary>
   <article v-for="w in cycle.weeks" :key="w.index" class="cycle-week"><div class="sectionhead"><b>{{w.title}}</b><span class="tiny muted">待核对</span></div><p class="tiny muted">{{w.start}} — {{w.end}}</p><p v-if="w.race_day" class="tiny">目标比赛日：{{w.race_day}} · 不默认已具备参赛条件</p><dl class="tiny"><dt>跑步</dt><dd>{{w.focus.run}}</dd><dt>恢复</dt><dd>{{w.focus.recovery}}</dd></dl></article>
  </details>
 </template>
 <p v-for="l in cycle.limitations" :key="l" class="tiny muted">{{l}}</p>
 <button class="btn" @click="$emit('settings')">核对比赛目标 →</button>
</section>
</template>
<style scoped>
.race-cycle{border:1px solid #e5e8df;border-radius:12px;padding:16px;margin:20px 0}.race-cycle .sectionhead{flex-wrap:wrap;gap:8px}.race-cycle p{margin:10px 0}.race-cycle details{margin:16px 0}.cycle-week{border-top:1px solid #e5e8df;padding:16px 0}.cycle-week dl{display:grid;grid-template-columns:40px 1fr;gap:8px}.cycle-week dd{margin:0;overflow-wrap:anywhere}
</style>
