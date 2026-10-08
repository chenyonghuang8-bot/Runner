# 家中局域网访问

用户选择家中访问，采用电脑直接提供HTTPS生产页面和API，不需要Docker、Caddy、域名或公网端口映射。服务只绑定指定RFC1918 IPv4地址，默认8443；不监听0.0.0.0或VPN测试网地址，不修改系统证书信任或路由器配置。

当前已启动：https://192.168.1.6:8443 。手机与电脑需连接同一家中网络，不能使用隔离的访客Wi-Fi。电脑保持开机且服务运行；休眠或网络切换会中断访问。使用现有账户登录，不创建第二个账户。生产模式不允许网页首次注册。

## 安卓首次使用

1. 将公开证书 `private/home-tls/runner-home-ca.cer` 从电脑复制到手机，例如通过USB。**只复制.cer，不复制ca.key、server.key或session-secret。**
2. 手机设置搜索“CA证书”，选择安装CA证书并选此文件；各品牌入口不同。这是让手机信任本项目本地HTTPS证书的操作，由你手动完成，程序不会代装或修改系统信任。系统若要求解锁或确认，请核对证书名Runner Home Local CA。
3. 在手机浏览器打开 https://192.168.1.6:8443 ，使用已有用户名和密码登录。证书安装成功后应正常验证；若仍出现证书警告，停止并核对安装/IP/浏览器，不选择绕过警告。

证书安装与手机登录尚未验收。公开CA可从Codex文件链接查看/保存；该证书没有私钥。以后不使用，可在手机“用户凭据/受信任凭据”中移除Runner Home Local CA。

## 重新启动

项目根目录执行：

```bash
npm run build --prefix frontend
backend/.venv/bin/python scripts/home.py prepare --ip 192.168.1.6
backend/.venv/bin/python scripts/home.py serve --ip 192.168.1.6
```

serve前检查证书、迁移数据库、托管frontend/dist并启用HTTPS，Ctrl+C停止。只读构建产物，不公开项目根目录；静态服务不跟随目录外链接。程序读取原.env的AI/天气/数据库配置，运行时覆盖生产地址和本地会话配置，不重写.env；没有复制健康资料或密钥到前端。仍使用现有data/runner.db与data/private，不引入新数据库。电脑开发入口127.0.0.1:5173保持独立，仅本机可访问；家中入口使用同一数据库，因此任何确认操作都作用于同一份真实资料。

CA及私钥保存在被忽略的private/home-tls，权限收紧；CA首次生成后复用，不自动重新创建手机受信的CA。服务器证书有效期365天，CA3650天；服务启动前检查服务器证书至少剩余一天，过期/缺失/签名错误会拒绝启动。续签需另行处理，不自动覆盖已有密钥。IP变更后先停止旧服务，在实际新的局域网IP上重新prepare/serve；新IP可沿用同一CA，手机访问地址也要更新。可在路由器为电脑设置固定DHCP租约，但程序未代改路由器。

## 当前验收

- 本机实际活动接口en1 IPv4为192.168.1.6，已绑定8443（HTTPS）。
- 使用ssl.create_default_context(cafile=项目CA)正常验证真实连接：`/`、`/api/v1/health`、`/api/v1/auth/status`为200；未登录profile为401；私钥、.env、API文档路径404。未禁用证书验证、未安装系统CA。
- 六项合成测试覆盖私有IPv4绑定、公开地址/回环/VPN地址拒绝、生产静态页面、鉴权与未知API404、目录外链接保护及Host校验；完整后端回归结果见implementation-plan。
- 尚未验证安卓信任/登录、Wi-Fi跨设备连通、防火墙/访客网络限制、电脑休眠恢复；没有声称手机已可用。

TLS配置使用[Uvicorn官方HTTPS参数](https://www.uvicorn.org/settings/#https)，静态服务使用[Starlette StaticFiles](https://www.starlette.io/staticfiles/)且follow_symlink=False。公网Docker部署方案作为以后可选方案保留，本轮不执行。
