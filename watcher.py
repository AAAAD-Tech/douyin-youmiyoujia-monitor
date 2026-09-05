# -*- coding: utf-8 -*-
"""
由米由家 · 抖音新作品监控器（云端就绪版）
========================================
功能：定时检查指定抖音博主是否发了新作品，一旦发现立即通过 Server酱 / PushPlus / Bark 推送到微信。
设计目标：彻底脱离 WorkBuddy 本地调度器，可跑在任意云端（GitHub Actions / Serverless / VPS / 轻量云）。

配置来源（优先级：环境变量 > config.json）：
  DOUYIN_SEC_USER_ID   博主主页 user/ 后面那串
  DOUYIN_COOKIE         浏览器复制的抖音登录 Cookie（会过期，需定期更新）
  SERVERCHAN_KEY        Server酱 SCT Key（主通道，推微信）
  PUSHPLUS_TOKEN        PushPlus token（备用通道，可选）
  BARK_KEY              Bark key（iPhone 备用通道，可选）
  CREATOR_NAME          博主昵称（展示用，默认 由米由家）
  CHECK_COUNT           抓取条数（默认 20）
  STATE_FILE            状态文件名（默认 state.json）
  LOG_FILE              日志文件名（默认 watcher.log）
  MIN_INTERVAL_MIN / MAX_INTERVAL_MIN  循环模式下的检查间隔随机范围（默认 5~8 分钟）
  MAX_JITTER_SEC        请求前随机延迟上限（默认 120 秒，降低被风控概率）

运行模式：
  单次（默认，配合定时器/CI 每调用一次查一次）：
      python watcher.py
  循环常驻（配合 VPS / Render 等一直开着的主机）：
      python watcher.py --loop
  仅测试抓取（不发提醒）：
      python watcher.py --test

依赖：requests, gmssl, pycryptodomex（见 requirements.txt）；abogus.py 与本脚本同目录。
"""
import os
import sys
import json
import time
import random
import logging
import subprocess
import base64
from urllib.parse import urlencode, quote

# ---------- 把本地依赖目录加入搜索路径 ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, ".pylibs")
for p in (LIB_DIR, SCRIPT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import requests  # noqa: E402
from abogus import ABogus  # noqa: E402

# 抖音网页接口要求的固定 UA（需与 a_bogus 算法内部一致）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")

# 环境变量 -> config.json 字段 映射
ENV_MAP = {
    "sec_user_id": "DOUYIN_SEC_USER_ID",
    "cookie": "DOUYIN_COOKIE",
    "serverchan_key": "SERVERCHAN_KEY",
    "pushplus_token": "PUSHPLUS_TOKEN",
    "bark_key": "BARK_KEY",
    "creator_name": "CREATOR_NAME",
    "count": "CHECK_COUNT",
    "state_file": "STATE_FILE",
    "log_file": "LOG_FILE",
    "min_interval_min": "MIN_INTERVAL_MIN",
    "max_interval_min": "MAX_INTERVAL_MIN",
    "max_jitter_sec": "MAX_JITTER_SEC",
}


def get_cfg():
    """配置：先读 config.json（本地/VPS 用），再用环境变量覆盖（CI/Serverless 用）。"""
    cfg = {}
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    for k, env in ENV_MAP.items():
        v = os.environ.get(env)
        if v:
            cfg[k] = v
    cfg.setdefault("creator_name", "由米由家")
    cfg.setdefault("count", 20)
    cfg.setdefault("state_file", "state.json")
    cfg.setdefault("log_file", "watcher.log")
    cfg.setdefault("enable_desktop_toast", False)
    cfg.setdefault("min_interval_min", 5)
    cfg.setdefault("max_interval_min", 8)
    cfg.setdefault("max_jitter_sec", 120)
    return cfg


# ---------- 日志 ----------
def setup_logging(log_file):
    logger = logging.getLogger("douyin_watcher")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(os.path.join(SCRIPT_DIR, log_file), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


log = logging.getLogger("douyin_watcher")


# ---------- 抓取最新作品 ----------
def fetch_latest_posts(sec_user_id, cookie, count=20):
    """返回作品列表（新->旧），每项含 aweme_id/desc/create_time/url"""
    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": "1",
        "version_code": "190500",
        "version_name": "19.5.0",
        "cookie_enabled": "true",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Firefox",
        "browser_online": "true",
        "engine_name": "Gecko",
        "os_name": "Windows",
        "os_version": "10",
        "platform": "PC",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_version": "124.0",
        "engine_version": "122.0.0.0",
        "cpu_core_num": "12",
        "device_memory": "8",
        "sec_user_id": sec_user_id,
        "max_cursor": "0",
        "count": str(count),
    }
    a_bogus = ABogus().get_value(params)
    a_bogus = quote(a_bogus, safe="")
    url = "https://www.douyin.com/aweme/v1/web/aweme/post/?" + urlencode(params) + "&a_bogus=" + a_bogus
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.douyin.com/",
        "Cookie": cookie,
    }
    resp = requests.get(url, headers=headers, timeout=15)
    if not resp.text or not resp.text.strip():
        raise RuntimeError("抖音返回空响应（可能 Cookie 失效 / 被风控，请在浏览器重新登录后更新 Cookie）")
    data = resp.json()
    if data.get("status_code", 0) != 0:
        raise RuntimeError("抖音返回错误 status_code=%s" % data.get("status_code"))
    if "verify" in data:
        raise RuntimeError("抖音触发风控验证（verify），Cookie 可能失效，请更新")
    aweme_list = data.get("aweme_list") or []
    posts = []
    for a in aweme_list:
        aid = a.get("aweme_id")
        if not aid:
            continue
        posts.append({
            "aweme_id": str(aid),
            "desc": (a.get("desc") or "").strip(),
            "create_time": int(a.get("create_time", 0)),
            "url": "https://www.douyin.com/video/%s" % aid,
            "digg": (a.get("statistics") or {}).get("digg_count", 0),
            "comment": (a.get("statistics") or {}).get("comment_count", 0),
        })
    return posts


