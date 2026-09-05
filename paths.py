import logging
import os
import re
import shutil

from astrbot.api.star import StarTools

from . import deps
from .proxy_util import (
    apply_jmcomic_log_config,
    get_default_proxy_config,
    get_proxy_host,
)

logger = logging.getLogger("astrbot")


def resolve_data_dir(plugin_root: str) -> str:
    try:
        return str(StarTools.get_data_dir("jmcomic"))
    except Exception as e:
        data_dir = os.path.join(plugin_root, "data")
        os.makedirs(data_dir, exist_ok=True)
        logger.warning("StarTools.get_data_dir 不可用，回退到 %s: %s", data_dir, e)
        return data_dir


def resolve_download_dir(data_dir: str, cfg_download: str) -> str:
    cfg_download = (cfg_download or "").strip()
    if cfg_download and cfg_download not in (".", "./downloads", "downloads"):
        return (
            cfg_download
            if os.path.isabs(cfg_download)
            else os.path.join(data_dir, cfg_download)
        )
    return os.path.join(data_dir, "downloads")


def ensure_option_file(plugin_root: str, data_dir: str, option_file: str) -> None:
    """运行时 option 落 plugin_data；首次从模板或旧插件目录复制。"""
    if os.path.isfile(option_file):
        return
    legacy = os.path.join(plugin_root, "option.yml")
    template = os.path.join(plugin_root, "option.example.yml")
    src = legacy if os.path.isfile(legacy) else template
    if not os.path.isfile(src):
        logger.error("缺少 option 模板: %s", template)
        return
    try:
        os.makedirs(data_dir, exist_ok=True)
        shutil.copy2(src, option_file)
        logger.info("已初始化 option.yml → %s", option_file)
    except Exception as e:
        logger.error("复制 option.yml 失败: %s", e)


def pdf_path(download_dir: str, legacy_download_dir: str, comic_id: str) -> str:
    """优先 plugin_data；兼容旧版插件目录缓存。"""
    primary = os.path.join(download_dir, f"jm_{comic_id}.pdf")
    if os.path.exists(primary):
        return primary
    legacy = os.path.join(legacy_download_dir, f"jm_{comic_id}.pdf")
    if os.path.exists(legacy):
        return legacy
    return primary


def apply_runtime_option(
    option_file: str,
    download_dir: str,
    plugin_cfg: dict,
) -> None:
    """更新 option.yml：下载目录、SSL、代理、客户端、日志。"""
    use_proxy = plugin_cfg.get("use_proxy", True)
    proxy_address = plugin_cfg.get("proxy_address", "http://127.0.0.1:7890")
    proxy_port = plugin_cfg.get("proxy_port", 7890)
    timeout = plugin_cfg.get("timeout", 10)
    retry_times = plugin_cfg.get("retry_times", 10)
    client_impl = plugin_cfg.get("client_impl", "api")
    jmcomic_log_level = plugin_cfg.get("jmcomic_log_level", "off")

    try:
        with open(option_file, 'r', encoding='utf-8') as f:
            config_data = deps.yaml.safe_load(f) or {}

        config_data.setdefault("dir_rule", {})
        config_data["dir_rule"]["base_dir"] = download_dir
        config_data["dir_rule"].setdefault("rule", "Bd")

        # 更新SSL验证和代理设置
        if 'client' in config_data and 'postman' in config_data['client'] and 'meta_data' in config_data['client']['postman']:
            config_data['client']['postman']['meta_data']['verify'] = False

            # 配置超时时间
            config_data['client']['postman']['meta_data']['timeout'] = timeout

            # 配置代理
            if use_proxy:
                # 检测Docker环境并调整代理地址
                proxy_host = get_proxy_host()

                if proxy_address:
                    # 解析用户配置的代理地址，替换主机部分
                    # 匹配 http://host:port 格式
                    proxy_match = re.match(r'(https?://)([^:]+):(\d+)', proxy_address)
                    if proxy_match:
                        protocol = proxy_match.group(1)
                        port = proxy_match.group(3)
                        # 使用检测到的代理主机
                        adjusted_proxy = f"{protocol}{proxy_host}:{port}"
                        config_data['client']['postman']['meta_data']['proxies'] = {
                            'http': adjusted_proxy,
                            'https': adjusted_proxy
                        }
                        logger.info(f"使用代理: {adjusted_proxy}")
                    else:
                        # 如果格式不匹配，直接使用原地址
                        config_data['client']['postman']['meta_data']['proxies'] = {
                            'http': proxy_address,
                            'https': proxy_address
                        }
                else:
                    # 使用自动检测的代理配置
                    default_proxy = get_default_proxy_config(proxy_port)
                    config_data['client']['postman']['meta_data']['proxies'] = default_proxy
                    logger.info(f"使用代理: {default_proxy}")
            else:
                # 不使用代理，移除代理配置
                if 'proxies' in config_data['client']['postman']['meta_data']:
                    del config_data['client']['postman']['meta_data']['proxies']

            # 配置客户端实现类型
            if 'client' in config_data:
                config_data['client']['impl'] = client_impl
                config_data['client']['retry_times'] = retry_times
                if client_impl == 'api':
                    # API 客户端自动拉取最新域名，无需手动配置网页域名
                    config_data['client']['domain'] = []

            apply_jmcomic_log_config(config_data, jmcomic_log_level)
            if jmcomic_log_level == 'off' and deps.jmcomic is not None:
                deps.jmcomic.disable_jm_log()

            # 写回配置文件
            with open(option_file, 'w', encoding='utf-8') as f:
                deps.yaml.dump(config_data, f, allow_unicode=True)

            # 记录最终的代理配置
            if 'client' in config_data and 'postman' in config_data['client'] and 'meta_data' in config_data['client']['postman']:
                proxies = config_data['client']['postman']['meta_data'].get('proxies', {})
                logger.info(f"配置文件已更新，代理设置: {proxies}")
        else:
            logger.warning("配置文件格式不符合预期，无法更新SSL和代理设置")
    except Exception as e:
        logger.error(f"更新配置文件SSL设置失败: {str(e)}")
