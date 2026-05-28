"""
通知模块 · 多通道推送

支持通道：
- Server 酱 (sct.ftqq.com)：环境变量 SERVERCHAN_SENDKEY（免费 5 条/日）
- 企业微信群机器人：环境变量 WEWORK_WEBHOOK（免费 20 条/分钟）
- PushPlus：环境变量 PUSHPLUS_TOKEN（免费 5 条/日）
- ntfy.sh：环境变量 NTFY_TOPIC（开源免费，iOS 上无法通知 banner 显图）
- Bark (iOS)：环境变量 BARK_KEY（iOS 专用，通知 banner 上直接显图；
              可选 BARK_AES_KEY+BARK_AES_IV 走端到端 AES-128-CBC 加密；
              可选 BARK_SERVER 指定自建实例，默认 api.day.app）

若均未配置，静默跳过（本地开发默认不发）
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

import requests


# 用于从 markdown 文本里提取第一张图片 URL（给 ntfy attach 用）
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s\)]+)\)")


def repo_raw_url(rel_path: str) -> Optional[str]:
    """
    把 repo 内相对路径（如 data/img/xxx.png）转为 raw.githubusercontent.com URL

    @param rel_path 相对仓库根目录的文件路径
    @returns URL 字符串；若环境变量 GITHUB_REPOSITORY 不存在返回 None
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        return None
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel_path.lstrip('/')}"


def _send_serverchan(title: str, content: str, sendkey: str) -> bool:
    """
    Server 酱推送（Markdown，支持图片 URL）
    """
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    try:
        resp = requests.post(
            url,
            data={"title": title[:32], "desp": content},
            timeout=15,
        )
        ok = resp.status_code == 200 and resp.json().get("code") == 0
        if not ok:
            print(f"[notify] Server酱 失败: {resp.text[:200]}")
        return ok
    except Exception as e:
        print(f"[notify] Server酱 异常: {e}")
        return False


def _send_wework(title: str, content: str, webhook: str) -> bool:
    """
    企业微信群机器人（markdown 格式）
    """
    try:
        resp = requests.post(
            webhook,
            json={
                "msgtype": "markdown",
                "markdown": {"content": f"### {title}\n\n{content}"},
            },
            timeout=10,
        )
        ok = resp.status_code == 200 and resp.json().get("errcode") == 0
        if not ok:
            print(f"[notify] 企业微信 失败: {resp.text[:200]}")
        return ok
    except Exception as e:
        print(f"[notify] 企业微信 异常: {e}")
        return False


def _send_pushplus(title: str, content: str, token: str) -> bool:
    """
    PushPlus 推送
    """
    try:
        resp = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": title, "content": content, "template": "markdown"},
            timeout=10,
        )
        ok = resp.status_code == 200 and resp.json().get("code") == 200
        if not ok:
            print(f"[notify] PushPlus 失败: {resp.text[:200]}")
        return ok
    except Exception as e:
        print(f"[notify] PushPlus 异常: {e}")
        return False


def _send_ntfy(title: str, content: str, topic: str, server: str = "https://ntfy.sh") -> bool:
    """
    ntfy.sh 推送（开源免费，无需注册）

    @param topic 用户自定义的频道名（公开字符串，知道名字就能订阅/推送，建议用难猜随机串）
    @param server ntfy 服务器地址，默认 https://ntfy.sh，可改为自建实例

    iOS app 显示策略（实测限制 2026-05-28）：
    - ntfy iOS app **不渲染 markdown**（`![alt](url)` 当字面文本显示）
    - ntfy iOS app **不在消息详情里 inline 显示附件**（attach / PUT 上传都不出现 UI）
    - 唯一能看图的方式：把 URL 当纯文本，让 iOS 自动识别成可点击 hyperlink → 点了跳 Safari
    - 额外加 Click：iOS 用户**点通知本身**就直接跳第一张图（最快路径）

    所以这里对 content 做预处理：
    - 把所有 `![alt](url)` 替换为 `📷 alt: url`（纯文本 URL 才能被 iOS link 化）
    - 抽取第一张图设为 click 字段（点通知 = 浏览器看图）
    """
    # 提取所有图片 URL，第一张用于 click
    first_img: Optional[str] = None
    if (m := _MD_IMAGE_RE.search(content)):
        first_img = m.group(1)

    # 把 markdown 图片语法换成纯文本 URL，确保 ntfy iOS 把 URL 渲染成可点击 hyperlink
    def _md_img_to_text(match: re.Match) -> str:
        # 从原始 `![alt](url)` 取 alt 文字
        raw = match.group(0)
        alt = raw.split("![", 1)[1].split("]", 1)[0] or "image"
        url = match.group(1)
        return f"📷 {alt}: {url}"

    content_for_ntfy = _MD_IMAGE_RE.sub(_md_img_to_text, content)

    payload = {
        "topic": topic,
        "title": title,
        "message": content_for_ntfy,
        # priority 1-5，3 = default。预测/开奖通知用 default 即可，不打扰
        "priority": 3,
        # tags 在 iOS app 上显示为 emoji，便于扫一眼分类
        "tags": ["bell"],
    }

    # 点通知本身 = 打开第一张图（iOS 上唯一直接看图的入口）
    if first_img:
        payload["click"] = first_img

    try:
        # 文档：https://docs.ntfy.sh/publish/#publish-as-json
        resp = requests.post(server.rstrip("/"), json=payload, timeout=10)
        ok = resp.status_code in (200, 201)
        if not ok:
            print(f"[notify] ntfy 失败 ({resp.status_code}): {resp.text[:200]}")
        return ok
    except Exception as e:
        print(f"[notify] ntfy 异常: {e}")
        return False


