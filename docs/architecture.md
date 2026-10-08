# 架构、数据与接口设计

## 部署边界

Vue 3 + TypeScript + Vite 前端，FastAPI + Pydantic 后端；SQLAlchemy + Alembic 管理 SQLite，后续按需迁移 PostgreSQL。实现时锁定兼容版本。截图预处理候选为 Pillow。首版一个 API 服务和一个独立 Python worker，共享本地持久化存储；SQLite WAL + busy timeout，事务保持短小，不在数据库事务内等待 AI。

worker 从数据库任务表领取工作，以租约和重试时间实现重启恢复。首版不引入 Redis/Celery。运行容器仅设计为 Docker Compose，在具备实际入口和依赖后再添加可运行配置，避免当前生成无法启动的壳。

浏览器只访问应用后端；AI、天气、通知密钥由后端持有。公开访问前实现单用户登录、密码散列、HttpOnly/SameSite 会话、CSRF 防护、HTTPS、登录限流和附件鉴权。首版不开公共注册。所有对象查询仍按 user_id 隔离。

## 数据实体

所有实体使用独立 ID；时间以 UTC 存储、按用户时区展示；日期型训练日保留本地日期。个人实体均有 user_id。距离统一米、时长秒、配速秒/公里；null 代表缺失，0 仅代表明确零值。

| 实体 | 核心内容 |
| --- | --- |
| User/Profile | 时区、训练经历、通知偏好；年龄及录入日期或可选生日，不能静默推断生日 |
| Goal | 比赛日期、距离、期望时间、优先级、评估状态 |
| Availability | 本地日期、可用时间、时长、地点；支持周模板与单日覆盖 |
| HealthCheckin | 发生时间、睡眠、疲劳、局部酸胀位置/程度/时机/趋势、疼痛、日常活动受影响情况、疾病和医生限制 |
| ImportJob/Asset | 文件散列、原图私有路径、切片坐标、状态、错误、保留期限 |
| ExtractedField | 路径、原文、标准值、单位、来源坐标、校验问题、用户确认状态 |
| StrengthProfile | 力量经验、熟悉动作、器械/场地、可用时间；未知状态保留 |
| ExerciseDefinition | 动作 ID、模式、目标肌群、器械、说明来源、适用限制、版本与审阅状态 |
| StrengthWorkout/ExerciseSet | 实际动作、组次、计时、负重/阻力、主观用力、部分完成和跳过原因 |
| Workout/Split | 运动类型、实际指标、逐公里数据、设备估计、事实修订号 |
| PlanVersion/Session | 版本号、run/strength/mobility/recovery 判别类型、训练日、目的、目标范围、状态、父版本；一次覆盖整体计划，不覆盖实际事实 |
| AdjustmentProposal | 原版本、事实版本、改变原因、证据 ID、差异、校验结果、有效期、状态 |
| Conversation | 对话与建议引用；不能代替数据库状态 |
| WeatherSnapshot | 供应商、地点、预报时段、获取时间、过期时间 |
| Job/Notification | 领取租约、幂等键、重试、计划版本、渠道流水号、送达状态 |
| AIUsage | 任务类型、模型标识/版本标签、输入输出及缓存 token、估价、请求 ID、提示词版本 |

## 状态与事务

导入：uploaded → processing → needs_review → confirmed；失败 → retryable_failed 或 failed。confirmed 后修改形成事实修订，触发旧建议失效。重复图片散列只提示复用，不等于同一运动：相同日期、开始时间、距离的其他截图可作为补充，不能自动合并不同运动。

建议：draft → pending → applied/rejected/expired/superseded。批准请求携带 expected_plan_version 和 expected_facts_revision。事务内检查所有权、有效期和版本；不匹配返回 409，成功则创建新计划版本，标记建议已应用，写入通知 outbox。重复批准返回相同结果。撤回产生新版本，保留历史，不撤销已经完成的训练事实。

worker 发送前再次检查计划版本与身体提示状态；取消旧版本尚未发送的通知。渠道受理和最终送达分开记录，超时结果不明确时先查询流水号，再决定是否重试。用户点一次确认不能触发多次计划创建或多条同类通知。

## 初版 API 契约

统一前缀 /api/v1；错误体含 code、message、field_errors、request_id。耗时任务返回 202 和 job_id，前端轮询状态；对话流式输出可在后续实现。

| 方法/路径 | 作用 |
| --- | --- |
| POST /auth/login；POST /auth/logout；GET /auth/me | 单用户认证 |
| GET/PATCH /profile；GET/POST /goals | 档案与目标 |
| GET/PUT /availability | 日程范围及覆盖 |
| POST /imports；GET /imports/{id} | multipart 上传；查看进度、字段与证据 |
| PATCH /imports/{id}/fields；POST /imports/{id}/confirm | 修正与确认，确认需版本号与幂等键 |
| POST /imports/{id}/retry | 仅重试失败模块 |
| GET/POST /workouts；PATCH /workouts/{id} | 查询、手工录入、修订 |
| POST /checkins | 身体反馈 |
| POST /plan-proposals；GET /plan-proposals/{id} | 初始计划或调整草稿 |
| POST /plan-proposals/{id}/approve 或 /reject | 版本校验、确认或拒绝 |
| GET /plans/current；GET /plans/versions | 当前及历史计划 |
| POST /coach/messages | 对话；只能创建建议，不能直接写计划 |
| GET /weather；GET/PUT /notification-settings | 天气及提醒配置 |
| GET /usage；POST /exports；DELETE /account/data | 用量、导出、明确确认后的删除 |

