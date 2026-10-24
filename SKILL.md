---
name: notify-user
description: 通过多种渠道给用户发一条通知/消息/邮件的统一手册——Telegram 机器人、任意 SMTP 邮箱(QQ/163/Gmail/Outlook 授权码)、Resend 邮件 API、微信推送(Server酱 / PushPlus 服务号)、moakt 临时匿名邮箱。当用户要求“给我发个通知/发邮件/发信”“发消息到我手机/Telegram/微信”“用临时邮箱/匿名邮箱发”“帮我发一封邮件给某人”,或提到 notification@drmail.in、moakt、temp mail、Resend、SMTP、telegram bot、chat_id、Server酱、SendKey、pushplus、推送加、服务号、授权码、通知渠道时使用。也覆盖:密钥必须放在 skill 之外的文件里、发送前校验、失败排查(Resend 沙箱 403、免费公共域名 422、QQ 收件箱判定、SMTP 认证码)。不要因为没提到具体渠道就跳过——只要意图是“把一条消息送到用户手上”就用本技能。
---

# notify-user:多渠道给用户发通知

一条消息、六种送达方式。统一入口是一个脚本:`scripts/notify.py`。选渠道 → 填内容 → 跑脚本 → 读 JSON 结果。

## 铁律

1. **密钥永远不读进对话。** 不要 `cat`/`Read` 密钥文件,不要 `echo` 变量,不要用 `--debug` 打印配置。脚本自己读密钥、只输出结果;错误信息里的凭证已被替换成 `***`。密钥文件在 skill 目录**之外**(默认 `~/.agents/secrets/notify-user.env`),就是为了让 skill 可以随便分享。
2. **发送前先 `status`。** 知道哪个渠道真的配好了,再决定用哪个,别硬碰一个没配的渠道。
3. **先 `--dry-run` 再真发。** `--dry-run` 会校验收件人/主题/正文长度但不发出去,避免误发。
4. **只发一次。** 脚本返回 `ok:true` 就是发出去了(结果里有 message_id / 邮件 id),不要因为想确认就重复调用——重复调用会真的再发一封。
5. **正文由你写。** 用户没给内容时自己写:主题一句话、具体;正文简短正式、能自洽(说明这是谁发的、为什么发、是否需要用户做动作)。
6. **moakt 渠道不用脚本 `send`。** 它是浏览器驱动的,`send --channel moakt` 会以退出码 10 明确告诉你改走 `references/moakt.md`。别把退出码 10 当失败重试。
7. **失败要照实说。** 把脚本给的 `error` 原文(HTTP 码 + 平台原话)转述给用户,这些文字就是排查依据;不要粉饰成“可能发出去了”。

## 选渠道

| 渠道 | 需要什么 | 适合 | 关键限制 |
|---|---|---|---|
| `telegram` | Bot token + chat_id(密钥文件里填一次) | 要“立刻到手”的提醒;手机上弹窗 | 国内网络要代理:设 `NOTIFY_PROXY` |
| `smtp` | 一个现成邮箱 + 授权码 | 发给**任意收件人**且**不需要自有域名**——首选 | 邮箱服务商可能限频;QQ/网易对新域名邮件判定严 |
| `resend` | Resend API key(+ 已验证域名) | 有自有域名、要 API 化批量发 | 无已验证域名时只能发给账号本人;免费公共域名加不进去 |
| `serverchan` | 一个 SendKey(sct.ftqq.com 微信扫码) | 消息进**微信**「Server酱」服务号,手机立刻弹 | 免费版有每日推送条数限制;要先关注服务号 |
| `pushplus` | 一个 token(pushplus.plus 微信扫码) | 消息进**微信**「pushplus 推送加」公众号 | 免费版有每日条数限制;要先关注公众号 |
| `moakt` | 什么都不用配 | 要一次性、匿名的发件地址,不想暴露自己的邮箱 | **实验性**:浏览器自动化(需带浏览器控制能力的 agent 环境),人机验证靠坐标点;邮箱 1 小时失效 |

