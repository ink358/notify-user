# Telegram 渠道

脚本入口:`python scripts/notify.py send --channel telegram --subject "..." --body "..."`(不填 `--to` 就用密钥里的 `TELEGRAM_CHAT_ID`)。

手机弹窗、无需域名、无需收件人邮箱——**要“立刻到手”的提醒时首选**。国内网络直连不到 `api.telegram.org`,通常需要代理,见下面“网络”。

## 密钥字段

| 字段 | 说明 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 形如 `123456:ABC-DEF...`,来自 @BotFather |
| `TELEGRAM_CHAT_ID` | 目标会话 id,私聊是正数,群/频道是负数(`-100...`) |

## 建机器人

1. Telegram 里搜 `@BotFather` → `/newbot` → 起名字 → 起用户名(必须以 `bot` 结尾)。
2. 拿到 token。**只在创建时显示一次**,立刻存进密钥文件。
3. **必须先给机器人发一条消息**(搜它,点 Start 或随便发句“hi”)。否则机器人不能主动给你发消息——这是最常见的“明明 token 对却发不出去”的原因。

## 拿 chat_id

给机器人发过消息后:

```bash
curl -sS --noproxy '*' "https://api.telegram.org/bot<TOKEN>/getUpdates"
```

在返回里找 `"message":{"from":{"id":123456789,...},"chat":{"id":123456789,...}}`,`chat.id` 就是。群组的话把机器人拉进群、在群里发一条消息,同样从 `getUpdates` 里读(群 id 是负数,通常 `-100` 开头)。

想从群/频道发出去,还要在 @BotFather 里把 `/setprivacy` 设为 Disable,否则机器人读不到群消息。

> 注意:`getUpdates` 的返回里带 token 的 URL 会出现在你的命令历史里。用完别把 URL 粘进对话;脚本自己不会打印 token。

## 网络(国内必看)

脚本默认**忽略**系统里的 `http_proxy`/`https_proxy`,只在显式设了 `NOTIFY_PROXY` 时才走代理——因为让 Python 悄悄继承系统代理,出问题时很难判断卡在哪。

本机实测(2026-10):

| 目标 | 直连 | 经 `NOTIFY_PROXY`(本地代理,如 Clash 系 7890 端口) |
|---|---|---|
| `api.telegram.org` | 超时 | 可达(HTTP 200) |
| `core.telegram.org` | 超时 | 可达(HTTP 200) |
| `www.google.com` | 超时 | 可达(HTTP 200) |
| `api.resend.com` | 可达 | 可达 |

结论:本机直连境外端点不可靠,**Telegram 必须走代理**。所以默认密钥文件里已经打开了 `NOTIFY_PROXY`。改代理配置后先验证:

```bash
python scripts/notify.py ping --channel telegram
```

`ping` 返回 `ok:true` 并带一个 HTTP 状态码就说明 TLS 通、端点可达——**收到 HTTP 401/404 也算通**(那只是没带 token)。

**代理会抖**:实测代理刚启动或负载高时,单次请求可能报 `URLError timed out` 或 `SSL: UNEXPECTED_EOF_WHILE_READING`,隔几秒重试就通了。遇到这两种错误先重试一次再下结论,别立刻判断“代理不能用”。

## 接口与限制

- 发送:`POST https://api.telegram.org/bot<token>/sendMessage`,JSON 体 `{chat_id, text}`。
- 脚本把 `subject` 和 `body` 拼成 `主题\n\n正文`(有主题时)。
- 单条消息上限 4096 字符;超了要么拆多条,要么改成发文档。
- 限速约 30 条/秒、单聊 1 条/秒,正常用量碰不到。
- `disable_web_page_preview: true` 由脚本默认带上,避免正文里的链接撑出大预览。
- 脚本**不发** `parse_mode`,所以正文里的 `*`、`_`、`[` 等会按纯文本原样显示,不会因为 Markdown 转义问题报 400。如果确实要用 MarkdownV2,注意它要求对大量字符做转义,别自己拼。

## 常见错误

| 返回 | 含义 | 处置 |
|---|---|---|
| `401 Unauthorized` | token 错/被 revoke | 用 @BotFather 的 `/mybots` 重新拿 token |
| `400 chat not found` | chat_id 错,或**没先给机器人发过消息** | 先给机器人发消息再取 chat_id |
| `403 bot was blocked by the user` | 用户屏蔽了机器人 | 让用户解禁 |
| `400 message text is empty` | 正文为空 | 脚本已拦;别绕过 |
| 连接超时 | 需要代理 | 设 `NOTIFY_PROXY` |
