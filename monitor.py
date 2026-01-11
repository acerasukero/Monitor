import requests
import json
import os
import time
import re
import concurrent.futures
import feedparser
from google_play_scraper import app as play_app
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================
#             第一部分：用户配置区
#              (仅需要修改此部分)
# ==========================================

# 1. App Store 监控列表
# 格式：("应用名称", "AppID", "地区代码")
# 【重要】名称必须与其他平台列表完全一致，才能进行多端比对
APP_STORE_LIST = [
    ("币安 HK", "1436799971", "hk"),
    ("PokePay HK", "6741506101", "hk"),
    ("PixEz TW", "1494435126", "tw"),
    ("Bybit US", "1488296980", "us"),
    ("Bitget Wallet US", "1395301115", "us"),
    ("Loon US", "1373567447", "us"),
    ("Telegram US", "686449807", "us"),
    ("Fate/Grand Order JP", "1015521325", "jp"),
    ("BanG Dream JP", "1195834442", "jp"),
    ("Project Sekai JP", "1489932710", "jp"),
    ("IDOLY PRIDE JP", "1535925293", "jp"),
    ("NIKKE KR", "1585915174", "kr")
]

# 2. Google Play 监控列表
# 格式：("应用名称", "包名", "地区代码")
GOOGLE_PLAY_LIST = [
    ("Fate/Grand Order JP", "com.aniplex.fategrandorder", "jp"),
    ("Fate/Grand Order TW", "com.xiaomeng.fategrandorder", "tw"),
    ("Fate/Grand Order US", "com.aniplex.fategrandorder.en", "us"),
    ("碧蓝航线 JP", "com.YoStarJP.AzurLane", "jp"),
    ("碧蓝航线 US", "com.YoStarEN.AzurLane", "us"),
    ("碧蓝航线 TW", "com.hkmanjuu.azurlane.gp", "tw")
]

# 3. TapTap 监控列表
# 格式：("应用名称", "TapTapID")
TAPTAP_LIST = [
    ("Fate/Grand Order CN", "12982"),
    ("碧蓝航线 CN", "31597")
]

# 4. GitHub 监控列表
# 格式：("项目名称", "用户名/仓库名")
GITHUB_REPO_LIST = [
     ("JMBQ悬浮窗", "JMBQ/azurlane")
]

# 5. RSS订阅 监控列表
# 格式：("动画名", "RSS订阅链接")
RSS_LIST = [
    ("能帮我弄干净吗？", "https://mikanani.me/RSS/Bangumi?bangumiId=3826"),
    ("达尔文事变", "https://mikanani.me/RSS/Bangumi?bangumiId=3852"),
    ("非人学生与厌世教师", "https://mikanani.me/RSS/Bangumi?bangumiId=3845")
]

# 6. 构建号比对配置
# 控制是否对比版本号括号内的内容 (通常为构建号)
# 优先级：应用级(APPS) > 分组级(GROUPS) > 全局默认(GLOBAL)
# True  = 开启比对 (例如：1.0(1) 与 1.0(2) 视为不同版本，触发更新)
# False = 关闭比对 (例如：1.0(1) 与 1.0(2) 视为相同版本，忽略更新)
BUILD_NUMBER_CHECK_GLOBAL = {
    "App Store": False,
    "Google Play": False,
    "TapTap": False,
    "GitHub": False,
    "RSS": True
}

BUILD_NUMBER_CHECK_GROUPS = {
    "GitHub项目更新": { "GitHub": False }
}

BUILD_NUMBER_CHECK_APPS = {
    "NIKKE KR": { "App Store": True }
}

# 7. RSS订阅正则匹配规则
# 格式：{"动画名": r"正则表达式"}
# 未配置时默认匹配全部内容。建议配置以精确匹配所需内容
RSS_REGEX_RULES = {
    "能帮我弄干净吗？": r"六四位元字幕组.*1080",
    "达尔文事变": r"LoliHouse.*1080",
    "非人学生与厌世教师": r"黒ネズミたち.*CR.*1080"
}

