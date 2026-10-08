# 配置说明

## 当前状态

M1 已安装并锁定前后端依赖，支持 scripts/dev.sh 启动。没有创建真实密钥或修改 Codex 全局设置。app/config.py 已读取基础配置、数据库、AI 供应商和图片上限；M2 已执行切片、固定非思考识别、AI 超时/重试/输出上限、预算预留与用量；M3 联合计划已执行教练思考和输出上限配置；天气与本地模拟提醒已经实现，原图自动保留期限仍未实现。未实现的配置不能视为已启用。

Codex 使用根目录 AGENTS.md 获取项目约定；复杂工作按实施计划推进。参考 [Codex 执行计划文档](https://developers.openai.com/cookbook/articles/codex_exec_plans)。这里的 DeepSeek 配置仅用于跑步应用，不改变 Codex 自身模型。

## 本地配置步骤（实现后使用）

1. 复制 `.env.example` 为 `.env`；开发者在本地填入密钥，不粘贴到聊天。
2. M1 保持 AI_PROVIDER=mock、WEATHER_PROVIDER=disabled、NOTIFICATION_PROVIDER=disabled。
3. M2 真实联调时设置 AI_PROVIDER=deepseek 和 DEEPSEEK_API_KEY；两个模型参数继续为 deepseek-flash。
4. 实现认证后本地生成 SESSION_SECRET。生产模式缺少安全会话密钥或 HTTPS 地址时启动失败；部署文件现已准备，容器/真实HTTPS/安卓实机尚未验收，当前继续本机开发。
5. 天气可按说明显式启用open_meteo；通知只实现本地模拟，微信暂缓，不配置PUSHPLUS_TOKEN或真实发送调度。

## 变量含义

| 分组 | 说明 |
| --- | --- |
| APP_* | 环境、用户默认时区、对外访问地址；推送链接不能使用 localhost |
| DATABASE_URL/PRIVATE_STORAGE_DIR | 相对路径按项目根目录解析；容器需绑定持久化卷 |
| SESSION_SECRET | 生产启动所需随机会话配置；当前会话使用数据库随机令牌散列，此项未用于签名；禁止进入前端配置 |
| AI_PROVIDER | mock 或 deepseek；mock 输出必须明确标记模拟，不得混入真实历史 |
| AI_*MODEL* | 版本显示标签与 API 标识分开；标识可配置，默认统一 Flash |
| AI_*THINKING | 识别 disabled、教练 enabled；按官方参数映射，不直接作为未知 API 字段发送 |
| AI_TIMEOUT/MAX_RETRIES | 每次请求 90 秒、最多额外重试 2 次；仅可重试错误退避，401/参数错误不重试 |
| AI_*MAX_OUTPUT_TOKENS | 初始输出上限 4096；截断视为失败或拆小任务，不保存残缺 JSON |
| AI_MONTHLY_BUDGET_CNY | 应用 AI 预算，使用事务预留与实际用量结算 |
| IMPORT_* | 上传上限、像素保护、切片、细节和保留期限；删除时同步清理切片和缓存 |
| WEATHER_* | 服务未选择前禁用；城市在用户设置选择 |
| NOTIFICATION_*/PUSHPLUS_* | 通知供应商与密钥，仅后端使用 |
| SCHEDULER_ENABLED | 控制定时生成提醒，worker 处理导入任务不依赖此开关 |
| VITE_API_BASE_URL | 唯一初始前端公开配置；开发由 Vite 代理，部署同源反向代理 |

配置实现时使用类型校验：切片重叠必须小于高度、预算非负、布尔值严格解析、真实供应商缺密钥明确报错。后端接口只返回配置状态，不回传秘密。

## 通知与天气接入待办

pushplus 为候选微信渠道：[渠道文档](https://pushplus.plus/doc/channel/)、[发送与异步状态](https://api.pushplus.plus/api-107783230)。实际账号条件、额度、HTTPS 接口及回执需要 M4 再核实。页面应区别“请求受理”“渠道送达”“用户查看”，不能把接口 200 当送达。

天气展示采用Open-Meteo，免费端点仅用于个人非商业原型。默认WEATHER_PROVIDER=disabled；本机.env改为open_meteo并重启才启用，不需要WEATHER_API_KEY。在线城市搜索仍连接失败；本地目录选择→预报的公开示例链路已通过，可以采用本地目录。用户已补充城市，本机已选择天气参考点并启用open_meteo，通过真实预报与刷新持久化验收；默认代码/模板仍disabled。按用户点击请求，城市中心位置与档案城市独立；页面每次勾选发送说明。15分钟缓存、2小时过期；AQI和官方预警未接入。已显示地点、时段、获取时间、过期时间和来源；不自动更新正式计划。

## M2 实际配置范围

截图读取 AI_PROVIDER、DEEPSEEK_API_KEY/BASE_URL、AI_VISION_MODEL、AI_TIMEOUT_SECONDS、AI_MAX_RETRIES、AI_VISION_MAX_OUTPUT_TOKENS、AI_MONTHLY_BUDGET_CNY，以及 AI_INPUT_PRICE_CNY_PER_MILLION（默认 2）/AI_OUTPUT_PRICE_CNY_PER_MILLION（默认 8）。价格不允许低于当前已核实的高峰值，供应商调价后应更新预算配置。识别固定 thinking=disabled、detail=original；环境模板中的 AI_VISION_THINKING 和 IMPORT_IMAGE_DETAIL 当前不用于动态切换。教练模型、思考和输出上限在 M3 联合计划适配器启用；天气、通知和自动清理仍未启用。

单段编码尺寸最多 2000×2000 像素，原图证据坐标仍使用原始方向纠正后的尺寸。每月账本按 APP_TIMEZONE 划分、在同一应用内全账户共用预算，以避免多用户绕过上限。默认首次 + AI_MAX_RETRIES 次尝试，只有用户点击才重试；费用未知保留全部预留。重启恢复保留草稿、缓存、租约和账本。

## M3 实际配置范围

联合计划使用 AI_COACH_MODEL=deepseek-flash、AI_COACH_THINKING=enabled 和 AI_COACH_MAX_OUTPUT_TOKENS=8192；请求失败不自动重试，由用户再次点击并重新预留预算。输出截断、证据不属于提供的事实、结构或规则不满足均不创建正式计划。上下文包括档案、最多 10 次跑步/10 次力量/3 次身体反馈、当前计划与所选七天日程；不发送原图、路线、账号或密码。页面每次需明确勾选发送说明。默认 mock，不含真实密钥；真实能力尚待联调。教练对话已接入同一模型、思考、输出上限、超时与预算配置；不自动重试。真实模式须发送说明勾选，仍待真实联调。


完整周期沿用 deepseek-flash、后端DEEPSEEK_API_KEY与统一预算，无新增密钥或前端环境密钥。默认mock时周期为local_rules保守规则草稿；真实模式须勾选完整周期发送说明。合成验收脚本scripts/verify_cycle.py使用独立private/cycle-smoke.db，不批准计划；joint/reduce/pain结果保存私有报告，不提交。


天气只读公开示例验证命令：backend/.venv/bin/python scripts/verify_weather.py --live-public-example，不读取个人库、不启用个人服务。完整成功需城市搜索与预报同时通过；早期在线报告为搜索失败、预报通过，退出码1；最新 --city-source local 报告为本地选择/预报通过，退出码0。不要把部分预报成功当成个人城市可用。官方接口文档 https://open-meteo.com/en/docs 、城市接口 https://open-meteo.com/en/docs/geocoding-api 、非商业/商业范围 https://open-meteo.com/en/pricing 。商业端点及密钥尚未适配。


城市目录默认本地查询，GET /weather/cities不联网、不保存候选、不发城市名字；选择本地条目只保存服务器提供的参考坐标。未启用天气亦可选择城市，刷新预报仍受disabled与页面同意控制。--city-source local（默认）仅验证固定公开北京条目；--city-source online显式验证尚未恢复的在线搜索。输出报告按source分别保存，避免混淆。无需新增天气密钥或前端配置。


生产配置现见[部署文档](deployment.md)：域名在deploy/site.env，后端运行变量在deploy/runtime.env，均忽略提交。Docker Compose raw env_file需要>=2.30，不打印展开配置；部署会固定APP_ENV、APP_PUBLIC_URL、数据库与原图路径，不能把开发.env整体复制进去。模板静态检查和生产模式合成测试通过；基础镜像标签为待容器验收候选，尚未构建或拉取。本机个人开发.env、资料及密钥未改变。
