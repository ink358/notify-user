#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify-user — 通过 telegram / smtp / resend / serverchan / pushplus / moakt 给用户发一条通知。

设计约束(来自用户要求):
  * 密钥不放在 skill 里。密钥文件默认在 skill 目录**之外**的
    ~/.agents/secrets/notify-user.env,所以 skill 目录可以随意分享/同步。
  * 密钥只在本进程内读取,永不打印、永不回显;所有错误信息都会把密钥值替换成 ***。
  * 可选加密模式(lock):密钥文件被 scrypt + AES-256-GCM 加密成 .enc,
    没有口令连明文都拿不到 —— 连本 agent 也读不了。

输出约定:每次调用最后一行都是一行 JSON,便于机器解析。
    {"ok": true,  "channel": "smtp", "to": ["a@b.com"], "subject": "...", "detail": {...}}
    {"ok": false, "channel": "smtp", "code": 2, "error": "..."}

退出码:0 成功 / 1 用法或配置错误 / 2 发送失败 / 3 密钥被锁或口令错误 / 10 该渠道需要人工浏览器操作
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import smtplib
import socket
import ssl
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlencode

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT_REL = "scripts/notify.py"
DEFAULT_ENV = Path.home() / ".agents" / "secrets" / "notify-user.env"
EXAMPLE_ENV = SKILL_DIR / "assets" / "notify-user.env.example"
ENC_SUFFIX = ".enc"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_SEND_FAILED = 2
EXIT_LOCKED = 3
EXIT_MANUAL = 10

# 命中这些名字的字段一律视为凭证,不打印、错误信息里也要抹掉
SECRET_KEY_RE = re.compile(r"(TOKEN|PASSWORD|PASSWD|PASS\b|SECRET|API_?KEY|SENDKEY|_KEY$)", re.I)

CHANNEL_SPEC = {
    "telegram": {
        "required": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        "to": ("TELEGRAM_CHAT_ID",),
        "from": (),
        "kind": "api",
        "hint": "@BotFather 建机器人拿 token,chat_id 用 getUpdates 拿",
    },
    "smtp": {
        "required": ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"),
        "to": ("SMTP_TO",),
        "from": ("SMTP_FROM", "SMTP_USER"),
        "kind": "api",
        "hint": "任意现成邮箱即可,不需要自有域名(QQ/163 用授权码,不是登录密码)",
    },
    "resend": {
        "required": ("RESEND_API_KEY", "RESEND_FROM"),
        "to": ("RESEND_TO",),
        "from": ("RESEND_FROM",),
        "kind": "api",
        "hint": "沙箱只能发给账号本人;发给任意收件人必须先验证自有域名",
    },
    "serverchan": {
        "required": ("SERVERCHAN_KEY",),
        "to": (),
        "from": (),
        "kind": "api",
        "hint": "sct.ftqq.com 微信扫码登录→SendKey 页面复制;消息进微信「Server酱」服务号",
    },
    "pushplus": {
        "required": ("PUSHPLUS_TOKEN",),
        "to": (),
        "from": (),
        "kind": "api",
        "hint": "pushplus.plus 微信扫码登录→复制 token;消息进微信「pushplus 推送加」公众号",
    },
    "moakt": {
        "required": (),
        "to": ("MOAKT_TO",),
        "from": (),
        "kind": "browser",
        "hint": "浏览器驱动,无密钥;见 references/moakt.md",
    },
}
AUTO_ORDER_DEFAULT = ("telegram", "smtp", "resend", "serverchan", "pushplus")

# 密钥文件里的 NOTIFY_PROXY 由 load_cfg 填进来;环境变量优先
_PROXY_FROM_FILE: str | None = None


def effective_proxy() -> str | None:
    return os.environ.get("NOTIFY_PROXY") or _PROXY_FROM_FILE


