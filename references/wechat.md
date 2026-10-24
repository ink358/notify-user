# Server酱 / PushPlus(微信服务号推送)

两个"消息进微信"的公众号网关渠道,配置都只要**一个字符串**,是国内环境"要弹到微信上"的最省事选择。脚本入口:

```bash
python scripts/notify.py send --channel serverchan --subject "..." --body "..."
python scripts/notify.py send --channel pushplus  --subject "..." --body "..."
```

两个渠道都**没有收件人参数**:消息固定发到凭证绑定的那个微信账号。

## 密钥字段

| 字段 | 说明 |
|---|---|
| `SERVERCHAN_KEY` | Server酱 Turbo 的 SendKey,形如 `SCTxxxxxx` |
| `PUSHPLUS_TOKEN` | PushPlus 的 token,一长串十六进制 |

## Server酱(sct.ftqq.com)

1. 打开 <https://sct.ftqq.com/>,**微信扫码登录**。
2. 按页面引导**绑定微信 / 关注「Server酱」服务号**(不关注收不到消息——最常见的"发送成功却收不到"就是这个)。
3. 在 **SendKey** 页面复制 SendKey,填进密钥文件的 `SERVERCHAN_KEY`。

消息出现在微信「Server酱」服务号的会话里,标题=subject,正文=body(desp 支持 Markdown)。

### 成功判定(实测坑)

HTTP 状态码**不可信**:`sctapi.ftqq.com` 错了也常回 200。必须看业务字段 **`code == 0`** 才算成功;非 0 时看 `message`。脚本已按这个判定,失败会带出 `code=...` 和平台原话。

### 限额

免费版(Turbo)有**每日推送条数限制**,超限返回业务错误码而不是 HTTP 错误。推送频率也别贴着上限打,有频率风控。具体数值以官网当前说明为准。

## PushPlus(pushplus.plus)

1. 打开 <https://www.pushplus.plus/>,**微信扫码登录**。
2. 登录后首页复制你的 **token**,填进密钥文件的 `PUSHPLUS_TOKEN`。
3. 同样要**关注「pushplus 推送加」公众号**才能收到。

消息出现在「pushplus 推送加」公众号会话里。脚本用 `template: "txt"`(纯文本);想要 Markdown/HTML 排版可自己改 payload 的 `template` 字段(`markdown`/`html`)。

### 成功判定

业务字段 **`code == 200`** 才算成功,失败看 `msg`。免费版同样有**每日条数限制**,超限返回业务错误码。

## 两个渠道怎么选

| | Server酱 | PushPlus |
|---|---|---|
| 配置 | 一个 SendKey | 一个 token |
| 消息落在 | 「Server酱」服务号 | 「pushplus 推送加」公众号 |
| 免费限额 | 更紧(每日条数少) | 相对宽松 |
| 附加能力 | 企业微信通道(需另配) | 渠道对/群,webhook 转发 |

免费版都够"任务完成提醒"这种低频场景用。高频场景(轮询告警)要么上付费,要么换 `smtp`/`telegram`。

## 网络注意

`sctapi.ftqq.com` 和 `www.pushplus.plus` 都是**国内直连可达**的端点,**不需要** `NOTIFY_PROXY`。本机代理开着也不影响——脚本默认忽略系统 `*_proxy`,只在显式设了 `NOTIFY_PROXY` 时才走代理;若你全局设了 `NOTIFY_PROXY`,这两个端点走代理一般也能通,连不上就先 `ping`:

```bash
python scripts/notify.py ping --channel serverchan
python scripts/notify.py ping --channel pushplus
```

## 来源说明

这两个渠道的实现与成功判定来自一个跑了几个月的 GLaDOS 自动签到脚本的实战经验(那份代码同样踩过"HTTP 200 但实际失败"和"没关注服务号收不到"两个坑),移植进本 skill 时按 notify-user 的 JSON 输出/错误打码约定重写。