# ---------- 抓取店铺商品数（监控上新） ----------
def fetch_shop_count(sec_user_id, cookie):
    """从博主主页的店铺卡片取当前商品数（用于监控店铺上新）。"""
    params = {
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "pc_client_type": "1", "version_code": "190500", "version_name": "19.5.0",
        "cookie_enabled": "true", "browser_language": "zh-CN", "browser_platform": "Win32",
        "browser_name": "Firefox", "browser_online": "true", "engine_name": "Gecko",
        "os_name": "Windows", "os_version": "10", "platform": "PC",
        "screen_width": "1920", "screen_height": "1080", "browser_version": "124.0",
        "engine_version": "122.0.0.0", "cpu_core_num": "12", "device_memory": "8",
        "sec_user_id": sec_user_id,
    }
    a_bogus = ABogus().get_value(params)
    a_bogus = quote(a_bogus, safe="")
    url = "https://www.douyin.com/aweme/v1/web/user/profile/other/?" + urlencode(params) + "&a_bogus=" + a_bogus
    headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/", "Cookie": cookie}
    resp = requests.get(url, headers=headers, timeout=15)
    if not resp.text.strip():
        raise RuntimeError("抖音返回空响应（Cookie 可能失效）")
    d = resp.json()
    if d.get("status_code", 0) != 0:
        raise RuntimeError("抖音返回错误 status_code=%s" % d.get("status_code"))
    u = d.get("user", {}) or {}
    for card in (u.get("card_entries") or []):
        if isinstance(card, str):
            try:
                card = json.loads(card)
            except Exception:
                continue
        if not isinstance(card, dict):
            continue
        cd = card.get("card_data") or {}
        if isinstance(cd, str):
            try:
                cd = json.loads(cd)
            except Exception:
                cd = {}
        if not isinstance(cd, dict):
            continue
        if cd.get("is_store") or cd.get("store_type") == "shop":
            try:
                return int(cd.get("product_count", 0))
            except Exception:
                return 0
    return 0


def check_shop(cfg, test=False, push_enabled=True):
    """监控博主店铺商品数变化（上新提醒）。完整商品名需店铺主私有接口，这里用商品数变化做可靠近似。"""
    sec_user_id = cfg["sec_user_id"]
    cookie = cfg["cookie"]
    name = cfg.get("creator_name", "该博主")
    shop_file = os.path.join(SCRIPT_DIR, "shop_state.json")
    try:
        sd = json.load(open(shop_file, encoding="utf-8"))
    except Exception:
        sd = {}
    known = int(sd.get("known_shop_count", 0) or 0)

    cnt = None
    last_err = None
    for attempt in range(3):
        try:
            cnt = fetch_shop_count(sec_user_id, cookie)
            if cnt:
                break
        except Exception as e:
            last_err = e
            log.warning("店铺计数抓取失败(第%d次)，3秒后重试: %s", attempt + 1, e)
            time.sleep(3)
    if not cnt:
        log.info("店铺计数获取失败，跳过本次店铺检查: %s", last_err)
        return

    if test:
        log.info("[TEST] 店铺当前商品数: %d", cnt)
        return

    if known == 0:
        with open(shop_file, "w", encoding="utf-8") as f:
            json.dump({"known_shop_count": cnt}, f, ensure_ascii=False, indent=2)
        log.info("店铺监控已播种，当前商品数 %d", cnt)
        return

    if cnt > known:
        title = "%s 的店铺上新了！" % name
        content = "【%s生态农业】抖音小店商品数 %d → %d，可能有新品上架。\n店铺：https://haohuo.jinritemai.com/views/shop/index?id=shccpkS" % (name, known, cnt)
        log.info("发现店铺上新：商品数 %d → %d", known, cnt)
        if push_enabled:
            push_serverchan(cfg.get("serverchan_key"), title, content)
            push_pushplus(cfg.get("pushplus_token"), title, title + "\n" + content)
            push_bark(cfg.get("bark_key"), title, content)
    else:
        log.info("店铺商品数无变化（%d）", cnt)
    with open(shop_file, "w", encoding="utf-8") as f:
        json.dump({"known_shop_count": cnt}, f, ensure_ascii=False, indent=2)