# 8. 后缀显示配置
# 优先级：应用级(APPS) > 分组级(GROUPS) > 全局默认(GLOBAL)
# 格式：{"平台标识": ["显示文本", 是否显示]}

# (1) 全局默认配置
SUFFIX_CONFIG_GLOBAL = {
    "App Store": ["iOS", True],
    "Google Play": ["Android", True],
    "TapTap": ["Android", True],
    "GitHub": ["GitHub", True],
    "RSS": ["RSS", False]
}

# (2) 分组级配置
# 这里的 Key 必须与推送分组中的分组名称一致
SUFFIX_CONFIG_GROUPS = {
    "应用更新": {
        "App Store": ["iOS", False]
    },
    "GitHub项目更新": {
        "GitHub": ["GitHub", False]
    }
}
# (3) 应用级配置
# 这里的 Key 必须与各平台监控列表中的应用名称一致
SUFFIX_CONFIG_APPS = {
    "Loon US": {
        "iOS": ["iOS", False]
    }
}

# 9. 推送分组与排序配置
# 在 Bark 推送中，将按照下列配置进行分组推送
# 每个分组将作为单独的通知发送，组内应用按列表顺序排列
NOTIFICATION_GROUPS = {
    "游戏更新": [
        "碧蓝航线 CN",
        "碧蓝航线 JP",
        "碧蓝航线 US",
        "碧蓝航线 TW",
        "Fate/Grand Order JP",
        "Fate/Grand Order CN",
        "Fate/Grand Order TW",
        "Fate/Grand Order US",
        "BanG Dream JP",
        "Project Sekai JP",
        "IDOLY PRIDE JP",
        "NIKKE KR"
    ],
    "应用更新": [
        "币安 HK",
        "PokePay HK",
        "PixEz TW",
        "Bybit US",
        "Bitget Wallet US",   
        "Loon US",
        "Telegram US"   
    ],
    "GitHub项目更新": [
        "JMBQ悬浮窗"
    ],
    "动画更新": [
        "能帮我弄干净吗？",
        "达尔文事变",
        "非人学生与厌世教师"
    ]
}

DEFAULT_GROUP = "其他更新"

# 10. 推送图标配置
NOTIFICATION_ICONS = {
    "游戏更新": "https://shared.fastly.steamstatic.com/community_assets/images/items/2855140/4fd8a06b61d271c4eb71c85df79268429de46d63.gif",
    "应用更新": "https://shared.fastly.steamstatic.com/community_assets/images/items/2861690/c6de335c0a6737e5105eef701af2d3284ab513c4.gif",
    "GitHub项目更新": "https://shared.fastly.steamstatic.com/community_assets/images/items/2861700/db894084fbca19c3dd051cce144af2ad076f7273.gif",
    "动画更新": "https://shared.fastly.steamstatic.com/community_assets/images/items/2861720/0f9367f89fad6b92c96b686442d61bcb86d627f5.gif"
}

DEFAULT_ICON = "https://shared.fastly.steamstatic.com/community_assets/images/items/2861720/eca5871ca45838af8c953be846ab495d198dad19.png"

