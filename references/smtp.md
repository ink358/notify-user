# SMTP 渠道

脚本入口:`python scripts/notify.py send --channel smtp --to a@b.com --subject "..." --body "..."`。

**这是“不需要自有域名就能发给任意收件人”的渠道。** 用任意一个现成邮箱当发件人即可,所以当用户被 Resend 的域名限制卡住时,SMTP 往往是最快的出路。

## 密钥字段

| 字段 | 说明 |
|---|---|
| `SMTP_HOST` | 服务商 SMTP 地址 |
| `SMTP_PORT` | 465(SSL)或 587(STARTTLS) |
| `SMTP_SSL` | `true` = 直接 SSL;`false` = STARTTLS。不填则按端口猜(465 → SSL) |
| `SMTP_USER` | 通常是完整邮箱地址 |
| `SMTP_PASSWORD` | **授权码,不是登录密码** |
| `SMTP_FROM` | 发件地址;不填用 `SMTP_USER` |
| `SMTP_TO` | 默认收件人,`--to` 优先级更高 |

## 常用服务商

| 服务商 | HOST | PORT | SSL | 密码 |
|---|---|---|---|---|
| QQ 邮箱 | `smtp.qq.com` | 465 | true | 授权码 |
| 163 邮箱 | `smtp.163.com` | 465 | true | 授权码 |
| 126 邮箱 | `smtp.126.com` | 465 | true | 授权码 |
| Gmail | `smtp.gmail.com` | 587 | false | 应用专用密码(需开两步验证) |
| Outlook / Microsoft 365 | `smtp.office365.com` | 587 | false | 账户密码或应用密码 |
| iCloud | `smtp.mail.me.com` | 587 | false | App 专用密码 |
| 企业微信邮箱 | `smtp.exmail.qq.com` | 465 | true | 客户端专用密码 |

## 授权码怎么拿(QQ 为例)

QQ 邮箱网页版 → 设置 → 账户 → “POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务” → 开启 **SMTP 服务** → 短信验证 → 得到一串 16 位授权码。163 类似(设置 → POP3/SMTP/IMAP → 开启服务 → 新增授权码)。

**用登录密码会报 535**:`smtp 认证失败(535): b'Error: authentication failed'`。脚本会直接把这条抛出来并附带提示,看到 535 先让用户确认用的是授权码。

## 其它常见错误

| 返回 | 含义 | 处置 |
|---|---|---|
| `535` 认证失败 | 密码不是授权码,或服务未开启 | 生成/开启授权码 |
| `550` / `553` 发件地址被拒 | `SMTP_FROM` 与服务商要求的不一致 | 多数服务商要求 `SMTP_FROM` 等于 `SMTP_USER` |
| `554` 被判定为垃圾邮件 | 内容或发件域名信誉问题 | 改正文措辞、降低频率,或换渠道 |
| 连接超时 | 端口被网络屏蔽(常见于 465/587 被封) | 换端口;或改用 Resend/Telegram |
| `SSLError` / `wrong version number` | 端口与 TLS 方式不匹配 | 465 配 `SMTP_SSL=true`;587 配 `false` |
| `SMTPRecipientsRefused` | 收件人地址不存在或被拒 | 核对地址;QQ 对陌生地址会静默拒 |

## 送达率(给用户提前说清楚)

- 从 QQ 邮箱发给 QQ 邮箱:自家人发自家,基本进收件箱。
- QQ/163 对**新域名或陌生发件人**发来的邮件判定很激进,可能直接进垃圾箱甚至连垃圾箱都不进。
- 用自有域名做发件域名时,SPF、DKIM、DMARC 三样配齐能显著改善;三者缺一就容易进垃圾箱。
- 脚本只保证“投递到对方 SMTP 服务器被接受”,**不保证最终进收件箱**。汇报时区分这两种说法,不要含糊成“已送达”。

## 排障

```bash
# 只测 TCP 连通性(不读凭证之外的任何东西)
python scripts/notify.py ping --channel smtp
```

脚本对 SMTP 会把拒绝的收件人单独报出来(`smtp 部分收件人被拒: {...}`),多收件人时别把部分失败当全部成功。