# ---------- 状态存储 ----------
def load_state(state_file):
    path = os.path.join(SCRIPT_DIR, state_file)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    return {
        "known_ids": set(data.get("known_ids", [])),
        "next_check_ts": float(data.get("next_check_ts", 0) or 0),
    }


def save_state(state_file, known_ids, next_check_ts):
    path = os.path.join(SCRIPT_DIR, state_file)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "known_ids": list(known_ids)[-300:],
            "next_check_ts": next_check_ts,
        }, f, ensure_ascii=False, indent=2)


# ---------- 提醒通道 ----------
def push_pushplus(token, title, content):
    if not token:
        return False
    try:
        r = requests.post("https://www.pushplus.plus/send", json={
            "token": token,
            "title": title,
            "content": content,
            "template": "html",
        }, timeout=15)
        log.info("PushPlus 返回: %s", r.text[:120])
        return r.json().get("code") == 200
    except Exception as e:
        log.error("PushPlus 推送失败: %s", e)
        return False


def push_bark(key, title, content):
    if not key:
        return False
    try:
        url = "https://api.day.app/%s/%s/%s" % (key, quote(title), quote(content))
        r = requests.get(url, timeout=15)
        log.info("Bark 返回: %s", r.text[:120])
        return r.status_code == 200
    except Exception as e:
        log.error("Bark 推送失败: %s", e)
        return False


def push_serverchan(key, title, content):
    if not key:
        return False
    try:
        r = requests.post("https://sctapi.ftqq.com/%s.send" % key,
                          data={"title": title, "desp": content}, timeout=15)
        log.info("Server酱 返回: %s", r.text[:120])
        return r.json().get("code") == 0
    except Exception as e:
        log.error("Server酱 推送失败: %s", e)
        return False


# ---------- 单次检查 ----------
def check_once(cfg, test=False, desktop=False, push_enabled=True):
    sec_user_id = cfg["sec_user_id"]
    cookie = cfg["cookie"]
    name = cfg.get("creator_name", "该博主")
    if not sec_user_id or sec_user_id.startswith("在此填写"):
        log.error("未配置 sec_user_id，请在环境变量或 config.json 填写博主主页 ID")
        return
    if not cookie or cookie.startswith("在此填写"):
        log.error("未配置 cookie，请在环境变量或 config.json 填写抖音登录 Cookie")
        return

    state_file = cfg.get("state_file", "state.json")
    count = int(cfg.get("count", 20))
    st = load_state(state_file)
    known = st["known_ids"]

    last_err = None
    posts = None
    for attempt in range(3):
        try:
            posts = fetch_latest_posts(sec_user_id, cookie, count)
            break
        except Exception as e:
            last_err = e
            log.warning("抓取失败(第%d次)，3秒后重试: %s", attempt + 1, e)
            time.sleep(3)
    if posts is None:
        log.error("抓取失败(已重试3次): %s", last_err)
        return

    if not posts:
        log.info("未获取到作品列表（可能 Cookie 失效或无作品）")
        return

    if test:
        log.info("[TEST] 抓到 %d 条作品，最新一条: %s", len(posts),
                 posts[0]["desc"][:30] if posts else "无")
        for p in posts[:3]:
            log.info("  - %s | %s", p["aweme_id"], p["desc"][:40])
        return

    if not known:
        # 首次运行：仅播种，不提醒
        save_state(state_file, set(p["aweme_id"] for p in posts), time.time())
        log.info("首次运行，已记录 %d 条现有作品，开始监控", len(posts))
        return

    new_posts = [p for p in posts if p["aweme_id"] not in known]
    if not new_posts:
        log.info("无新作品（现有 %d 条均已记录）", len(posts))
        save_state(state_file, known | set(p["aweme_id"] for p in posts), time.time())
        return

    # 按时间正序播报
    new_posts.sort(key=lambda x: x["create_time"])
    lines, text_lines = [], []
    first_desc = (new_posts[0]["desc"] or "(无文案)")
    first_desc = first_desc.replace("\r", " ").replace("\n", " ").strip()[:20]
    title = "%s 发新作品：%s" % (name, first_desc)
    for p in new_posts:
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(p["create_time"]))
        desc = p["desc"] or "(无文案)"
        lines.append(
            "<p>🎬 <b>%s</b><br>文案：%s<br>时间：%s<br>"
            "点赞：%s 评论：%s<br><a href='%s'>点击查看作品</a></p><hr>"
            % (name, desc, t, p["digg"], p["comment"], p["url"])
        )
        text_lines.append("文案：%s\n时间：%s\n点赞：%s 评论：%s\n链接：%s"
                          % (desc, t, p["digg"], p["comment"], p["url"]))
    html = "<h3>%s 发了 %d 条新作品！</h3>" % (name, len(new_posts)) + "".join(lines)
    text = title + "\n\n" + "\n\n".join(text_lines)

    log.info("发现 %d 条新作品，发送提醒", len(new_posts))
    if push_enabled:
        push_serverchan(cfg.get("serverchan_key"), title, text)
        push_pushplus(cfg.get("pushplus_token"), title, html)
        push_bark(cfg.get("bark_key"), title, new_posts[0]["desc"] or "(无文案)")
    if desktop:
        show_desktop_toast(title, (new_posts[0]["desc"] or "(无文案)")[:80])

    save_state(state_file, known | set(p["aweme_id"] for p in posts), time.time())