# 11. 富媒体通知图片配置
# 逻辑：
# 1. 单应用更新：直接显示该应用的图片。
# 2. 多应用更新：只有当本次更新的所有应用均配置完全相同的图片URL时，才会显示。
RICH_MEDIA_CONFIG = {
    "碧蓝航线 CN": "https://261213.xyz/AzurLane.png",
    "碧蓝航线 JP": "https://261213.xyz/AzurLane.png", 
    "碧蓝航线 US": "https://261213.xyz/AzurLane.png",
    "碧蓝航线 TW": "https://261213.xyz/AzurLane.png",
    "Fate/Grand Order JP": "https://261213.xyz/FateGrandOrder.png",
    "Fate/Grand Order CN": "https://261213.xyz/FateGrandOrder.png",
    "Fate/Grand Order TW": "https://261213.xyz/FateGrandOrder.png",
    "Fate/Grand Order US": "https://261213.xyz/FateGrandOrder.png",
    "BanG Dream JP": "https://261213.xyz/BanGDream.png",
    "IDOLY PRIDE JP": "https://261213.xyz/IDOLYPRIDE.png",
    "Project Sekai JP": "https://261213.xyz/ProjectSekai.png",
    "NIKKE KR": "https://261213.xyz/NIKKE.png",
    "币安 HK": "https://261213.xyz/Binance.png",
    "PokePay HK": "https://261213.xyz/PokePay.png",
    "PixEz TW": "https://261213.xyz/PixEz.png",
    "Bybit US": "https://261213.xyz/Bybit.png",
    "Bitget Wallet US": "https://261213.xyz/Bitget%20Wallet.png",
    "Loon US": "https://261213.xyz/Loon.png",
    "Telegram US": "https://261213.xyz/Telegram.png",
    "JMBQ悬浮窗": "https://261213.xyz/JMBQ.PNG",
    "能帮我弄干净吗？": "https://image.tmdb.org/t/p/w1280/1pRDbev2ITZCqHgow2pDvj4AEBP.jpg",
    "达尔文事变": "https://image.tmdb.org/t/p/w1280/frh8aDHSjbnyHv2YU5fJBThEhK3.jpg",
    "非人学生与厌世教师": "https://image.tmdb.org/t/p/w1280/1GmD3pP3aCQAknNem6yaQ5gP5os.jpg"
}

# 12. Bark 历史消息归档配置
# 用于将推送分组映射为 Bark App 内的历史记录归档组名
# 格式：{"推送分组名称": "Bark历史消息归档名称"}
BARK_ARCHIVE_MAPPING = {
    "游戏更新": "🎮 Game",
    "应用更新": "📱 App",
    "GitHub项目更新": "🛠️ GitHub",
    "动画更新": "📺 Anime",
    DEFAULT_GROUP: "🔔 Other"
}

# 13. Bark Key
# 请在 Repository Secrets 中配置密钥，无需在此处填写
BARK_KEY = os.environ.get("BARK_KEY")

# ==========================================
#             第二部分：功能函数区
# ==========================================

