<script setup lang="ts">
import { label } from '../metricLabels'
import { computed, onMounted, reactive, ref } from 'vue'
import { api, json, type AIUsage, type Draft, type Recognition, type Workout, type ReviewGroup } from '../api'
const props=defineProps<{draft:Draft;disabled:boolean}>()
const emit=defineEmits<{suggestions:[fields:Partial<Workout>];refresh:[];working:[busy:boolean];reviewed:[draft:Draft]}>()
const job=ref<Recognition|null>(props.draft.recognition),usage=ref<AIUsage|null>(null),running=ref(false),error=ref(''),privacy=ref(false),stop=ref(false)
const defaultTop=props.draft.height>props.draft.width*1.5?Math.min(props.draft.height-1,Math.round(props.draft.width*.9)):0
const region=reactive(props.draft.recognition?.region?{...props.draft.recognition.region}:{x:0,y:defaultTop,width:props.draft.width,height:props.draft.height-defaultTop})
const valid=computed(()=>Object.values(region).every(Number.isInteger)&&region.x>=0&&region.y>=0&&region.width>0&&region.height>0&&region.x+region.width<=props.draft.width&&region.y+region.height<=props.draft.height)
const sameRegion=computed(()=>Boolean(job.value&&Object.entries(region).every(([key,value])=>job.value!.region[key as keyof typeof region]===value)))
const failed=computed(()=>job.value?.tiles.find(t=>t.state==='failed'))
const filter=ref('problems'),search=ref('')
const core=['date','started_time','sport','title','distance_m','duration_seconds','avg_heart_rate','max_heart_rate','ascent_m']
const groups=computed(()=>job.value?.review_groups||[])
const problems=computed(()=>groups.value.filter(g=>g.status!=='needs_review'))
const missing=computed(()=>['date','sport','title','distance_m','duration_seconds'].filter(key=>{
 const saved=props.draft.fields[key as keyof Workout]
 return (saved===undefined||saved===null||saved==='')&&!groups.value.some(g=>g.field===key&&g.status==='needs_review')
}))
const shown=computed(()=>groups.value.filter(g=>{
 const category=filter.value==='all'||(filter.value==='problems'&&g.status!=='needs_review')||(filter.value==='core'&&core.includes(g.field))||(filter.value==='splits'&&g.field.startsWith('split_'))||(filter.value==='metrics'&&!core.includes(g.field)&&!g.field.startsWith('split_'))
 return category&&(!search.value.trim()||[label(g.field),...g.reading_indices.map(i=>job.value?.readings[i]?.raw||'')].join(' ').toLowerCase().includes(search.value.trim().toLowerCase()))
}))
function statusLabel(group:ReviewGroup){return {needs_review:'读数一致，待人工核对',conflict:'读数冲突',invalid:'校验问题',unknown:'未识别清楚'}[group.status]}
const editing=ref<string|null>(null),savingReview=ref(false),reviewError=ref('')
const reviewForm=reactive({status:'confirmed' as 'confirmed'|'excluded',value:null as number|null,raw:'',note:'',source:0,unit:''})
function currentReview(field:string){return props.draft.metric_reviews?.find(r=>r.field===field&&r.current)}
function editReview(g:ReviewGroup){
 const r=job.value!.readings[g.reading_indices[0]!]!,saved=currentReview(g.field)
 editing.value=g.field;reviewError.value=''
 Object.assign(reviewForm,{status:saved?.status||'confirmed',value:saved?.value??(g.status==='needs_review'&&typeof r.value==='number'?r.value:null),raw:saved?.raw||r.raw,note:saved?.note||'',source:g.reading_indices[0],unit:g.expected_unit})
}
async function saveReview(){
 if(!editing.value||!job.value||savingReview.value)return
 savingReview.value=true;reviewError.value='';emit('working',true)
 try{
  const updated=await api<Draft>(`/imports/${props.draft.id}/metric-reviews`,json('POST',{expected_revision:props.draft.revision,cache_key:job.value.cache_key,field:editing.value,status:reviewForm.status,value:reviewForm.status==='excluded'?null:reviewForm.value,unit:reviewForm.unit,raw:reviewForm.raw,note:reviewForm.note,source_reading_index:reviewForm.source}))
  job.value=updated.recognition;editing.value=null;emit('reviewed',updated)
 }catch(e){reviewError.value=(e as Error).message}
 finally{savingReview.value=false;emit('working',false)}
}
const previewStyle=computed(()=>({width:`${props.draft.width/region.width*100}%`,maxWidth:'none',marginLeft:`${-region.x/region.width*100}%`,marginTop:`${-region.y/region.width*100}%`}))
async function loadUsage(){try{usage.value=await api<AIUsage>('/ai/usage')}catch(e){error.value=(e as Error).message}}
async function recognize(){
 if(!valid.value||!privacy.value||running.value)return
 running.value=true;stop.value=false;error.value='';emit('working',true)
 try{
  job.value=await api<Recognition>(`/imports/${props.draft.id}/recognition`,json('POST',{...region,expected_revision:props.draft.revision,excludes_private_content:privacy.value}))
  while(!job.value.complete&&!stop.value){
   job.value=await api<Recognition>(`/imports/${props.draft.id}/recognition/step`,json('POST',{cache_key:job.value.cache_key}))
   if(job.value.tiles.some(t=>t.state==='failed'))break
  }
  emit('refresh');await loadUsage()
 }catch(e){error.value=(e as Error).message;try{const latest=await api<Draft>(`/imports/${props.draft.id}`);job.value=latest.recognition}catch{}}
 finally{running.value=false;emit('working',false)}
}
onMounted(loadUsage)
</script>
<template>
<section class="recognition-box">
 <div class="sectionhead"><h3>截图识别与核对</h3><span class="badge">{{usage?.provider==='deepseek'?'DeepSeek-V4.1-Flash':'未启用真实 AI'}}</span></div>
 <p class="tiny muted">先选择数据区域，排除路线地图、头像和账户。坐标按原图像素填写；不会自动发送整张图片。</p>
 <div v-if="usage" class="usage-line tiny">本月预估费用及预留 ¥{{usage.committed_cny.toFixed(3)}} / ¥{{usage.budget_cny}} · {{usage.calls}} 次请求</div>
 <p v-if="usage?.provider!=='deepseek'" class="evidence-note">当前为 mock 模式，不发送图片也不编造识别结果。可继续手工录入；启用方式见项目 README 的 DeepSeek 配置。</p>
 <details :open="!job?.complete"><summary>{{job?.complete?'查看识别区域与发送设置':'设置识别区域'}}</summary>
 <fieldset :disabled="disabled||running" class="crop-controls">
  <div class="form-grid"><label class="field">左侧 x<input v-model.number="region.x" type="number" min="0" :max="draft.width-1"></label><label class="field">顶部 y<input v-model.number="region.y" type="number" min="0" :max="draft.height-1"></label><label class="field">区域宽度<input v-model.number="region.width" type="number" min="1" :max="draft.width"></label><label class="field">区域高度<input v-model.number="region.height" type="number" min="1" :max="draft.height"></label></div>
  <p class="tiny muted">下面是所选区域的预览，可滚动检查。顶部默认值仅供定位，请自行确认是否已避开地图。</p>
  <div v-if="valid" class="crop-preview"><div :style="{height:`${region.height/region.width*100}cqw`,overflow:'hidden'}"><img :src="draft.image_url" :style="previewStyle" alt="待发送数据区域预览"></div></div>
  <p v-else class="error">区域超出图片，请调整坐标和大小。</p>
  <label class="privacy-check"><input v-model="privacy" type="checkbox">我已检查区域，不包含路线、头像或账户信息；同意将该区域分段发送到 DeepSeek。</label>
 </fieldset>
 </details>
 <div class="actions"><button type="button" class="btn primary" :disabled="disabled||running||!valid||!privacy||usage?.provider!=='deepseek'" @click="recognize">{{running?'正在分段识别…':sameRegion&&job?.complete?'读取已缓存结果':sameRegion&&job?'继续 / 重试未完成分段':'开始识别所选区域'}}</button><button v-if="running" type="button" class="btn" :disabled="stop" @click="stop=true">{{stop?'已请求停止':'本段结束后停止'}}</button></div>
 <p v-if="error" class="error" role="alert">{{error}}</p>
 <template v-if="job">
  <p class="tiny muted" role="status">已完成 {{job.done}} / {{job.total}} 段。刷新后可打开草稿继续；已成功的分段不会重复调用。</p>
  <p v-if="failed" class="error">{{failed.error}}（已尝试 {{failed.attempts}} 次）</p>
  <div v-if="job.readings.length" class="review-overview">
   <h4>先核对，再保存</h4>
   <p class="tiny muted">{{groups.length}} 个字段 · {{job.readings.length}} 条原始读数 · {{problems.length}} 个字段需要重点核对。读数一致只表示重复位置未发现差异，不代表识别准确。</p>
   <p v-if="!job.complete" class="evidence-note">分段尚未完成，目前结果不完整；可继续识别或手工填写。</p>
   <p v-if="missing.length" class="evidence-note">基础记录仍需手工补充或核对：{{missing.map(label).join('、')}}。这里只检查已保存草稿和识别结果；未保存的表单输入以表单为准。</p>
   <div class="review-filters" aria-label="读数分类">
    <button v-for="f in [{id:'problems',name:'重点核对'},{id:'core',name:'基础记录'},{id:'splits',name:'公里分段'},{id:'metrics',name:'其他指标'},{id:'all',name:'全部'}]" :key="f.id" type="button" class="btn" :aria-pressed="filter===f.id" @click="filter=f.id">{{f.name}}</button>
   </div>
   <label class="field review-search">查找字段或原始读数<input v-model="search" type="search" placeholder="例如：心率、触地、6′22″"></label>
   <p v-if="!shown.length" class="tiny muted">{{filter==='problems'&&!search?'未发现字段冲突或校验问题，仍请核对基础记录。':'当前分类没有匹配读数。'}}</p>
   <article v-for="g in shown" :key="g.field" class="reading-card" :class="{attention:g.status!=='needs_review'}">
    <div class="sectionhead"><h4>{{label(g.field)}}</h4><span class="badge">{{statusLabel(g)}}</span></div>
    <p v-for="reason in g.reasons" :key="reason" class="tiny">{{reason}}</p>
    <div v-for="index in g.reading_indices" :key="index" class="reading-source">
     <p><strong>{{job.readings[index]!.raw}}</strong><span class="tiny muted"> → {{job.readings[index]!.value??'留空'}} {{job.readings[index]!.unit}}</span></p>
     <p class="tiny muted">模型原返回单位：{{(job.readings[index]!.raw_unit??job.readings[index]!.unit)||'无单位'}} · {{job.readings[index]!.origin==='device_estimate'?'设备估计':'设备读数'}} · 待人工核对</p>
     <a class="tiny" :href="`/api/v1/imports/${draft.id}/evidence/${job.readings[index]!.source_tile_id}`" target="_blank" rel="noopener">查看第 {{job.readings[index]!.source_tile_id+1}} 段原图 ↗</a>
     <span class="tiny muted"> · 证据框 {{job.readings[index]!.source_rect.join(', ')}}</span>
    </div>
    <p v-if="currentReview(g.field)" class="evidence-note">你的核对：{{currentReview(g.field)!.status==='confirmed'?`${currentReview(g.field)!.value} ${currentReview(g.field)!.unit} · 已确认指标`:'不采用此指标'}}。整次训练确认后才进入正式记录。</p>
    <button v-if="!core.includes(g.field)" type="button" class="btn" :disabled="disabled||running||savingReview||!job.complete" @click="editReview(g)">{{currentReview(g.field)?'修改核对':'核对 / 修正指标'}}</button>
    <form v-if="editing===g.field" class="metric-editor" @submit.prevent="saveReview">
     <fieldset :disabled="disabled||savingReview" style="border:0;padding:0;margin:0">
      <label class="field">采用方式<select v-model="reviewForm.status"><option value="confirmed">核对后确认数值</option><option value="excluded">不采用 / 无法确定</option></select></label>
      <label class="field">核对所参照的读数<select v-model.number="reviewForm.source"><option v-for="i in g.reading_indices" :key="i" :value="i">第 {{job.readings[i]!.source_tile_id+1}} 段 · {{job.readings[i]!.raw}}</option></select></label>
      <label class="field">核对后的原图文字<input v-model="reviewForm.raw" maxlength="200" required></label>
      <label v-if="reviewForm.status==='confirmed'" class="field">手工输入标准数值 · {{reviewForm.unit||'无单位'}}<input v-model.number="reviewForm.value" type="number" step="any" min="0" required></label>
      <label class="field">核对说明{{reviewForm.status==='excluded'?'（必填）':'（可选）'}}<input v-model="reviewForm.note" maxlength="300" :required="reviewForm.status==='excluded'"></label>
      <p class="tiny muted">保留原识别结果及历次修改。单位固定为字段标准单位，请参照原图填写；非精确或无法判断的读数请选择不采用。设备估计仍是设备估计。</p>
      <p v-if="reviewError" class="error" role="alert">{{reviewError}}</p>
      <div class="actions"><button class="btn primary">{{savingReview?'保存中…':'保存这项核对'}}</button><button type="button" class="btn" @click="editing=null">取消</button></div>
     </fieldset>
    </form>
   </article>
   <button type="button" class="btn" :disabled="disabled||running||!job.complete||!Object.keys(job.suggested_fields).length" @click="emit('suggestions',job.suggested_fields)">将无冲突结果填入未填写字段</button>
   <p class="tiny muted">不会覆盖已有输入。冲突字段请参照原图手工填写；未核对的指标和分段只保留为证据；已确认指标在整次训练确认后入库。填写后保存核对草稿，再单独确认加入训练记录。</p>
  </div>
  <details v-if="draft.metric_reviews?.length"><summary>查看 {{draft.metric_reviews.length}} 次指标核对记录</summary><p v-for="r in draft.metric_reviews" :key="r.id" class="tiny">{{label(r.field)}} · {{r.status==='confirmed'?`${r.value} ${r.unit}`:'不采用'}} · {{r.current?'当前核对':'历史 / 证据已变化'}} · 草稿版本 {{r.draft_revision}}<br>{{r.raw}}<br>{{r.note}}</p></details>
  <details v-if="job.readings.length"><summary>查看 {{job.readings.length}} 条读数及来源</summary><div class="evidence-table"><table><thead><tr><th>字段</th><th>原始读数 → 标准值</th><th>来源 / 核对</th></tr></thead><tbody><tr v-for="(r,i) in job.readings" :key="i"><td>{{label(r.field)}}</td><td>{{r.raw}} → {{r.value??'留空'}} {{r.unit}}</td><td><a :href="`/api/v1/imports/${draft.id}/evidence/${r.source_tile_id}`" target="_blank" rel="noopener">第 {{r.source_tile_id+1}} 段 ↗</a><div class="tiny muted">原图框 {{r.source_rect.join(', ')}}</div><div class="tiny">{{r.issue||(r.origin==='device_estimate'?'设备估计，待核对':'待人工核对')}}</div></td></tr></tbody></table></div></details>
 </template>
</section>
</template>

<style scoped>
.review-overview{margin-top:20px}
.review-filters{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.review-filters [aria-pressed="true"]{background:var(--green,#285642);color:white}
.review-search{margin:12px 0}
.reading-card{border:1px solid #dce4dd;border-radius:12px;padding:16px;margin:12px 0;overflow-wrap:anywhere}
.reading-card.attention{background:#fff9ed;border-color:#e3cc97}
.reading-card h4{margin:0}
.reading-card .sectionhead{flex-wrap:wrap;gap:8px}
.metric-editor{margin-top:12px;padding-top:12px;border-top:1px solid #dce4dd}.metric-editor .field{margin:10px 0}
.reading-source{border-top:1px solid #e4e5db;padding:10px 0}
.reading-source p{margin:4px 0}
@media(max-width:560px){.reading-card{padding:12px}.review-filters .btn{padding:8px 10px}}
</style>
