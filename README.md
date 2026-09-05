# 由米由家 · 抖音新作品监控（云端版）

电脑关机也照常监控博主「由米由家」是否发新作品，一旦发现立即推送到你的**微信**。
彻底脱离 WorkBuddy 本地调度器，跑在云端（GitHub Actions / Serverless / VPS 均可）。

---

## 一、它怎么工作

1. 定时（每 5~10 分钟）检查「由米由家」主页有没有新作品；
2. 发现新作品 → 通过 **Server酱** 推一条消息到你的微信（PushPlus / Bark 为备用通道）；
3. 你把状态记录（state.json）存回仓库，避免同一条重复提醒。

> 推送本身走云端（Server酱），所以你**电脑关机也照常收微信提醒**。

---

## 二、部署前先准备 3 样东西（都是密钥/配置，不会泄露）

| 名称 | 怎么拿 | 填到哪里 |
|------|--------|----------|
| `sec_user_id` | 打开博主主页 `https://www.douyin.com/user/xxxx`，`user/` 后面那串就是 | Secrets |
| `DOUYIN_COOKIE` | 电脑浏览器登录抖音后，F12 → Network → 随便点个请求 → 复制 Request Headers 里的 `Cookie` 整段 | Secrets |
| `SERVERCHAN_KEY` | 打开 sct.ftqq.com 用微信扫码登录，拿到 `SCTxxxx` 开头的 Key | Secrets |

> ⚠️ 抖音 Cookie 会过期（通常几周）。过期后监控会报「空响应/风控」，届时重新复制一次 Cookie 更新 Secrets 即可。

---

## 三、方式一：GitHub Actions（推荐·免费·真正纯云端）

> 免费额度：仓库设为「公开」→ Actions 无限时长，可每 5 分钟；设为「私有」→ 每月 2000 分钟，建议用每 10~15 分钟（本仓库默认 `*/10`）。代码不含任何密钥，密钥只存在仓库 Secrets 里，公开也安全。

1. 注册一个 GitHub 账号（https://github.com ，免费）。
2. 新建一个仓库（名字随意，如 `douyin-monitor`），**勾选 Public**（或 Private 也行，只是检查频率要调低）。
3. 把本目录全部文件上传到仓库：
   - `watcher.py`、`abogus.py`、`requirements.txt`、`config.example.json`
   - `.github/workflows/monitor.yml`（连同文件夹一起传）
   - 不需要传 `config.json`（云端用 Secrets，不读文件）。
4. 进入仓库 **Settings → Secrets and variables → Actions → New repository secret**，逐个添加：
   - `DOUYIN_SEC_USER_ID` = 第一步拿到的 sec_user_id
   - `DOUYIN_COOKIE` = 第二步拿到的 Cookie
   - `SERVERCHAN_KEY` = 第三步拿到的 SCT Key
   - （可选）`PUSHPLUS_TOKEN`、`BARK_KEY`、`CREATOR_NAME`
5. 进入仓库 **Actions** 标签页，左侧看到 `douyin-monitor`，点 **Enable workflow** 启用。
6. 点 **Run workflow** 手动跑一次测试。看 Logs：
   - 显示「首次运行，已记录 N 条现有作品」→ 正常，已开始监控（不会马上推，因为先播种）；
   - 博主一发新作品，微信立刻收到 Server酱 推送。

之后完全自动：你电脑关机、合上盖都行，云端每 10 分钟查一次，有更新就推微信。

---

## 四、方式二：VPS / 轻量云 / 树莓派 / 旧手机（常驻循环）

适合有一台一直开着的设备，或想要更密的检查频率。

```bash
pip install -r requirements.txt
# 把配置写进 config.json（参考 config.example.json，填入真实 sec_user_id / cookie / serverchan_key）
python watcher.py --loop     # 循环常驻，每 5~8 分钟查一次
```

想开机自启：用 `nohup python watcher.py --loop &` 或写成 systemd / supervisor 服务。

---

## 五、本地自测（不开云端也能验证脚本没问题）

```bash
pip install -r requirements.txt
# 准备好 config.json（含真实 sec_user_id / cookie / serverchan_key）
python watcher.py --test     # 只抓取、打印最新作品，不推送
```

---

## 六、常见问题

- **收不到微信推送？** 先确认 Server酱 Key 填对、微信已关注「Server酱」服务号；看 Actions Logs 里 `Server酱 返回` 那行。
- **报 Cookie 失效 / 空响应 / verify？** Cookie 过期或被风控，重新复制浏览器 Cookie 更新 Secrets（或 config.json）。
- **改检查频率？** 编辑 `.github/workflows/monitor.yml` 里的 `cron`（公开库 `*/5`，私有库建议 `*/15` 省额度）。
- **不想推了？** 在 Actions 里 Disable workflow，或删掉仓库即可，与你电脑无关。