class NotifyError(Exception):
    def __init__(self, message: str, code: int = EXIT_SEND_FAILED, channel: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.channel = channel


# --------------------------------------------------------------------------- 密钥文件

def env_path() -> Path:
    override = os.environ.get("NOTIFY_ENV")
    return Path(override).expanduser() if override else DEFAULT_ENV


def parse_env(text: str) -> dict:
    cfg: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        cfg[key.strip().upper()] = value
    return cfg


def _fernet(passphrase: str, salt: bytes):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    kdf = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8"))))


def encrypted_path(path: Path) -> Path:
    return Path(str(path) + ENC_SUFFIX)


def prompt_allowed() -> bool:
    """默认**不**交互式提示口令。

    agent/脚本环境下 stdin 常常是个 tty,一提示就会永久卡住等输入 —— 那比失败更糟。
    所以默认只认 NOTIFY_PASSPHRASE;人来用时显式开:NOTIFY_ALLOW_PROMPT=1.
    """
    return (os.environ.get("NOTIFY_ALLOW_PROMPT") or "").strip().lower() in ("1", "true", "yes", "on")


def get_passphrase(prompt: str = "notify-user 密钥口令: ", allow_prompt: bool | None = None) -> str:
    phrase = os.environ.get("NOTIFY_PASSPHRASE", "")
    if phrase:
        return phrase
    if allow_prompt is None:
        allow_prompt = prompt_allowed()
    if not allow_prompt:
        return ""
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def load_cfg() -> tuple:
    """读密钥。返回 (cfg, path, mode)。加密文件存在时优先用它。"""
    global _PROXY_FROM_FILE
    path = env_path()
    enc = encrypted_path(path)
    if enc.exists():
        phrase = get_passphrase()
        if not phrase:
            raise NotifyError(
                f"密钥已加密({enc.name}),但这次没有口令。设置环境变量 NOTIFY_PASSPHRASE 再运行;"
                f"人要在终端里被提示输入口令,加 NOTIFY_ALLOW_PROMPT=1。",
                EXIT_LOCKED,
            )
        blob = enc.read_bytes()
        try:
            plain = _fernet(phrase, blob[:16]).decrypt(blob[16:]).decode("utf-8")
        except Exception:
            raise NotifyError("口令被拒绝,或加密文件已损坏。", EXIT_LOCKED)
        cfg = parse_env(plain)
        _PROXY_FROM_FILE = cfg.get("NOTIFY_PROXY") or None
        return cfg, enc, "encrypted"
    if not path.exists():
        raise NotifyError(
            f"找不到密钥文件 {path}。先运行:{sys.executable} {SKILL_DIR / SCRIPT_REL} init",
            EXIT_USAGE,
        )
    cfg = parse_env(path.read_text(encoding="utf-8"))
    _PROXY_FROM_FILE = cfg.get("NOTIFY_PROXY") or None
    return cfg, path, "plain"


def scrub(text: str, cfg: dict | None) -> str:
    """把任何凭证值从文本里抹掉,防止密钥随错误信息泄漏。"""
    out = str(text)
    if cfg:
        for key, value in cfg.items():
            if value and SECRET_KEY_RE.search(key) and len(value) >= 6:
                out = out.replace(value, "***")
    return out


def harden(path: Path) -> str:
    """尽力收紧权限:POSIX 用 chmod 600,Windows 用 icacls 去掉继承。"""
    notes = []
    try:
        os.chmod(path, 0o600)
        notes.append("chmod 600")
    except OSError as exc:
        notes.append(f"chmod failed: {exc}")
    if os.name == "nt":
        user = os.environ.get("USERNAME") or ""
        if user:
            try:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    check=True, capture_output=True, timeout=20,
                )
                notes.append("icacls: 只保留当前用户")
            except Exception as exc:  # 非致命
                notes.append(f"icacls failed: {exc}")
    return "; ".join(notes)


# --------------------------------------------------------------------------- 渠道实现

