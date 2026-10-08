import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig(({mode})=>({plugins:[vue()],server:{host:'127.0.0.1',port:5173,strictPort:true,proxy:{'/api':loadEnv(mode,'.','RUNNER_DEV_').RUNNER_DEV_API_PROXY||'http://127.0.0.1:8000'}}}))