def _bark_aes_encrypt(plaintext: str, key: str, iv: str) -> str:
    """
    Bark AES-128-CBC + PKCS7 + Base64 加密

    @param plaintext 待加密的 JSON 字符串（Bark payload 整体）
    @param key 16 字节 ASCII 字符串（AES-128 用 16 字节 key）
    @param iv 16 字节 ASCII 字符串
    @returns Base64 编码的密文（ASCII 字符串）
    """
    # 延迟 import，未配置加密的用户不需要装 pycryptodome
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    import base64

    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
    encrypted = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(encrypted).decode("ascii")


def _send_bark(
    title: str,
    content: str,
    device_key: str,
    aes_key: Optional[str] = None,
    aes_iv: Optional[str] = None,
    server: str = "https://api.day.app",
) -> bool:
    """
    Bark iOS 推送（开源免费，iOS 通知 banner 上直接显示图片）

    @param device_key Bark device key（约 22 位字母数字，从 Bark app 复制）
    @param aes_key 可选 16 字节 ASCII AES key（与 Bark app 内配置一致才能解密）
    @param aes_iv 可选 16 字节 ASCII IV
    @param server Bark 服务器，默认 https://api.day.app（作者公共实例）

    iOS app 显示策略：
    - 提取 markdown 第一张图作为 image 参数 → Bark iOS app 在通知 banner 上 inline 显示
    - body 里 markdown 图语法替换为 `📷 alt`（不显示原 URL，避免 banner 太长）
    - level=active 确保即时铃声 + banner（不被 iOS 定时摘要吞）
    """
    # 提取 markdown 第一张图给 Bark image 字段（Bark 自动下载并 inline 显示）
    img_match = _MD_IMAGE_RE.search(content)
    image_url: Optional[str] = img_match.group(1) if img_match else None

    # 把 markdown 图语法替换为 emoji + alt，body 文本更干净
    body_text = _MD_IMAGE_RE.sub(
        lambda m: f"📷 {(m.group(0).split('![',1)[1].split(']',1)[0] or 'image')}",
        content,
    )

    payload = {
        "title": title,
        "body": body_text,
        "group": "DaLeTou",
        "level": "active",  # active = 即时铃声 + banner（非 passive/timeSensitive/critical）
    }
    if image_url:
        payload["image"] = image_url

    url = f"{server.rstrip('/')}/{device_key}"
    try:
        if aes_key and aes_iv:
            # 端到端加密：把整个 payload JSON 用 AES-128-CBC 加密，POST ciphertext
            import json
            ciphertext = _bark_aes_encrypt(json.dumps(payload), aes_key, aes_iv)
            resp = requests.post(url, data={"ciphertext": ciphertext}, timeout=10)
        else:
            # 明文 JSON
            resp = requests.post(url, json=payload, timeout=10)

        ok = resp.status_code == 200 and resp.json().get("code") == 200
        if not ok:
            print(f"[notify] Bark 失败 ({resp.status_code}): {resp.text[:200]}")
        return ok
    except Exception as e:
        print(f"[notify] Bark 异常: {e}")
        return False


def notify(title: str, content: str) -> List[str]:
    """
    广播通知到所有已配置的通道

    @param title 标题
    @param content Markdown 正文
    @returns 成功的通道列表
    """
    channels: List[str] = []

    if key := os.environ.get("SERVERCHAN_SENDKEY"):
        if _send_serverchan(title, content, key):
            channels.append("ServerChan")

    if hook := os.environ.get("WEWORK_WEBHOOK"):
        if _send_wework(title, content, hook):
            channels.append("WeWork")

    if token := os.environ.get("PUSHPLUS_TOKEN"):
        if _send_pushplus(title, content, token):
            channels.append("PushPlus")

    if topic := os.environ.get("NTFY_TOPIC"):
        server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
        if _send_ntfy(title, content, topic, server):
            channels.append("ntfy")

    if bk := os.environ.get("BARK_KEY"):
        bk_aes_k = os.environ.get("BARK_AES_KEY")
        bk_aes_iv = os.environ.get("BARK_AES_IV")
        bk_server = os.environ.get("BARK_SERVER", "https://api.day.app")
        if _send_bark(title, content, bk, bk_aes_k, bk_aes_iv, bk_server):
            channels.append("Bark")

    if channels:
        print(f"[notify] 已推送到: {', '.join(channels)}")
    else:
        print("[notify] 未配置通道或推送失败，跳过")

    return channels
