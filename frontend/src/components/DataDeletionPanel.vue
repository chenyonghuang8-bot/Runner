<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api,json} from '../api'
const emit=defineEmits<{changed:[]}>()
type Preview={id:string;expires_at:string;counts:{label:string;count:number}[];confirmation:string}
const preview=ref<Preview|null>(null),password=ref(''),phrase=ref(''),busy=ref(false),error=ref(''),notice=ref('')
const pending=ref<{id:string;remaining_originals:number}[]>([])
async function reload(){pending.value=await api('/data/deletions')}
onMounted(()=>reload().catch(e=>error.value=e.message))
async function prepare(){busy.value=true;error.value='';password.value='';phrase.value='';try{preview.value=await api('/data/deletion-preview',json('POST',{}))}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
function cancel(){preview.value=null;password.value='';phrase.value=''}
async function confirm(id:string){busy.value=true;error.value='';try{const result=await api<{message:string}>(`/data/deletions/${id}/confirm`,json('POST',{password:password.value,confirmation:phrase.value}));notice.value=result.message;cancel();await reload();emit('changed')}catch(e){error.value=(e as Error).message}finally{password.value='';busy.value=false}}
</script>
<template><section class="card panelwide deletion-panel"><h2>清空当前应用数据</h2><p class="tiny muted">清空档案与日程、跑步与力量、身体反馈、截图与识别缓存、计划、聊天、天气和提醒。保留登录账户与AI费用账本，费用额度不会重置。</p><p class="tiny muted">操作不可在页面撤销。备份、已下载的导出包和 private/ 下的联调文件不在清空范围；已发送到外部服务的数据也不会由此撤回。建议先导出或备份。</p><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p><button v-if="!preview" class="btn" :disabled="busy" @click="prepare">先预览清空范围</button><template v-if="preview"><h3 style="margin-top:18px">本次将清空</h3><ul class="counts"><li v-for="row in preview.counts" :key="row.label">{{row.label}}：{{row.count}} 条</li></ul><p class="tiny muted">预览有效至 {{new Date(preview.expires_at).toLocaleString()}}。期间数据有变化需重新预览。</p></template><form v-if="preview||pending.length" @submit.prevent="confirm(preview?.id||pending[0]!.id)"><p v-if="pending.length" class="error">{{pending.length}} 次清空仍有原图待清理。重试只清理对应旧原图，不会再次清空新数据。</p><label class="field">当前账户密码<input v-model="password" type="password" autocomplete="current-password" maxlength="128" required :disabled="busy"></label><label class="field">输入「清空我的训练数据」<input v-model="phrase" autocomplete="off" maxlength="40" required :disabled="busy"></label><div class="actions"><button class="btn danger" :disabled="busy||!password||phrase!=='清空我的训练数据'">{{busy?'处理中…':preview?'确认清空当前数据':'重试清理旧原图'}}</button><button v-if="preview" class="btn" type="button" :disabled="busy" @click="cancel">取消</button></div></form></section></template>
<style scoped>.deletion-panel{margin-top:24px;overflow-wrap:anywhere}.deletion-panel p{margin:14px 0}.counts{padding-left:20px;font-size:13px;line-height:1.9;columns:2}.deletion-panel .field{margin:16px 0}.danger{color:#a4372d;border-color:#c38983}.actions{flex-wrap:wrap}@media(max-width:480px){.counts{columns:1}}</style>