**怎么定:** 用户说“发我 Telegram/提醒我” → `telegram`;用户说“发微信/发到我手机微信” → `serverchan` 或 `pushplus`(看哪个已配置);用户给了邮箱地址要发信 → `smtp`(除非用户明确要 Resend);用户要匿名/临时 → `moakt`;用户明确说 Resend/API key → `resend`;用户只说“通知我”没说渠道 → `auto`,脚本按 `NOTIFY_CHANNEL_ORDER` 挑第一个配好的。

## 一条命令发出去

```bash
# 0) 看清哪些渠道可用(不打印任何密钥)
python scripts/notify.py status

# 1) 空跑校验
python scripts/notify.py send --channel smtp --to someone@qq.com \
  --subject "构建完成" --body "任务已跑完,无错误。" --dry-run

# 2) 真发
python scripts/notify.py send --channel smtp --to someone@qq.com \
  --subject "构建完成" --body "任务已跑完,无错误。"

# Telegram:--to 不填就用密钥里的 TELEGRAM_CHAT_ID
python scripts/notify.py send --channel telegram --subject "构建完成" --body "任务已跑完。"

# 长正文:从文件或 stdin
python scripts/notify.py send --channel resend --subject "日报" --body-file /tmp/report.txt
echo "多行正文" | python scripts/notify.py send --channel telegram --body -
```

脚本路径相对本 skill 目录。Windows Git Bash 下用 `python`;若 `python` 不在 PATH 试 `py -3`。

**成功**输出一行 JSON:`{"ok": true, "channel": "smtp", ..., "detail": {"message_id": ...}}`。**失败**也是 JSON:`{"ok": false, "code": 2, "error": "..."}`。

退出码:`0` 成功 / `1` 用法或配置问题 / `2` 发送失败 / `3` 密钥被锁或缺口令 / `10` 该渠道需要人工浏览器操作。

## 密钥(放在 skill 之外)

```bash
python scripts/notify.py path      # 密钥文件在哪、是明文还是加密
python scripts/notify.py init      # 按模板创建(在 skill 外,并尽力收紧权限)
python scripts/notify.py harden    # chmod 600 + Windows icacls 只留当前用户
```

位置解析顺序:环境变量 `NOTIFY_ENV` → 默认 `~/.agents/secrets/notify-user.env`。**不放 skill 目录内**,这样 skill 可以整体复制/同步而不带走凭证。填哪些字段看 `assets/notify-user.env.example`(每个字段上方都有注释说明去哪拿)。

**要更强的保护(连 agent 都读不了明文)**:

```bash
python scripts/notify.py lock      # 交互输入口令 → 生成 notify-user.env.enc(密文),删除明文
python scripts/notify.py unlock    # 用口令解回明文
```

加密用 scrypt + AES-256-GCM。之后每次发送都要有口令:环境变量 `NOTIFY_PASSPHRASE`;人在终端里想被提示输入,加 `NOTIFY_ALLOW_PROMPT=1`。**脚本默认不提示口令**(agent 环境里 stdin 常常是 tty,一提示就会永久卡住等输入),没有口令时立刻以退出码 3 报“密钥已加密”——这时别去猜口令、别找绕过的办法、也别加 `NOTIFY_ALLOW_PROMPT`,直接告诉用户需要提供口令。代价是 agent 不能无人值守发送,所以默认是明文模式;这个取舍由用户选。

给用户配 SMTP 时最容易踩的坑:**不要用登录密码,要用“授权码”**(QQ 邮箱:设置→账户→开启 SMTP 服务后生成;Gmail 用应用专用密码)。脚本报 535 认证失败时几乎都是这个原因。

## 发送后确认

以脚本退出码和 JSON 的 `ok` 为准,不要额外猜。用户可见的汇报包含:**渠道**、**发件地址**(`from`)、**收件人**(`to`)、**结果**与平台给的 id/原话。失败就把 `error` 原文贴出来并说清卡在哪一步。

## 各渠道细节