## 隐私与可靠性

上传校验真实 MIME、像素上限与文件大小，拒绝解压炸弹；不接收任意外部图片 URL。私有原图不放公开静态目录。日志不记录密钥、图片 Base64、完整健康对话。删除覆盖原图、切片、提取缓存和引用；备份保留周期需在部署阶段明确。JSON/CSV 导出包括单位与来源。

无 AI 时仍可记录、查看计划；无天气时显示不可用及更新时间，不能编造预报。通知无可用渠道时显示未发送。只有正式配置且通过联调的服务才能标记已启用。

## M3 跑步与力量共同排期约束

力量使用独立输入模型与实际记录 API（GET/POST /strength-workouts），不改变 M1 必填距离的跑步输入，也不以 0 公里代替力量记录。计划批准按统一事实版本校验跑步、力量、身体反馈和日程；在同一事务创建包含两类训练的新 PlanVersion。动作只能引用动作库，服务端验证器械、适用限制、时长和规则审阅状态。详见 `docs/strength-and-coach.md`；这些表与接口已通过 0003 迁移实现；当前仅支持一般轻强度七天草稿，完整训练周期仍待后续。

### 身体反馈时序与训练引用

POST /checkins 接受 related_training_type（run/strength）及 related_training_id，必须成对且引用当前用户的真实训练；反馈与计划项分开。feedback_phase 支持 unknown/before/during/after/next_day。observed_time 是档案时区中的 HH:MM 输入，或通过 observed_at 提供带偏移的 ISO 时间，不能同时填写。后端转换 UTC 并保存 observation_timezone；该来源时区不能由客户端伪造。无发生时间保留 null；旧记录来源时区也是 null，不回填。GET /checkins 补齐空值默认字段，页面标注原时区未知。日程日期和训练关系按反馈填写日期检查，未知开始/结束时间不推测。已知发生时间用于同日排序，未填时按保存时间兜底；同一排序用于页面与模型上下文。

### 教练问答持久化

CoachTurn 按 user_id/request_id 唯一，保存问题、结构化回复、来源、事实修订号、processing/succeeded/failed 状态及租约期限。POST /coach/messages 的成功请求可幂等重取；相同 ID 更换问题返回 409，处理中或失败不自动再调用。GET 返回最近 50 次提问及动态 context_stale/interrupted 提示，历史正文不构成事实。DeepSeek 对话上下文最多最近六次成功问答，回答仅供解释与核对；没有计划写权限。预算 task_kind=coach_chat，和识别、联合计划共用账本；费用未知保持预留。

## 详细指标核对（0005）

import_metric_reviews为按草稿版本追加的人工事件，外键关联imports/user，唯一键(import_id,draft_revision)。payload保存字段、确认/不采用、值与标准单位、原图抄录与说明、来源索引/框/原组快照、cache_key和evidence_hash。指标保存同时对imports.revision及recognitions.updated_at/cache_key作CAS。原识别JSON不可由核对接口改写。

imports读接口返回核对历史及current标记，按字段最新事件和当前证据判定。整次确认后workouts.payload.confirmed_metrics冻结当前已确认指标；设备估计与人工核对标签同时保留。教练gather使用正式payload，尚未整次确认的核对事件不参与事实；已有用户级外发同意、预算和请求大小限制继续适用。


## 完整周期（runner-cycle-rules-1，无新增数据库迁移）

GET /assessment增加ability；PUT /cycle-schedule保存共同训练窗口；POST /cycle-proposals生成周蓝图并展开完整日期。PlanProposal/PlanVersion.payload用scope=cycle区分，保存ability、cycle_weeks、blueprint、来源与逐日差异；既有approve事务/CAS统一应用。完整周期不可直接按七天restore平移恢复，需重新评估剩余周期。Profile自述医生许可/可空就诊日期、Workout.session_context与Checkin.soreness_tolerability均为既有JSON的可选扩展；旧数据使用未知默认，不补造事实。模型只提供周蓝图，后端决定日期与校验，不得编造未知负荷。AI task_kind=coach_cycle共用原子预算；历史仅发送最新10/10/3条，周期日程需单独明确同意。已有长周期计划只发送本次七天明细与摘要，避免将180天每日内容无界外发。


## 天气缓存（0006）