def show_desktop_toast(title, message):
    try:
        safe_title = title.replace("'", "''")
        safe_msg = message.replace("'", "''")
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] | Out-Null\n"
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
            "$x = [xml]$t.GetXml()\n"
            "$x.GetElementsByTagName('text')[0].AppendChild($x.CreateTextNode('%s')) | Out-Null\n"
            "$x.GetElementsByTagName('text')[1].AppendChild($x.CreateTextNode('%s')) | Out-Null\n"
            "$n = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('抖音更新监控')\n"
            "$n.Show([Windows.UI.Notifications.ToastNotification]::new($x))\n"
        ) % (safe_title, safe_msg)
        b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", b64],
                       capture_output=True, timeout=30)
        return True
    except Exception as e:
        log.error("桌面弹窗失败: %s", e)
        return False


# ---------- 定时器（CI / Serverless）模式 ----------
def main_cron(cfg, test=False):
    state_file = cfg.get("state_file", "state.json")
    st = load_state(state_file)
    lo = float(cfg.get("min_interval_min", 5))
    hi = float(cfg.get("max_interval_min", 8))
    max_jitter = int(cfg.get("max_jitter_sec", 120))

    if not test:
        now = time.time()
        if now < st["next_check_ts"]:
            log.info("距下次检查还有 %.0f 秒，本次跳过（随机节奏）", st["next_check_ts"] - now)
            return
        jitter = random.randint(0, max_jitter)
        if jitter:
            log.info("随机延迟 %d 秒后开始请求", jitter)
            time.sleep(jitter)
    check_once(cfg, test=test)
    check_shop(cfg, test=test, push_enabled=not test)
    # 安排下次检查：现在 + 随机间隔（5~8 分钟，越短越好且规避抖音风控）
    if not test:
        nxt = time.time() + random.uniform(lo, hi) * 60
        save_state(state_file, load_state(state_file)["known_ids"], nxt)
        log.info("已安排下次检查：%s", time.strftime("%H:%M:%S", time.localtime(nxt)))


# ---------- 循环常驻（VPS / Render）模式 ----------
def main_loop(cfg):
    lo = float(cfg.get("min_interval_min", 5))
    hi = float(cfg.get("max_interval_min", 8))
    log.info("进入循环常驻模式，每 %.0f~%.0f 分钟检查一次", lo, hi)
    while True:
        try:
            check_once(cfg)
        except Exception as e:
            log.error("循环异常: %s", e)
        time.sleep(random.uniform(lo, hi) * 60)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="循环常驻模式（VPS/Render 等一直开着的主机）")
    ap.add_argument("--test", action="store_true", help="仅测试抓取，不提醒")
    ap.add_argument("--no-push", action="store_true", help="只检查不推送（调试用）")
    args = ap.parse_args()

    cfg = get_cfg()
    setup_logging(cfg.get("log_file", "watcher.log"))

    if args.loop:
        main_loop(cfg)
    else:
        main_cron(cfg, test=args.test)


if __name__ == "__main__":
    main()