def get_retry_session(retries=3, backoff_factor=0.5):
    session = requests.Session()
    session.headers.update({
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8"
    })
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504, 520, 521, 522, 524],
        allowed_methods=["HEAD", "GET", "OPTIONS", "TRACE"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def worker_appstore(item):
    name, app_id = item[0], item[1]
    country = item[2] if len(item) > 2 else "cn"
    return item, get_appstore_version(app_id, country)

def worker_googleplay(item):
    name, pkg_name = item[0], item[1]
    country = item[2] if len(item) > 2 else "us"
    return item, get_googleplay_version(pkg_name, country)

def worker_taptap(item):
    name, app_id = item[0], item[1]
    return item, get_taptap_version(app_id)

def worker_github(item):
    name, repo = item[0], item[1]
    return item, get_github_version(repo)

def worker_rss(item):
    name, rss_url = item[0], item[1]
    regex_pattern = RSS_REGEX_RULES.get(name, None)
    return item, get_rss_latest(rss_url, regex_pattern)

def fetch_parallel(data_list, worker_func, max_workers=5):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {executor.submit(worker_func, item): item for item in data_list}
        for future in concurrent.futures.as_completed(future_to_item):
            try:
                results.append(future.result())
            except Exception as e:
                print(f"⚠️ 线程异常: {e}")
    return results

def get_check_config(app_name, platform):
    if app_name in BUILD_NUMBER_CHECK_APPS:
        if platform in BUILD_NUMBER_CHECK_APPS[app_name]:
            return BUILD_NUMBER_CHECK_APPS[app_name][platform]
    found_group = None
    for g_name, g_apps in NOTIFICATION_GROUPS.items():
        if app_name in g_apps:
            found_group = g_name
            break
    if found_group and found_group in BUILD_NUMBER_CHECK_GROUPS:
        if platform in BUILD_NUMBER_CHECK_GROUPS[found_group]:
            return BUILD_NUMBER_CHECK_GROUPS[found_group][platform]
    return BUILD_NUMBER_CHECK_GLOBAL.get(platform, False)

def clean_version_display(version, should_keep_build_num):
    if not version: return version
    version = str(version)
    if not should_keep_build_num:
        version = re.sub(r'\s*\(.*?\)', '', version)
    return version.strip()

def validate_update(new_raw, history_data, app_name, platform):
    if not history_data:
        return True
    if isinstance(history_data, str):
        latest_ver = history_data
        prev_ver = None
    else:
        latest_ver = history_data.get("latest")
        prev_ver = history_data.get("prev")
    should_check = get_check_config(app_name, platform)
    def clean(v):
        if not v: return ""
        s = str(v)
        if not should_check:
            s = re.sub(r'\s*\(.*?\)', '', s)
        return s.strip()
    v_new = clean(new_raw)
    v_last = clean(latest_ver)
    v_prev = clean(prev_ver)
    if not v_new or "varies" in v_new.lower():
        return False
    if v_new == v_last:
        return False
    if v_new == v_prev:
        print(f"🛡️ [防回滚] {app_name}: 检测到上一版本 {v_new}，判定为缓存回滚")
        return False
    return True

def process_check_result(name, key, fetched_ver, platform, history, new_history, current_state, update_buffer):
    if not fetched_ver:
        print(f"[{name}] ({platform}) 获取失败")
        if key in history: new_history[key] = history[key]
        return
    raw_data = history.get(key)
    if isinstance(raw_data, str):
        saved_data = {"latest": raw_data, "prev": None}
    else:
        saved_data = raw_data if raw_data else {"latest": None, "prev": None}
    saved_latest = saved_data.get("latest")
    current_state[name][platform] = fetched_ver or saved_latest
    display_log_ver = fetched_ver
    if platform == "RSS" and len(display_log_ver) > 30:
        display_log_ver = display_log_ver[:30] + "..."
    print(f"[{name}] ({platform}) 网络: {display_log_ver} | 本地: {saved_latest}")
    if validate_update(fetched_ver, saved_data, name, platform):
        if name not in update_buffer: update_buffer[name] = []
        update_buffer[name].append(platform)
        new_history[key] = {
            "latest": fetched_ver,
            "prev": saved_latest
        }
    else:
        new_history[key] = saved_data

def get_appstore_version(app_id, country="cn"):
    try:
        timestamp = int(time.time())
        url = f"https://itunes.apple.com/{country}/lookup?id={app_id}&t={timestamp}"
        session = get_retry_session()
        resp = session.get(url, timeout=10).json()
        if resp["resultCount"] > 0:
            return resp["results"][0]["version"]
    except Exception as e:
        print(f"❌ [App Store Error] ID {app_id}: {e}")
    return None

def get_googleplay_version(pkg_name, country="us"):
    try:
        result = play_app(pkg_name, lang='en', country=country)
        return result.get('version')
    except Exception as e:
        print(f"❌ [Google Play Error] {pkg_name}: {e}")
    return None

def get_taptap_version(app_id):
    try:
        timestamp = int(time.time())
        url = f"https://www.taptap.cn/app/{app_id}?_={timestamp}"
        session = get_retry_session()
        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        regex_match = re.search(r'"softwareVersion"\s*:\s*"([^"]+)"', resp.text)
        if regex_match:
            return regex_match.group(1)
        return None
    except Exception as e:
        print(f"❌ [TapTap Error] ID {app_id}: {e}")
    return None

def get_github_version(repo_path):
    try:
        timestamp = int(time.time())
        url = f"https://api.github.com/repos/{repo_path}/releases/latest?t={timestamp}"
        session = get_retry_session()
        resp = session.get(url, timeout=10)
        if resp.status_code == 404:
             url = f"https://api.github.com/repos/{repo_path}/tags?t={timestamp}"
             resp = session.get(url, timeout=10)
             data = resp.json()
             if data: return data[0]["name"]
        else:
            data = resp.json()
            if "tag_name" in data: return data["tag_name"]
    except Exception as e:
        print(f"❌ [GitHub Error] Repo {repo_path}: {e}")
    return None

def get_rss_latest(rss_url, regex_pattern=None):
    try:
        feed = feedparser.parse(rss_url)
        if not feed.entries:
            print(f"⚠️ [RSS Warning] 解析成功但无条目: {rss_url}")
            return None
        print(f"🔍 [调试] 正在扫描 {len(feed.entries)} 个条目...")
        for entry in feed.entries:
            title = entry.title.strip()
            print(f"-> 扫描标题: {title}")
            if not regex_pattern:
                return title
            if re.search(regex_pattern, title, re.IGNORECASE):
                print(f"✅ 匹配成功！")
                return title
        print(f"⚠️ [匹配失败] 扫描结束，未找到符合正则的资源")
        return None
    except Exception as e:
        print(f"❌ [RSS Error] URL {rss_url}: {e}")
    return None

def send_bark_notification(title, content, group_name=None, icon_url=None, image_url=None):
    if not BARK_KEY:
        print("错误：未检测到 BARK_KEY")
        return
    if not group_name: group_name = title
    if not icon_url: icon_url = DEFAULT_ICON
    print(f"🚀 准备推送 -> {title} (归档: {group_name})")
    url = f"https://api.day.app/{BARK_KEY}"
    payload = {
        "title": title,
        "body": content,
        "icon": icon_url,
        "group": group_name
    }
    if image_url:
        payload["image"] = image_url        
    try:
        session = get_retry_session()
        resp = session.post(url, data=payload, timeout=10)
        print(f"📨 推送回执: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ 推送网络错误: {e}")

# ==========================================
#             第三部分：主程序运行区
# ==========================================

if __name__ == "__main__":
    HISTORY_FILE = "version_history.json"
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                print("⚠️ 警告：历史记录文件损坏或为空文件，已重置。")
                history = {}
    else:
        history = {}

    update_buffer = {}
    current_state = {}
    new_history = history.copy()

    print("\n>>> 开始检查 App Store (并行)...")
    results = fetch_parallel(APP_STORE_LIST, worker_appstore, max_workers=5)
    for item, fetched_ver in results:
        name, app_id = item[0], item[1]
        country = item[2] if len(item) > 2 else "cn"
        key = f"app_{app_id}_{country}"
        if name not in current_state: current_state[name] = {}
        process_check_result(name, key, fetched_ver, "App Store", history, new_history, current_state, update_buffer)

    print("\n>>> 开始检查 Google Play (并行)...")
    results = fetch_parallel(GOOGLE_PLAY_LIST, worker_googleplay, max_workers=5)
    for item, fetched_ver in results:
        name, pkg_name = item[0], item[1]
        country = item[2] if len(item) > 2 else "us"
        key = f"gp_{pkg_name}_{country}"
        if name not in current_state: current_state[name] = {}
        process_check_result(name, key, fetched_ver, "Google Play", history, new_history, current_state, update_buffer)

    print("\n>>> 开始检查 TapTap (并行)...")
    results = fetch_parallel(TAPTAP_LIST, worker_taptap, max_workers=5)
    for item, fetched_ver in results:
        name, app_id = item[0], item[1]
        key = f"taptap_{app_id}"
        if name not in current_state: current_state[name] = {}
        process_check_result(name, key, fetched_ver, "TapTap", history, new_history, current_state, update_buffer)

    print("\n>>> 开始检查 GitHub (并行)...")
    results = fetch_parallel(GITHUB_REPO_LIST, worker_github, max_workers=5)
    for item, fetched_ver in results:
        name, repo = item[0], item[1]
        key = f"gh_{repo}"
        if name not in current_state: current_state[name] = {}
        process_check_result(name, key, fetched_ver, "GitHub", history, new_history, current_state, update_buffer)

    print("\n>>> 开始检查 RSS 订阅 (并行)...")
    results = fetch_parallel(RSS_LIST, worker_rss, max_workers=5)
    for item, fetched_ver in results:
        name, rss_url = item[0], item[1]
        key = f"rss_{name}"
        if name not in current_state: current_state[name] = {}
        process_check_result(name, key, fetched_ver, "RSS", history, new_history, current_state, update_buffer)

    if update_buffer:
        print("\n>>> 检测到更新，准备推送...")
        with open(HISTORY_FILE, "w") as f:
            json.dump(new_history, f, indent=2)
        
        def format_msg_line(app_name, platform, version):
            if platform == "RSS":
                return app_name
            
            should_check = get_check_config(app_name, platform)
            display_ver = clean_version_display(version, should_check)
            final_conf = None
            if app_name in SUFFIX_CONFIG_APPS:
                if platform in SUFFIX_CONFIG_APPS[app_name]:
                    final_conf = SUFFIX_CONFIG_APPS[app_name][platform]
            if final_conf is None:
                found_group = None
                for g_name, g_apps in NOTIFICATION_GROUPS.items():
                    if app_name in g_apps:
                        found_group = g_name
                        break
                if found_group and found_group in SUFFIX_CONFIG_GROUPS:
                    if platform in SUFFIX_CONFIG_GROUPS[found_group]:
                        final_conf = SUFFIX_CONFIG_GROUPS[found_group][platform]
            if final_conf is None:
                final_conf = SUFFIX_CONFIG_GLOBAL.get(platform, [platform, True])
            suffix_text = final_conf[0]
            is_visible = final_conf[1]
            if is_visible:
                return f"{app_name} ({suffix_text}): {display_ver}"
            else:
                return f"{app_name}: {display_ver}"

        def get_msg_lines(name):
            lines = []
            platforms_updated = update_buffer[name]
            app_ver_info = current_state.get(name, {})
            for plat in ["App Store", "Google Play", "TapTap", "GitHub", "RSS"]:
                if plat in platforms_updated:
                    plat_ver = app_ver_info.get(plat)
                    lines.append(format_msg_line(name, plat, plat_ver))
            return list(dict.fromkeys(lines))

        processed_apps = set()
        for group_title, group_apps in NOTIFICATION_GROUPS.items():
            group_msg_lines = []
            updated_apps_in_this_group = []
            for name in group_apps:
                if name in update_buffer:
                    processed_apps.add(name)
                    updated_apps_in_this_group.append(name)
                    group_msg_lines.extend(get_msg_lines(name))
            if group_msg_lines:
                archive_name = BARK_ARCHIVE_MAPPING.get(group_title, group_title)
                group_icon = NOTIFICATION_ICONS.get(group_title, DEFAULT_ICON)
                rich_image = None
                config_source = globals().get("RICH_MEDIA_CONFIG", {})
                img_urls = [config_source.get(app) for app in updated_apps_in_this_group]
                if img_urls and (None not in img_urls) and (len(set(img_urls)) == 1):
                    rich_image = img_urls[0]
                    print(f"🖼️ [富媒体] {group_title}: 判定成功 -> 显示图片")
                send_bark_notification(group_title, "\n".join(group_msg_lines), group_name=archive_name, icon_url=group_icon, image_url=rich_image)
                time.sleep(1)

        leftover_msg_lines = []
        leftover_apps_list = []
        for name in update_buffer:
            if name not in processed_apps:
                leftover_apps_list.append(name)
                leftover_msg_lines.extend(get_msg_lines(name))
        if leftover_msg_lines:
            archive_name = BARK_ARCHIVE_MAPPING.get(DEFAULT_GROUP, DEFAULT_GROUP)
            other_icon = NOTIFICATION_ICONS.get(DEFAULT_GROUP, DEFAULT_ICON)
            rich_image = None
            config_source = globals().get("RICH_MEDIA_CONFIG", {})
            img_urls = [config_source.get(app) for app in leftover_apps_list]
            if img_urls and (None not in img_urls) and (len(set(img_urls)) == 1):
                rich_image = img_urls[0]
                print(f"🖼️ [富媒体] {DEFAULT_GROUP}: 判定成功 -> 显示图片")
            send_bark_notification(DEFAULT_GROUP, "\n".join(leftover_msg_lines), group_name=archive_name, icon_url=other_icon, image_url=rich_image)
    else:
        print("\n>>> 未检测到更新。")