- **moakt / 临时邮箱**(浏览器流程,含 reCAPTCHA 坐标点击等硬坑):读 `references/moakt.md`
- **Resend**:`references/resend.md` —— 沙箱 403、免费公共域名 422、Broadcasts 前置条件的原话都在那里
- **SMTP**:`references/smtp.md` —— 各服务商 host/端口/TLS、授权码怎么拿
- **Telegram**:`references/telegram.md` —— 建 bot、拿 chat_id、代理
- **Server酱 / PushPlus(微信)**:`references/wechat.md` —— SendKey/token 怎么拿、每日限额、成功判定的坑

## 症状速查

| 症状 | 原因 | 处置 |
|---|---|---|
| `status` 报 `locked_or_missing`,code 3 | 密钥被 lock 了但这次没有口令 | 让用户给 `NOTIFY_PASSPHRASE`,或用户在交互终端跑;不要猜口令 |
| 找不到密钥文件,code 1 | 还没 init | `python scripts/notify.py init`,然后让用户填真值 |
| `渠道未配置,缺少 ['SMTP_PASSWORD', ...]` | 字段空着 | 告诉用户缺哪些字段(字段名可以讲,值不要读) |
| resend HTTP 403 `You can only send testing emails to your own email address` | 没有已验证域名,沙箱只允许发给账号本人 | 见 `references/resend.md`;要么换 smtp,要么验证自有域名 |
| resend HTTP 422 `We don't allow free public domains` | 用了免费公共域名(如 `dpdns.org`) | 该域名永远加不进 Resend;换付费自有域名或改用 smtp |
| resend HTTP 403 `Broadcasts cannot be sent from resend.dev` | Broadcasts 硬要求自有已验证域名 | 同上;或改事务邮件接口/换渠道 |
| smtp 535 / `认证失败(535)` | 用了登录密码而不是授权码 | 让用户生成授权码 |
| smtp 连接超时 / TLS 错误 | 端口与 TLS 方式不匹配 | 465 配 `SMTP_SSL=true`;587 配 `false`(STARTTLS) |
| 请求失败且提示 TLS/连接 | 系统代理没用上,或代理在抖 | 看密钥里的 `NOTIFY_PROXY`;`ping` 验证;`timed out` / `UNEXPECTED_EOF_WHILE_READING` 先重试一次再下结论 |
| telegram 不可达 | 国内直连不到 api.telegram.org | 设 `NOTIFY_PROXY` 指向本机代理(如 Clash 的 7890 端口);先 `ping` 验证,可达(200)再发 |
| serverchan 推送失败 `code=9xxxx` / 提示配额 | 免费版每日条数用完,或 SendKey 无效 | 次日重试,或换 `pushplus`/`smtp`;SendKey 在 sct.ftqq.com 的 SendKey 页面核对 |
| pushplus 推送失败 `code=9xx` / `msg=...` | 免费版每日条数用完,或 token 无效 | 次日重试,或换 `serverchan`/`smtp`;token 在 pushplus.plus 后台核对 |
| 微信收不到(Server酱/PushPlus 都返回成功) | 没关注对应服务号/公众号,或消息被微信折叠 | 让用户在微信里搜索并关注「Server酱」/「pushplus 推送加」,再触发一次 |
| resend 报 `HTTP 403: error code: 1010` | 自己写的客户端没带 User-Agent,被 Cloudflare 拦 | 脚本已带;写裸 urllib/requests 时补上 UA |
| `auto` 报“所有渠道都失败” | 每个渠道各自的原因都在 `attempts` 里 | 逐条读 `attempts`,别只看最后一句 |
| 退出码 10 | 选了 moakt 走脚本发送 | 按 `references/moakt.md` 用浏览器流程,别重试脚本 |
| `ping` 返回 HTTP 401/404 但仍标 `ok:true` | 端点可达(收到 HTTP 响应即通) | 正常,不是错误 |

`ping` 用来在不暴露凭证的前提下验证网络路径:`python scripts/notify.py ping --channel telegram|resend|smtp|serverchan|pushplus`。
