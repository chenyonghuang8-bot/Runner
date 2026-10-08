<script setup lang="ts">
import type { PlanComparisonData } from '../api'
defineProps<{comparison:PlanComparisonData}>()
</script>
<template>
<section class="plan-comparison">
 <h3>一起核对训练时间</h3>
 <p class="tiny muted">新窗口 {{comparison.after_window.start}} — {{comparison.after_window.end}}</p>
 <p v-if="comparison.has_previous_plan" class="tiny muted">原窗口 {{comparison.before_window.start}} — {{comparison.before_window.end}}</p>
 <p v-else class="tiny muted">尚无原正式计划；原值为空基线，不表示过去没有训练。</p>
 <p v-if="comparison.has_previous_plan&&!comparison.same_window" class="evidence-note tiny">两个计划的日期范围不同，请逐日核对；不能把窗口总时长的变化直接当作减量。</p>
 <div class="comparison-grid">
  <div><b>跑步</b><p class="tiny">{{comparison.before.run.sessions}} 次 / {{comparison.before.run.minutes}} 分钟 → {{comparison.after.run.sessions}} 次 / {{comparison.after.run.minutes}} 分钟</p></div>
  <div><b>休息 / 恢复</b><p class="tiny">{{comparison.before.recovery.sessions}} 项 → {{comparison.after.recovery.sessions}} 项</p><p class="tiny muted">零负荷待核对也计为恢复项，不表示实际恢复完成</p></div>
 </div>
 <p class="tiny">计划总时间 {{comparison.before.total_minutes}} → {{comparison.after.total_minutes}} 分钟</p>
 <details><summary>查看七天训练时间</summary>
  <article v-for="d in comparison.days" :key="d.date" class="comparison-day">
   <b class="tiny">{{d.date}}</b>
   <p class="tiny muted">{{!d.availability?'当时未填写日程':!d.availability.available?'当时标记不可训练':`当时可用 ${d.availability.duration_minutes} 分钟`}}</p>
   <p class="tiny">新安排：跑步 {{d.after.run.minutes}}，总计 {{d.after.total_minutes}} 分钟</p>
  </article>
 </details>
 <p class="tiny muted">这里保留生成草稿时的日程快照；新事实使提案失效，需重新生成。</p>
 <p v-for="l in comparison.limitations" :key="l" class="tiny muted">{{l}}</p>
</section>
</template>
<style scoped>
.plan-comparison{border:1px solid #e5e8df;border-radius:12px;padding:16px;margin:20px 0}.comparison-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.comparison-grid>div{background:#f5f6f2;padding:12px;border-radius:8px;overflow-wrap:anywhere}.comparison-day{border-top:1px solid #e5e8df;padding:12px 0}.plan-comparison p{margin:8px 0}.plan-comparison details{margin:12px 0}@media(max-width:600px){.comparison-grid{grid-template-columns:1fr}}
</style>
