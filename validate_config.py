import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


CONFIG_FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.json")
PLATFORMS = ("App Store", "Google Play", "TapTap", "GitHub", "RSS")
TOP_LEVEL_KEYS = {
    "app_store_list",
    "google_play_list",
    "taptap_list",
    "github_repo_list",
    "rss_list",
    "build_number_check",
    "rss_regex_rules",
    "suffix_config",
    "notification_groups",
    "default_group",
    "notification_icons",
    "default_icon",
    "rich_media_config",
    "bark_archive_mapping",
}

errors = []
warnings = []
all_names = set()
rss_names = set()
group_names = set()


def add_error(path, message):
    errors.append(f"{path}: {message}")


def add_warning(path, message):
    warnings.append(f"{path}: {message}")


def load_json(path):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    if not path.exists():
        add_error(str(path), "file not found")
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        add_error(str(path), f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    except ValueError as exc:
        add_error(str(path), str(exc))
    return {}


def is_non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def require_dict(path, value):
    if not isinstance(value, dict):
        add_error(path, "must be an object")
        return {}
    return value


def require_list(path, value):
    if not isinstance(value, list):
        add_error(path, "must be an array")
        return []
    return value


def check_url(path, value):
    if not is_non_empty_string(value):
        add_error(path, "must be a non-empty string")
        return
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        add_warning(path, "does not look like an HTTP URL")


def validate_monitor_list(config, key, platform, min_len, max_len, identity_indexes):
    items = require_list(key, config.get(key, []))
    seen_names = set()
    seen_identities = set()
    names = set()

    for index, item in enumerate(items):
        path = f"{key}[{index}]"
        if not isinstance(item, list):
            add_error(path, "must be an array")
            continue
        if len(item) < min_len or len(item) > max_len:
            add_error(path, f"must contain {min_len} to {max_len} fields")
            continue
        for field_index, field in enumerate(item):
            if not is_non_empty_string(field):
                add_error(f"{path}[{field_index}]", "must be a non-empty string")
        if not item or not is_non_empty_string(item[0]):
            continue

        name = item[0]
        names.add(name)
        all_names.add(name)

        if name in seen_names:
            add_error(f"{path}[0]", f"duplicate name {name!r} in {platform}")
        seen_names.add(name)

        identity = tuple(item[i] for i in identity_indexes if i < len(item))
        if identity in seen_identities:
            add_error(path, f"duplicate {platform} target {identity!r}")
        seen_identities.add(identity)

        if key in ("app_store_list", "google_play_list") and len(item) >= 3:
            if not re.fullmatch(r"[A-Za-z]{2}", item[2]):
                add_warning(f"{path}[2]", "country code is usually two letters")
        if key == "github_repo_list" and len(item) >= 2:
            if not re.fullmatch(r"[^/\s]+/[^/\s]+", item[1]):
                add_warning(f"{path}[1]", "repository should look like owner/repo")
        if key == "rss_list" and len(item) >= 2:
            rss_names.add(name)
            check_url(f"{path}[1]", item[1])

    return names


def validate_bool_map(path, value, known_outer=None, require_all_platforms=False):
    outer = require_dict(path, value)
    if require_all_platforms:
        for platform in PLATFORMS:
            if platform not in outer:
                add_error(f"{path}.{platform}", "missing platform default")
    for outer_key, inner in outer.items():
        if known_outer is not None and outer_key not in known_outer:
            add_warning(f"{path}.{outer_key}", "does not match a known name")
        if isinstance(inner, bool):
            continue
        nested = require_dict(f"{path}.{outer_key}", inner)
        for platform, enabled in nested.items():
            if platform not in PLATFORMS:
                add_warning(f"{path}.{outer_key}.{platform}", "unknown platform")
            if not isinstance(enabled, bool):
                add_error(f"{path}.{outer_key}.{platform}", "must be true or false")


def validate_suffix_value(path, value):
    if not isinstance(value, list) or len(value) != 2:
        add_error(path, "must be [text, visible]")
        return
    if not isinstance(value[0], str):
        add_error(f"{path}[0]", "must be a string")
    if not isinstance(value[1], bool):
        add_error(f"{path}[1]", "must be true or false")


def validate_suffix_map(path, value, known_outer=None, require_all_platforms=False):
    outer = require_dict(path, value)
    if require_all_platforms:
        for platform in PLATFORMS:
            if platform not in outer:
                add_error(f"{path}.{platform}", "missing platform default")
    for outer_key, inner in outer.items():
        if known_outer is not None and outer_key not in known_outer:
            add_warning(f"{path}.{outer_key}", "does not match a known name")
        if isinstance(inner, list):
            validate_suffix_value(f"{path}.{outer_key}", inner)
            continue
        nested = require_dict(f"{path}.{outer_key}", inner)
        for platform, suffix in nested.items():
            if platform not in PLATFORMS:
                add_warning(f"{path}.{outer_key}.{platform}", "unknown platform")
            validate_suffix_value(f"{path}.{outer_key}.{platform}", suffix)


def validate_config(config):
    config = require_dict("config", config)
    missing = TOP_LEVEL_KEYS - set(config)
    unknown = set(config) - TOP_LEVEL_KEYS

    for key in sorted(missing):
        add_error(key, "missing required key")
    for key in sorted(unknown):
        add_error(key, "unknown top-level key")

    validate_monitor_list(config, "app_store_list", "App Store", 2, 3, (1, 2))
    validate_monitor_list(config, "google_play_list", "Google Play", 2, 3, (1, 2))
    validate_monitor_list(config, "taptap_list", "TapTap", 2, 2, (1,))
    validate_monitor_list(config, "github_repo_list", "GitHub", 2, 2, (1,))
    validate_monitor_list(config, "rss_list", "RSS", 2, 2, (0,))

    groups = require_dict("notification_groups", config.get("notification_groups", {}))
    seen_grouped_apps = {}
    for group, names in groups.items():
        if not is_non_empty_string(group):
            add_error("notification_groups", "group name must be a non-empty string")
            continue
        group_names.add(group)
        items = require_list(f"notification_groups.{group}", names)
        local_seen = set()
        for index, name in enumerate(items):
            path = f"notification_groups.{group}[{index}]"
            if not is_non_empty_string(name):
                add_error(path, "must be a non-empty string")
                continue
            if name not in all_names:
                add_error(path, f"unknown monitored item {name!r}")
            if name in local_seen:
                add_error(path, f"duplicate item {name!r} in group")
            local_seen.add(name)
            if name in seen_grouped_apps:
                add_error(path, f"also appears in group {seen_grouped_apps[name]!r}")
            seen_grouped_apps[name] = group

    build_number_check = require_dict("build_number_check", config.get("build_number_check", {}))
    validate_bool_map("build_number_check.global", build_number_check.get("global", {}), require_all_platforms=True)
    validate_bool_map("build_number_check.groups", build_number_check.get("groups", {}), group_names)
    validate_bool_map("build_number_check.apps", build_number_check.get("apps", {}), all_names)

    regex_rules = require_dict("rss_regex_rules", config.get("rss_regex_rules", {}))
    for name, pattern in regex_rules.items():
        if name not in rss_names:
            add_warning(f"rss_regex_rules.{name}", "does not match an RSS item")
        if not isinstance(pattern, str):
            add_error(f"rss_regex_rules.{name}", "must be a string")
            continue
        try:
            re.compile(pattern)
        except re.error as exc:
            add_error(f"rss_regex_rules.{name}", f"invalid regex: {exc}")

    default_group = config.get("default_group")
    if not is_non_empty_string(default_group):
        add_error("default_group", "must be a non-empty string")
    elif default_group in group_names:
        add_warning("default_group", "also exists in notification_groups")

    suffix_config = require_dict("suffix_config", config.get("suffix_config", {}))
    validate_suffix_map("suffix_config.global", suffix_config.get("global", {}), require_all_platforms=True)
    validate_suffix_map("suffix_config.groups", suffix_config.get("groups", {}), group_names)
    validate_suffix_map("suffix_config.apps", suffix_config.get("apps", {}), all_names)

    icons = require_dict("notification_icons", config.get("notification_icons", {}))
    known_icon_groups = set(group_names)
    if is_non_empty_string(default_group):
        known_icon_groups.add(default_group)
    for group, url in icons.items():
        if group not in known_icon_groups:
            add_warning(f"notification_icons.{group}", "does not match a notification group")
        check_url(f"notification_icons.{group}", url)

    check_url("default_icon", config.get("default_icon"))

    rich_media = require_dict("rich_media_config", config.get("rich_media_config", {}))
    for name, url in rich_media.items():
        if name not in all_names:
            add_warning(f"rich_media_config.{name}", "does not match a monitored item")
        check_url(f"rich_media_config.{name}", url)

    archive_mapping = require_dict("bark_archive_mapping", config.get("bark_archive_mapping", {}))
    expected_archive_groups = set(group_names)
    if is_non_empty_string(default_group):
        expected_archive_groups.add(default_group)
    for group in sorted(expected_archive_groups):
        if group not in archive_mapping:
            add_warning(f"bark_archive_mapping.{group}", "missing archive mapping")
    for group, archive in archive_mapping.items():
        if group not in expected_archive_groups:
            add_warning(f"bark_archive_mapping.{group}", "does not match a notification group")
        if not is_non_empty_string(archive):
            add_error(f"bark_archive_mapping.{group}", "must be a non-empty string")


config_data = load_json(CONFIG_FILE)
validate_config(config_data)

if warnings:
    print("Config warnings:")
    for item in warnings:
        print(f"  - {item}")

if errors:
    print("Config errors:")
    for item in errors:
        print(f"  - {item}")
    sys.exit(1)

print("Config validation passed")
