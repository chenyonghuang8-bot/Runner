# 单用户部署与验收

## 当前验收状态

已准备两容器配置：Caddy托管Vue生产构建并同源代理`/api/*`，FastAPI单进程连接SQLite。没有引入额外业务服务或通知通道。本机没有Docker/Caddy，现阶段只完成模板静态检查、前端生产构建与应用生产模式的合成测试；容器构建、Linux依赖安装、卷权限、代理及真实HTTPS、安卓访问均尚未验收。部署位置/域名待用户选择，没有打开公网端口或迁移个人资料。

基础镜像候选固定为python:3.11.17-slim-bookworm、node:22.23.3-bookworm-slim、caddy:2.11.7-alpine，已核对官方镜像目录；尚未拉取或构建，因此不是实际验证的运行版本。Python依赖继续requirements.lock，前端继续package-lock.json。构建验收后应记录实际镜像ID/digest；不使用latest，不把版本标签视为不可变散列。

## 配置范围

- `deploy/compose.yaml`固定项目名runner，仅web发布80/443，backend没有宿主机端口。不要增加后端端口或启用多副本；当前限流在进程内，SQLite用于单实例。
- runner_data卷包含数据库、WAL/SHM和private原图；runner_backups卷保存完整备份。Caddy证书分别保存在caddy_data/caddy_config卷。只有前端产物位于web镜像，后端不含个人资料。
- `.dockerignore`采用允许列表，排除.env、数据库、private、测试结果、node_modules、构建缓存；前端构建仅有公开VITE_API_BASE_URL=/api/v1，没有后端环境文件或密钥。
- backend以UID10001运行、只读镜像、可写/data与/backups和临时/tmp、单进程。新命名卷继承镜像目录权限；已有卷权限需要部署时核对，勿盲目递归修改个人目录。
- `runtime.env`仅注入backend；Compose固定生产环境、URL和数据路径。使用raw格式避免密钥中`$`被插值，需Compose >=2.30。该文件不加引号，不提交、不粘贴到聊天；site.env只有域名。[Compose官方说明](https://docs.docker.com/reference/compose-file/services/#format)
- Caddy为正确解析且可到达服务器的公开域名申请证书并重定向HTTP；证书申请与域名解析需实机验收，不能仅凭配置称已启用HTTPS。[Caddy官方说明](https://caddyserver.com/docs/automatic-https)
- `/api/*`保留完整路径转发；API与页面同源。反向代理不暴露私有目录、Swagger或环境文件；原图通过已有鉴权接口访问。请求体代理上限25MB，应用图片上限20MB（需预留multipart开销）。
- 生产模式要求合法HTTPS同源URL和至少32字符SESSION_SECRET，拒绝外部Host，关闭Swagger/OpenAPI及远程首次账户注册。当前会话使用数据库中随机令牌散列；SESSION_SECRET是生产启动配置要求，不承担当前数据库会话签名。

## 部署主机操作（尚未执行）

准备Docker Engine和Compose >=2.30、公开域名DNS到服务器；如有AAAA记录，IPv6也必须可达。确认允许在该主机发布80/443后再启动web。局域网无公开域名的方案需另外处理安卓信任的HTTPS证书，不改成生产HTTP或指导跳过浏览器证书警告。

项目根目录复制模板并在部署主机编辑：

```bash
cp deploy/site.env.example deploy/site.env
cp deploy/runtime.env.example deploy/runtime.env
chmod 600 deploy/site.env deploy/runtime.env
```

site.env填真实域名；runtime.env中的SESSION_SECRET本机生成随机值（例如用Python secrets.token_urlsafe(48)写入文件，不贴到聊天），保持AI_PROVIDER=mock、WEATHER_PROVIDER=disabled先验收。启用DeepSeek时仅后端填写自己的DEEPSEEK_API_KEY、AI_PROVIDER=deepseek；模型仍deepseek-flash。密钥不传给web容器。不要复制整个开发.env，它含开发环境/本机路径及无关变量。

```bash
backend/.venv/bin/python scripts/check_deployment.py
# -q只检查，不把包含密钥的展开配置打印出来
 docker compose --env-file deploy/site.env -f deploy/compose.yaml config -q
 docker compose --env-file deploy/site.env -f deploy/compose.yaml build
# 先迁移并在本机交互终端初始化账户；密码不出现在命令参数或管道中
 docker compose --env-file deploy/site.env -f deploy/compose.yaml run --rm backend python -m app.owner --username toby
# 域名、服务器与公开访问范围确认后再运行
 docker compose --env-file deploy/site.env -f deploy/compose.yaml up -d
 docker compose --env-file deploy/site.env -f deploy/compose.yaml ps
```

首次运行使用新卷，不会自动导入你现在电脑上的截图或档案。已有owner时初始化工具拒绝覆盖，不改密码。entrypoint在启动命令前执行迁移，失败则不启动；更新前先备份，避免迁移与回滚混淆。当前本机开发服务继续使用dev.sh，部署配置不改开发.env。

## 备份、恢复与更新

在已运行服务上用唯一名称创建完整备份（把20261008-before-update换成实际日期/操作名，不覆盖已有目录）：

```bash
 docker compose --env-file deploy/site.env -f deploy/compose.yaml exec backend python scripts/backup.py create --database /data/runner.db --images /data/private --output /backups/20261008-before-update
 docker compose --env-file deploy/site.env -f deploy/compose.yaml exec backend python scripts/backup.py verify /backups/20261008-before-update
 docker compose --env-file deploy/site.env -f deploy/compose.yaml cp backend:/backups/20261008-before-update ./private/server-backup-20261008
```

备份包含健康数据和密码散列，需要妥善保存；不包含runtime.env。在线SQLite备份保持数据库一致，但原图可能在备份期间被主动清空导致备份校验失败；此时暂停清空/写入操作再用新目录备份，不宣称失败包可恢复。

更新前先下载并验证备份，随后构建新镜像、`up -d`，复验登录/数据/计划/费用。回滚不自动执行数据库降级；先在新目录restore-copy检查，确认兼容后再停服务与替换卷，不在当前实例直接覆盖。`docker compose down`保留命名卷；不要用`down -v`清除个人资料。当前没有自动备份定时器，也没有自动切换恢复卷脚本。

## 实机验收清单

1. 页面及API均通过有效HTTPS访问，HTTP重定向；安卓在正常证书验证下能登录，Secure/HttpOnly/SameSite cookie和CSRF生效。
2. 非登录原图/导出不可读，`/private`、`/data`、`/.env`和API文档不可访问；后端8000不在公网监听。
3. 使用合成记录验收截图→核对→确认、跑步/力量/反馈、周期草稿→整体确认→动态调整；未确认模型结果不生效。
4. 重启和重新创建容器后，账户、记录、草稿、原图、计划版本及费用仍在；卷权限满足备份/原图清理。
5. 实际创建备份、下载、散列检查与恢复到新目录，合成删除不触碰个人数据。
6. 默认模拟AI/关闭天气下手工可用；明确启用后单独验收外部接口及预算。微信仍暂缓，不创建发送worker。

以上清单尚未完成，不把应用TestClient成功等同真实服务器、TLS或安卓验收。
