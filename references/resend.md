# Resend 渠道

脚本入口:`python scripts/notify.py send --channel resend --to a@b.com --subject "..." --body "..."`,密钥字段 `RESEND_API_KEY` / `RESEND_FROM` / `RESEND_TO`。

## 前置条件(最容易卡在这)

**没有已验证域名时,`from` 只能是 `onboarding@resend.dev`,而且只能发给账号本人。** 越界发信会被拒(实测原话):

```
HTTP 403 validation_error
You can only send testing emails to your own email address (<账号邮箱>).
To send emails to other recipients, please verify a domain at resend.com/domains,
and change the `from` address to an email using this domain.
```

**免费公共域名加不进 Resend**(实测原话):

```
HTTP 422
We don't allow free public domains. Please use a domain you own instead.
```

实测被拒的例子:一个 `dpdns.org` 后缀的域名(`dpdns.org` 是 DigitalPlat 的免费域名)。拦的是“免费公共域名”这一整类,不是某一个后缀——换免费的其它后缀大概率一样被拒。所以想走 Resend 发给任意收件人,需要一个**自己付费注册的域名**。

**Broadcasts 比事务邮件更严**:它硬性要求自有已验证域名,连沙箱地址都不让用(实测原话):

```
HTTP 403 validation_error
Broadcasts cannot be sent from resend.dev. Please use a verified domain owned by your team.
```

也就是说:把收件人加进 Resend 的 contacts/audience **不能**绕过域名验证——联系人只解决“发给谁”,不改变“允许从哪发”。实测在已添加联系人的受众上发 Broadcast,依然被上面这条 403 拒掉。

## 验证自有域名要加的三组 DNS 记录

在 `resend.com/domains` 添加域名后,按面板给的记录到 DNS 服务商加,再点验证。子域名也可以验证(例如 `mail.example.com`),验证通过后该域名下**任意地址**都能当 `from`。

| 类型 | 名称(示例) | 作用 |
|---|---|---|
| MX | `send`(Return-Path 子域) | 退信/退订的处理 |
| TXT | `send` | SPF |
| TXT | `resend._domainkey` | DKIM |

如果域名挂在 Cloudflare 且绑了 Workers:加这些记录**不需要**改 Workers 配置,也不会动到现有路由。但**改 DNS 前先问用户**——尤其当域名上有生产流量时。注意 Cloudflare 代理(橙色云)不适用于这里要加的 MX/TXT,保持 DNS only。

## 发送接口

```bash
curl -sS --noproxy '*' -X POST https://api.resend.com/emails \
  -H "Authorization: Bearer $RESEND_API_KEY" -H "Content-Type: application/json" \
  -d '{"from":"Acme <notify@example.com>","to":["a@b.com"],"subject":"标题","text":"正文"}'
```

- `to` 最多 50 个地址;需要批量个性化(每封不同内容,一次最多 100 封)用 `POST /batch`。
- 默认限速约 2 请求/秒,超了返回 429。
- `text` 和 `html` 至少给一个;脚本两个都给(`html` 由 `text` 转义换行生成)。
- 免费额度:3000 封/月、100 封/天、3 个自定义域名。

## 其它可用命令

```bash
# 看账号下已添加的域名及其状态
curl -sS --noproxy '*' https://api.resend.com/domains -H "Authorization: Bearer $RESEND_API_KEY"

# 看联系人/受众(验证 Broadcast 到底能不能发时用得上)
curl -sS --noproxy '*' https://api.resend.com/contacts -H "Authorization: Bearer $RESEND_API_KEY"
```

按 API key 的权限,这些只读查询通常都能跑。**API key 只在创建时显示一次**,拿不到就只能在面板重建一个——所以它值放在密钥文件里。

## 本机 curl 的坑

系统里若有本机代理的 `http_proxy`(如 Clash 系 7890 端口),`curl` 走它做 TLS 握手会失败(exit 35 / `schannel: failed to receive handshake`)。加 `--noproxy '*'` 直连即可。Python 脚本不受影响——它默认忽略环境里的 `*_proxy`,只在显式设了 `NOTIFY_PROXY` 时才走代理。

## 自己写客户端时的坑:User-Agent

Resend 前面挂着 Cloudflare,**默认的 Python urllib UA 会被拦**,实测返回:

```
HTTP 403 {"error code": 1010}
```

这不是鉴权失败,也不是额度问题——加上一个正常 `User-Agent` 就通了。`scripts/notify.py` 里的 `api_headers()` 已经带上了,自己写脚本时记得带。`curl` 的默认 UA 不会被拦。