weather_states按user_id唯一，revision与payload保存候选城市、已选城市中心、小时预报及获取/过期时刻。GET /weather只读已存快照，绝不隐式发外部请求；POST /weather/search与POST /weather/refresh要求allow_external_weather=true和expected_revision；PUT /weather/location仅从本账户已搜索候选选择。更换位置清空预报；返回较晚的旧请求通过CAS拒绝，不覆盖新位置。服务错误保留旧缓存，返回502；不回退虚构城市或天气。发送仅城市名/城市中心坐标，固定位于官方HTTPS端点，不发健康/账户/路线。预报快照独立于事实修订；只有完整周期生成明确选择include_weather时进入本次教练上下文，不直接更改正式计划。用户根据天气明确保存日程时仍按既有规则更新事实。AQI与官方预警待接入。


### 本地城市目录（无数据库迁移）

GET /weather/cities?q=只读带来源的GeoNames文件快照，返回最多10项，允许未启用天气时查询；鉴权仍必需，禁止后台外发。服务器按别名/ASCII名称规范化查询，空查询返回人口排序参考条目，未找到保持空。PUT /weather/location支持服务器目录ID，忽略不存在的ID并422，禁止客户端附带自造坐标；公共目录不受用户所有权限制，账户位置与缓存依旧隔离。选本地地点不增加训练事实或发送天气；刷新仍要求启用供应商和显式同意。已保存location_source=geonames_local或open_meteo_geocoding，逐条source链接可核对。城市参考坐标不承诺是行政几何中心或精确现场；与个人档案城市/时区分开。


完整周期天气联动：/cycle-proposals使用include_weather/avoid_rain/expected_weather_revision读取同账户未过期快照，冻结开始日起七天、档案训练时段起132分钟保守窗口。payload.weather_snapshot保留revision、rule_version、标准units、城市名称、source、fetched_at/expires_at、timezone/training_time、每日window_hours/window_complete/blocked_reason；没有预测窗口外天气，null不补值。排除雷暴户外跑，避雨仅在用户选择时；日期分配与验证仍遵守跑步/力量/恢复共同规则。天气数据仅在主动选择时进入本次DeepSeek蓝图请求，坐标不外发。模型不决定日期或直接更新。

weather_snapshot随PlanProposal/PlanVersion JSON持久化，无新增表；绑定提案到期取24小时与天气到期较早者。读取比较天气修订/到期，已变化草稿显示expired，正式计划needs_review且保留历史。确认时对WeatherState执行expected revision的无增量UPDATE获得SQLite写锁，再执行既有用户事实/计划CAS、提案状态CAS、正式版本及outbox的同一事务；生成期间变化拒绝保存。已确认提案重复请求保持幂等。天气搜索也递增revision，因此会保守地使相关未确认草稿失效。其他草稿不强制绑定天气；七天一般草稿目前使用日程手工调整。


## 本地模拟提醒（0007）

notification_settings以账户为主键，payload保存enabled、类别、提前分钟、每日限额和免打扰时间；修改携带expected_revision。notification_jobs按账户与版本/类别/日期dedupe_key唯一，保存事实/计划/设置版本、due_at/expires_at、attempts、state、通用内容及固定原因。preview与simulate显式请求，未到期不处理；GET不触发任务或外部调用。正式计划outbox作为更新提醒来源，模拟后通过任务唯一键去重，不将outbox标为真实送达。

单个SQLite事务取得账户写锁，核对当下正式版本、身体/天气/事实与偏好、取消旧任务、按时区延期/额度判断、模拟并保存结果；并发处理不会重复模拟或超每日限额。事实和正式版本写入同时取消pending。模拟错误区分可重试明确失败与unknown（不自动重试），异常文本不入库。独立worker只能显式--simulate，使用本地模拟器，每30秒或--once；当前未接网络通道、真实回执或自动进程启动。页面提醒列表仅显示最近50条。未来网络适配器需要独立的提交租约/不确定结果查询设计，不能直接把持有SQLite锁的模拟发送替换成长时间网络调用。

微信接入因用户不接受付费而暂缓；官方免费额度未作本项目渠道验证。不保存第三方密钥、不自动绑定或发测试消息。模拟记录绝不是已发送微信。


## 数据留存

GET /data/export仅当前账户，显式SQLite读事务建立同一快照，输出版本runner-data-export-1；UTC时间、原始JSON字段与unknown/null不补齐，提案存储状态与到期时间原样保留。识别通过imports所有权join，其他业务表按user_id限定；账户排除username/password_hash，Recognition排除lease_token/lease_until，图片缓存不输出。AIBudget为全账户总表不导出，输出当前用户AICall明细。ZIP默认data.json和manifest.json，主动选择原图后以import ID作包内文件名并校验SHA；临时文件响应结束清理，无可公开访问存储URL。

本机备份是独立格式runner-local-backup-1：完整SQLite文件与登记原图，含账户认证散列，禁止公开/提交；配置密钥不包含。脚本恢复到新目录并禁用会话/租约/提醒，不提供覆盖运行中数据库的操作。导出包与备份用途分开，无在线导入或删除API。本轮备份恢复已用真实本机副本验证；公网部署/数据删除待实现。
