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
#             (从 config.json 加载)
# ==========================================

CONFIG_FILE = os.environ.get("MONITOR_CONFIG_FILE", "config.json")


def load_config(path=CONFIG_FILE):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_config()
BUILD_NUMBER_CHECK = CONFIG.get("build_number_check", {})
SUFFIX_CONFIG = CONFIG.get("suffix_config", {})

APP_STORE_LIST = CONFIG.get("app_store_list", [])
GOOGLE_PLAY_LIST = CONFIG.get("google_play_list", [])
TAPTAP_LIST = CONFIG.get("taptap_list", [])
GITHUB_REPO_LIST = CONFIG.get("github_repo_list", [])
RSS_LIST = CONFIG.get("rss_list", [])

BUILD_NUMBER_CHECK_GLOBAL = BUILD_NUMBER_CHECK.get("global", {})
BUILD_NUMBER_CHECK_GROUPS = BUILD_NUMBER_CHECK.get("groups", {})
BUILD_NUMBER_CHECK_APPS = BUILD_NUMBER_CHECK.get("apps", {})

RSS_REGEX_RULES = CONFIG.get("rss_regex_rules", {})

SUFFIX_CONFIG_GLOBAL = SUFFIX_CONFIG.get("global", {})
SUFFIX_CONFIG_GROUPS = SUFFIX_CONFIG.get("groups", {})
SUFFIX_CONFIG_APPS = SUFFIX_CONFIG.get("apps", {})

NOTIFICATION_GROUPS = CONFIG.get("notification_groups", {})
DEFAULT_GROUP = CONFIG.get("default_group", "其他更新")
NOTIFICATION_ICONS = CONFIG.get("notification_icons", {})
DEFAULT_ICON = CONFIG.get("default_icon")
RICH_MEDIA_CONFIG = CONFIG.get("rich_media_config", {})
BARK_ARCHIVE_MAPPING = CONFIG.get("bark_archive_mapping", {})

# Bark Key 继续来自 Repository Secrets，不写入 config.json。
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