def _send_json(url: str, payload: dict, headers: dict, cfg: dict, timeout: float, label: str) -> dict:
    """POST JSON 并返回解析结果。错误绝不回显 URL(可能内嵌 token)。"""
    opener = build_opener()
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(url, data=body, headers=api_headers(headers), method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {"status": resp.status, "body": json.loads(raw) if raw.strip() else {}}
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        detail = raw.strip()[:600]
        try:
            parsed = json.loads(raw)
            detail = parsed.get("message") or parsed.get("description") or detail
        except Exception:
            pass
        raise NotifyError(scrub(f"{label} HTTP {exc.code}: {detail}", cfg), EXIT_SEND_FAILED, label)
    except Exception as exc:
        raise NotifyError(
            scrub(f"{label} 请求失败: {type(exc).__name__} {exc}", cfg), EXIT_SEND_FAILED, label
        )


def _send_form(url: str, fields: dict, cfg: dict, timeout: float, label: str) -> dict:
    """POST 表单编码并返回解析结果。错误绝不回显 URL(Server酱的 URL 内嵌 SendKey)。"""
    opener = build_opener()
    body = urlencode(fields).encode("utf-8")
    req = urlrequest.Request(
        url, data=body,
        headers=api_headers({"Content-Type": "application/x-www-form-urlencoded"}),
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {"status": resp.status, "body": json.loads(raw) if raw.strip() else {}}
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        detail = raw.strip()[:600]
        try:
            parsed = json.loads(raw)
            detail = parsed.get("message") or detail
        except Exception:
            pass
        raise NotifyError(scrub(f"{label} HTTP {exc.code}: {detail}", cfg), EXIT_SEND_FAILED, label)
    except Exception as exc:
        raise NotifyError(
            scrub(f"{label} 请求失败: {type(exc).__name__} {exc}", cfg), EXIT_SEND_FAILED, label
        )


API_UA = "notify-user/1.0 (+https://localhost; python-urllib)"


def build_opener():
    """默认忽略环境里的 *_proxy(让 Python 悄悄继承系统代理时,出问题很难定位);
    只有显式给了代理才走代理 —— 环境变量 NOTIFY_PROXY,或密钥文件里的同名键。"""
    proxy = effective_proxy()
    if proxy:
        handler = urlrequest.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urlrequest.ProxyHandler({})
    return urlrequest.build_opener(handler)


def api_headers(extra: dict | None = None) -> dict:
    """必须带一个像样的 User-Agent:Resend 前面的 Cloudflare 会拦 python-urllib 默认 UA
    (实测返回 HTTP 403 error code: 1010),带上就正常。"""
    headers = {"User-Agent": API_UA, "Accept": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def split_recipients(value) -> list:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = re.split(r"[,;\s]+", str(value))
    return [item for item in (x.strip() for x in items) if item]


def resolve_to(cfg: dict, channel: str, explicit) -> list:
    recipients = split_recipients(explicit)
    if recipients:
        return recipients
    for key in CHANNEL_SPEC[channel]["to"]:
        if cfg.get(key):
            return split_recipients(cfg[key])
    return []


def resolve_from(cfg: dict, channel: str) -> str:
    for key in CHANNEL_SPEC[channel]["from"]:
        if cfg.get(key):
            return cfg[key]
    return ""


def send_telegram(cfg: dict, to: list, subject: str, body: str, timeout: float) -> dict:
    token = cfg["TELEGRAM_BOT_TOKEN"]
    text = f"{subject}\n\n{body}" if subject else body
    payload = {"chat_id": to[0], "text": text, "disable_web_page_preview": True}
    result = _send_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        payload,
        {"Content-Type": "application/json"},
        cfg, timeout, "telegram",
    )
    if not result["body"].get("ok", True):
        raise NotifyError(f"telegram 拒绝发送: {result['body'].get('description')}", EXIT_SEND_FAILED)
    return {"message_id": result["body"].get("result", {}).get("message_id"), "chat_id": to[0]}


def send_resend(cfg: dict, to: list, subject: str, body: str, timeout: float) -> dict:
    html = "<p>" + body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>") + "</p>"
    payload = {
        "from": resolve_from(cfg, "resend"),
        "to": to,
        "subject": subject,
        "text": body,
        "html": html,
    }
    result = _send_json(
        "https://api.resend.com/emails",
        payload,
        {"Authorization": f"Bearer {cfg['RESEND_API_KEY']}", "Content-Type": "application/json"},
        cfg, timeout, "resend",
    )
    return {"id": result["body"].get("id"), "to": to}


def send_smtp(cfg: dict, to: list, subject: str, body: str, timeout: float) -> dict:
    host = cfg["SMTP_HOST"]
    raw_port = cfg.get("SMTP_PORT") or ""
    port = int(raw_port) if raw_port.isdigit() else 465
    flag = (cfg.get("SMTP_SSL") or "").strip().lower()
    use_ssl = port == 465 if not flag else flag in ("1", "true", "yes", "on")
    sender = resolve_from(cfg, "smtp") or cfg["SMTP_USER"]

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject or "(无主题)"
    msg.set_content(body)

    context = ssl.create_default_context()
    server = None
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        refused = server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        code = exc.smtp_code
        detail = (exc.smtp_error or b"").decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else exc.smtp_error
        raise NotifyError(scrub(f"smtp 认证失败({code}): {detail} —— 很多邮箱要求用授权码而非登录密码", cfg), EXIT_SEND_FAILED)
    except smtplib.SMTPRecipientsRefused as exc:
        raise NotifyError(scrub(f"smtp 收件人被拒: {exc.recipients}", cfg), EXIT_SEND_FAILED)
    except (smtplib.SMTPException, socket.error, OSError, ssl.SSLError) as exc:
        raise NotifyError(scrub(f"smtp 连接/发送失败: {type(exc).__name__} {exc}", cfg), EXIT_SEND_FAILED)
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass
    if refused:
        raise NotifyError(scrub(f"smtp 部分收件人被拒: {list(refused)}", cfg), EXIT_SEND_FAILED)
    return {"from": sender, "host": host, "port": port, "tls": "ssl" if use_ssl else "starttls", "to": to}


def send_serverchan(cfg: dict, to: list, subject: str, body: str, timeout: float) -> dict:
    """Server酱(Turbo):消息发到 SendKey 绑定的微信「Server酱」服务号。
    成功判定:HTTP 200 且业务 code==0 —— HTTP 可能照样回 200,必须看业务 code。"""
    title = subject or "通知"
    result = _send_form(
        f"https://sctapi.ftqq.com/{cfg['SERVERCHAN_KEY']}.send",
        {"title": title, "desp": body},
        cfg, timeout, "serverchan",
    )
    if str(result["body"].get("code")) != "0":
        raise NotifyError(
            f"serverchan 推送失败: code={result['body'].get('code')} "
            f"{str(result['body'].get('message', ''))[:200]}".strip(),
            EXIT_SEND_FAILED,
        )
    return {"pushid": result["body"].get("pushid"), "to": "微信「Server酱」服务号"}


def send_pushplus(cfg: dict, to: list, subject: str, body: str, timeout: float) -> dict:
    """PushPlus:消息发到 token 绑定的微信「pushplus 推送加」公众号。成功判定:业务 code==200。"""
    payload = {
        "token": cfg["PUSHPLUS_TOKEN"],
        "title": subject or "通知",
        "content": body,
        "template": "txt",
    }
    result = _send_json(
        "https://www.pushplus.plus/send",
        payload,
        {"Content-Type": "application/json"},
        cfg, timeout, "pushplus",
    )
    if str(result["body"].get("code")) != "200":
        raise NotifyError(
            f"pushplus 推送失败: code={result['body'].get('code')} "
            f"{str(result['body'].get('msg', ''))[:200]}".strip(),
            EXIT_SEND_FAILED,
        )
    return {"trace_id": result["body"].get("data"), "to": "微信「pushplus 推送加」公众号"}


def send_moakt(cfg: dict, to: list, subject: str, body: str, timeout: float) -> dict:
    raise NotifyError(
        "moakt 渠道必须走浏览器自动化(无 API)。按 references/moakt.md 用 browser-use 的 "
        f"control-browser 操作;发件地址 {cfg.get('MOAKT_NAME', 'notification')}@"
        f"{cfg.get('MOAKT_DOMAIN', 'drmail.in')},收件人 {to[0] if to else cfg.get('MOAKT_TO', '')}。",
        EXIT_MANUAL,
        "moakt",
    )


SENDERS = {
    "telegram": send_telegram,
    "resend": send_resend,
    "smtp": send_smtp,
    "serverchan": send_serverchan,
    "pushplus": send_pushplus,
    "moakt": send_moakt,
}


def channel_configured(cfg: dict, channel: str) -> tuple:
    missing = [key for key in CHANNEL_SPEC[channel]["required"] if not cfg.get(key)]
    if channel in ("smtp", "resend", "telegram") and not resolve_to(cfg, channel, None):
        default_to = CHANNEL_SPEC[channel]["to"][0]
        if default_to not in missing:
            missing.append(default_to)
    return (not missing), missing


def auto_order(cfg: dict) -> list:
    raw = cfg.get("NOTIFY_CHANNEL_ORDER") or ""
    order = [item.strip().lower() for item in re.split(r"[,;\s]+", raw) if item.strip()]
    return [c for c in order if c in CHANNEL_SPEC] or list(AUTO_ORDER_DEFAULT)


def run_send(cfg: dict, channel: str, to: list, subject: str, body: str, timeout: float, dry_run: bool) -> dict:
    spec = CHANNEL_SPEC[channel]
    if channel in ("smtp", "resend") and not subject:
        raise NotifyError(f"{channel} 需要主题(--subject)", EXIT_USAGE, channel)
    if not body.strip():
        raise NotifyError("正文为空,拒绝发送空通知", EXIT_USAGE, channel)
    ok, missing = channel_configured(cfg, channel)
    if not ok:
        raise NotifyError(
            f"{channel} 未配置,缺少 {missing} —— 补齐后重试,或换一个已配置的渠道"
            f"(python {SKILL_DIR / SCRIPT_REL} status 可看全貌)",
            EXIT_USAGE, channel,
        )
    if channel == "telegram" and len(to) > 1:
        to = to[:1]
    if dry_run:
        sender = resolve_from(cfg, channel)
        if channel == "telegram":
            sender = sender or "(Telegram 机器人自身)"
        elif channel == "serverchan":
            sender = sender or "(Server酱 服务号)"
        elif channel == "pushplus":
            sender = sender or "(pushplus 推送加 公众号)"
        return {
            "dry_run": True,
            "channel": channel,
            "kind": spec["kind"],
            "to": to,
            "from": sender,
            "subject": subject,
            "body_len": len(body),
        }
    if spec["kind"] == "browser":
        return SENDERS[channel](cfg, to, subject, body, timeout)
    if spec["to"] and not to:
        raise NotifyError(f"{channel} 没有收件人:加 --to,或在密钥文件里设 {spec['to'][0]}", EXIT_USAGE, channel)
    detail = SENDERS[channel](cfg, to, subject, body, timeout)
    return {"channel": channel, "to": to, "subject": subject, "detail": detail}


# --------------------------------------------------------------------------- 子命令

def cmd_init(args) -> int:
    path = env_path()
    if path.exists() and not args.force:
        print(json.dumps({"ok": True, "path": str(path), "action": "exists"}, ensure_ascii=False))
        return EXIT_OK
    path.parent.mkdir(parents=True, exist_ok=True)
    template = EXAMPLE_ENV.read_text(encoding="utf-8") if EXAMPLE_ENV.exists() else "# notify-user secrets\n"
    path.write_text(template, encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(path), "action": "created", "perms": harden(path)}, ensure_ascii=False))
    return EXIT_OK


def cmd_path(args) -> int:
    path = env_path()
    enc = encrypted_path(path)
    print(json.dumps({
        "ok": True,
        "secrets_path": str(path),
        "exists": path.exists(),
        "encrypted_path": str(enc),
        "encrypted_exists": enc.exists(),
        "override_env": os.environ.get("NOTIFY_ENV") or None,
    }, ensure_ascii=False))
    return EXIT_OK


def cmd_status(args) -> int:
    path = env_path()
    try:
        cfg, used, mode = load_cfg()
    except NotifyError as exc:
        print(json.dumps({"ok": False, "locked_or_missing": True, "path": str(path),
                          "code": exc.code, "error": exc.message}, ensure_ascii=False))
        return exc.code
    channels = {}
    for channel in CHANNEL_SPEC:
        ok, missing = channel_configured(cfg, channel)
        channels[channel] = {
            "configured": ok,
            "missing": missing,
            "kind": CHANNEL_SPEC[channel]["kind"],
            "to": resolve_to(cfg, channel, None)[:3],
            "from": resolve_from(cfg, channel),
            "hint": CHANNEL_SPEC[channel]["hint"],
        }
    print(json.dumps({
        "ok": True,
        "secrets_file": str(used),
        "mode": mode,
        "default_channel": cfg.get("NOTIFY_DEFAULT_CHANNEL") or "auto",
        "auto_order": auto_order(cfg),
        "proxy": effective_proxy() or "(不走代理:忽略系统 *_proxy)",
        "channels": channels,
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_send(args) -> int:
    cfg, used, mode = load_cfg()
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    if body == "-":
        body = sys.stdin.read()
    body = body or ""
    subject = args.subject or ""
    if not subject and args.channel in ("smtp", "resend"):
        subject = "ZCode 通知"
    channel = (args.channel or cfg.get("NOTIFY_DEFAULT_CHANNEL") or "auto").lower()
    if channel == "auto":
        order = auto_order(cfg)
        failures = []
        for candidate in order:
            ok, missing = channel_configured(cfg, candidate)
            if not ok:
                failures.append({"channel": candidate, "error": f"未配置,缺少 {missing}"})
                continue
            try:
                result = run_send(cfg, candidate, resolve_to(cfg, candidate, args.to), subject, body,
                                  args.timeout, args.dry_run)
                print(json.dumps({"ok": True, "secrets_file": str(used), "mode": mode,
                                  "attempts": failures, **result}, ensure_ascii=False))
                return EXIT_OK
            except NotifyError as exc:
                failures.append({"channel": candidate, "code": exc.code, "error": scrub(exc.message, cfg)})
        print(json.dumps({"ok": False, "channel": "auto", "code": EXIT_SEND_FAILED,
                          "error": "所有渠道都失败", "attempts": failures}, ensure_ascii=False))
        return EXIT_SEND_FAILED
    if channel not in CHANNEL_SPEC:
        raise NotifyError(f"未知渠道 {channel};可选 {sorted(CHANNEL_SPEC)}", EXIT_USAGE)
    try:
        result = run_send(cfg, channel, resolve_to(cfg, channel, args.to), subject, body, args.timeout, args.dry_run)
    except NotifyError as exc:
        if exc.channel is None:
            exc.channel = channel
        raise
    print(json.dumps({"ok": True, "secrets_file": str(used), "mode": mode, **result}, ensure_ascii=False))
    return EXIT_OK


def cmd_lock(args) -> int:
    path = env_path()
    if not path.exists():
        raise NotifyError(f"没有明文的密钥文件可加密: {path}", EXIT_USAGE)
    phrase = Path(args.passphrase_file).read_text(encoding="utf-8").strip() if args.passphrase_file \
        else get_passphrase("设置加密口令(至少 8 位): ")
    if len(phrase) < 8:
        raise NotifyError(
            "没拿到口令(至少 8 个字符)。用 NOTIFY_PASSPHRASE 传,或人在终端里加 NOTIFY_ALLOW_PROMPT=1 运行。",
            EXIT_USAGE,
        )

    salt = os.urandom(16)
    token = _fernet(phrase, salt).encrypt(path.read_bytes())
    enc = encrypted_path(path)
    enc.write_bytes(salt + token)
    harden(enc)
    path.unlink()
    print(json.dumps({
        "ok": True, "encrypted": str(enc), "plaintext_removed": str(path), "perms": harden(enc),
        "note": "此后需要 NOTIFY_PASSPHRASE(或交互输入口令)。SSD 上已删除文件的残留无法保证清除。",
    }, ensure_ascii=False))
    return EXIT_OK


def cmd_unlock(args) -> int:
    path = env_path()
    enc = encrypted_path(path)
    if not enc.exists():
        raise NotifyError(f"没有加密文件可解开: {enc}", EXIT_USAGE)
    cfg, used, _ = load_cfg()
    if used != enc:
        raise NotifyError("内部错误:未按加密文件读取", EXIT_USAGE)
    path.write_text(_plain_dump(cfg), encoding="utf-8")
    harden(path)
    enc.unlink()
    print(json.dumps({"ok": True, "plaintext": str(path), "encrypted_removed": str(enc),
                      "perms": harden(path)}, ensure_ascii=False))
    return EXIT_OK


def _plain_dump(cfg: dict) -> str:
    """把已解开的配置写回明文(注释无法保留,按 key 排序输出)。"""
    return "\n".join(f"{key}={cfg[key]}" for key in sorted(cfg) if key) + "\n"


def cmd_ping(args) -> int:
    channel = (args.channel or "telegram").lower()
    try:  # 密钥文件里的 NOTIFY_PROXY 也要生效;读不到不影响探测
        load_cfg()
    except NotifyError:
        pass
    if channel == "smtp":
        cfg, _, _ = load_cfg()
        host = cfg.get("SMTP_HOST")
        if not host:
            raise NotifyError("密钥里没有 SMTP_HOST", EXIT_USAGE)
        raw_port = cfg.get("SMTP_PORT") or "465"
        port = int(raw_port) if raw_port.isdigit() else 465
        try:
            with socket.create_connection((host, port), timeout=args.timeout):
                pass
            print(json.dumps({"ok": True, "channel": "smtp", "reachable": f"{host}:{port}"}, ensure_ascii=False))
            return EXIT_OK
        except Exception as exc:
            print(json.dumps({"ok": False, "channel": "smtp", "code": EXIT_SEND_FAILED,
                              "error": f"连不上 {host}:{port}: {type(exc).__name__} {exc}"}, ensure_ascii=False))
            return EXIT_SEND_FAILED
    if channel == "resend":
        url = "https://api.resend.com/domains"
    elif channel == "telegram":
        url = "https://api.telegram.org/"
    elif channel == "serverchan":
        url = "https://sctapi.ftqq.com/"
    elif channel == "pushplus":
        url = "https://www.pushplus.plus/"
    else:
        raise NotifyError(f"ping 只支持 telegram/resend/smtp/serverchan/pushplus,收到 {channel}", EXIT_USAGE)
    req = urlrequest.Request(url, method="GET")
    try:
        with build_opener().open(req, timeout=args.timeout) as resp:
            status = resp.status
        print(json.dumps({"ok": True, "channel": channel, "status": status, "note": "TLS 通,能到达"}, ensure_ascii=False))
        return EXIT_OK
    except urlerror.HTTPError as exc:
        print(json.dumps({"ok": True, "channel": channel, "status": exc.code,
                          "note": "TLS 通,能到达(HTTP 错误码属正常,说明端点可达)"}, ensure_ascii=False))
        return EXIT_OK
    except Exception as exc:
        print(json.dumps({"ok": False, "channel": channel, "code": EXIT_SEND_FAILED,
                          "error": f"不可达: {type(exc).__name__} {exc}",
                          "hint": "本机需要代理时设 NOTIFY_PROXY(你的本地代理地址,如 http://127.0.0.1:7890)"}, ensure_ascii=False))
        return EXIT_SEND_FAILED


def cmd_harden(args) -> int:
    path = env_path()
    target = encrypted_path(path) if encrypted_path(path).exists() else path
    if not target.exists():
        raise NotifyError(f"文件不存在: {target}", EXIT_USAGE)
    print(json.dumps({"ok": True, "path": str(target), "perms": harden(target)}, ensure_ascii=False))
    return EXIT_OK


# --------------------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notify.py", description="多渠道给用户发通知(密钥不落在 skill 里)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="按模板创建密钥文件(默认在 skill 之外)")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在的密钥文件")
    p_init.set_defaults(func=cmd_init)

    sub.add_parser("path", help="打印密钥文件位置").set_defaults(func=cmd_path)
    sub.add_parser("status", help="列出各渠道是否配置齐(不打印任何密钥)").set_defaults(func=cmd_status)
    sub.add_parser("harden", help="收紧密钥文件权限").set_defaults(func=cmd_harden)

    p_send = sub.add_parser("send", help="发一条通知")
    p_send.add_argument("--channel", default=None,
                        help="telegram|smtp|resend|serverchan|pushplus|moakt|auto(默认取密钥里的 NOTIFY_DEFAULT_CHANNEL)")
    p_send.add_argument("--to", default=None, help="收件人,可用逗号分隔;不填则用密钥里的默认值")
    p_send.add_argument("--subject", default=None, help="主题(smtp/resend 必填)")
    p_send.add_argument("--body", default=None, help="正文;传 - 表示从 stdin 读")
    p_send.add_argument("--body-file", default=None, help="从文件读正文")
    p_send.add_argument("--timeout", type=float, default=30.0)
    p_send.add_argument("--dry-run", action="store_true", help="只校验配置,不真的发")
    p_send.set_defaults(func=cmd_send)

    p_lock = sub.add_parser("lock", help="用口令加密密钥文件(之后没有口令读不了)")
    p_lock.add_argument("--passphrase-file", default=None, help="从文件读口令(不填则交互输入)")
    p_lock.set_defaults(func=cmd_lock)

    sub.add_parser("unlock", help="解密密钥文件回明文").set_defaults(func=cmd_unlock)

    p_ping = sub.add_parser("ping", help="探测渠道端点可达性(不需要凭证)")
    p_ping.add_argument("--channel", default="telegram", help="telegram|resend|smtp|serverchan|pushplus")
    p_ping.add_argument("--timeout", type=float, default=10.0)
    p_ping.set_defaults(func=cmd_ping)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = None
    try:
        return args.func(args)
    except NotifyError as exc:
        channel = getattr(exc, "channel", None)
        print(json.dumps({"ok": False, "channel": channel, "code": exc.code,
                          "error": scrub(exc.message, cfg)}, ensure_ascii=False))
        return exc.code
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "code": EXIT_USAGE, "error": "被中断"}, ensure_ascii=False))
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
