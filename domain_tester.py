"""禁漫域名探测：标准库 + jmcomic，不依赖 requests。"""
from __future__ import annotations

import logging
import os
import random
import socket
import ssl
import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from jmcomic import *

logger = logging.getLogger("astrbot")

os.environ["CURL_CA_BUNDLE"] = ""
os.environ["PYTHONHTTPSVERIFY"] = "0"

PUB_PAGE_URLS = [
    "https://jmcomicgo.org",
    "https://jmcomicoi.net",
    "https://jmcomic-fb.vip",
]

FALLBACK_DOMAINS = [
    "jmcomic-zzz.one",
    "jmcomic-zzz.org",
    "comic18j-bubu.net",
    "comic18j-bubu.club",
    "comic18j-robo.cc",
]

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def get_proxy_host():
    try:
        if os.path.exists("/.dockerenv"):
            return "host.docker.internal"
        try:
            socket.gethostbyname("host.docker.internal")
            return "host.docker.internal"
        except socket.gaierror:
            pass
        return "127.0.0.1"
    except Exception:
        return "127.0.0.1"


proxy_host = get_proxy_host()
proxy_config = {
    "http": f"http://{proxy_host}:7890",
    "https": f"http://{proxy_host}:7890",
}

meta_data = {
    "proxies": proxy_config,
    "verify": False,
    "timeout": 15,
    "headers": dict(_DEFAULT_HEADERS),
}

disable_jm_log()


def configure_proxy(proxy_address: Optional[str] = None, *, use_proxy: bool = True) -> None:
    global proxy_config, meta_data
    if not use_proxy:
        proxy_config = {}
    elif proxy_address:
        proxy_config = {"http": proxy_address, "https": proxy_address}
    else:
        host = get_proxy_host()
        proxy_config = {
            "http": f"http://{host}:7890",
            "https": f"http://{host}:7890",
        }
    meta_data["proxies"] = proxy_config


def _http_get(url: str, *, timeout: int = 10) -> tuple[int, str, str]:
    ctx = ssl._create_unverified_context()
    handlers: list = [HTTPSHandler(context=ctx)]
    if proxy_config.get("http") or proxy_config.get("https"):
        handlers.insert(
            0,
            ProxyHandler(
                {
                    "http": proxy_config.get("http") or proxy_config.get("https"),
                    "https": proxy_config.get("https") or proxy_config.get("http"),
                }
            ),
        )
    opener = build_opener(*handlers)
    req = Request(url, headers=_DEFAULT_HEADERS)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return int(getattr(resp, "status", None) or resp.getcode() or 200), resp.geturl(), text
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return int(e.code or 0), url, body


def _is_valid_domain(domain: str) -> bool:
    if not domain or "/" in domain:
        return False
    if domain.startswith("jm365"):
        return False
    if domain.startswith("t.me"):
        return False
    return True


def _add_domains(domain_set, domains):
    for domain in domains:
        if _is_valid_domain(domain):
            domain_set.add(domain)


def fetch_domains_from_pub_pages(domain_set):
    for url in PUB_PAGE_URLS:
        for attempt in range(3):
            try:
                time.sleep(random.uniform(0.3, 1))
                status, final_url, text = _http_get(url, timeout=10)
                if status == 200:
                    _add_domains(domain_set, JmcomicText.analyse_jm_pub_html(text))
                    logger.info("成功从发布页 %s 获取域名", final_url)
                    break
                logger.warning("请求 %s 返回状态码: %s", url, status)
            except (URLError, OSError, TimeoutError, Exception) as e:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                logger.warning("获取发布页域名失败 %s: %s", url, e)


def fetch_domains_via_jmcomic(domain_set):
    try:
        postman = JmModuleConfig.new_postman(meta_data=meta_data, session=True)
        _add_domains(domain_set, JmModuleConfig.get_html_domain_all(postman))
        logger.info("通过 jmcomic 内置方法获取到 %s 个域名", len(domain_set))
    except Exception as e:
        logger.warning("jmcomic 内置域名获取失败: %s", e)


def get_all_domain():
    domain_set = set()
    logger.info("正在从禁漫发布页获取最新域名...")
    fetch_domains_from_pub_pages(domain_set)
    fetch_domains_via_jmcomic(domain_set)
    if not domain_set:
        logger.warning("未能动态获取域名，使用兜底域名列表")
        domain_set.update(FALLBACK_DOMAINS)
    logger.info("总共获取到 %s 个域名", len(domain_set))
    return domain_set


def test_domain(domain: str, max_retries=2):
    test_url = f"https://{domain}/album/422866"
    status = "不可用"
    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(0.3, 1))
            code, _final, text = _http_get(test_url, timeout=10)
            if code == 200 and (
                "JM" in text or "禁漫" in text or "album" in text.lower()
            ):
                logger.info("测试域名 %s: ok", domain)
                return "ok"
            if attempt < max_retries - 1:
                time.sleep(1 + attempt)
                continue
            logger.info("测试域名 %s: 不可用 - 状态码 %s", domain, code)
            return status
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1 + attempt)
                continue
            logger.info("测试域名 %s: 不可用 - %s", domain, e)
            return status
    return status


def test_api_domain():
    try:
        option = JmOption.default()
        option.client.impl = "api"
        option.client.postman.meta_data.proxies = proxy_config
        option.client.postman.meta_data.verify = False
        client = option.new_jm_client()
        client.get_album_detail("422866")
        logger.info("API 客户端可用，当前域名: %s", client.domain_list)
        return True, client.domain_list
    except Exception as e:
        logger.warning("API 客户端测试失败: %s", e)
        return False, []


def test_all_domains(domains=None):
    if domains is None:
        domains = get_all_domain()
    logger.info("获取到 %s 个域名，开始测试", len(domains))
    domain_status_dict = {}
    for domain in domains:
        try:
            domain_status_dict[domain] = test_domain(domain)
        except Exception as ex:
            logger.warning("测试域名失败: %s - %s", domain, ex)
            domain_status_dict[domain] = "不可用"
    return domain_status_dict


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        api_ok, api_domains = test_api_domain()
        print(f"\nAPI 客户端: {'可用' if api_ok else '不可用'}")
        if api_domains:
            print("API 域名:", api_domains)
        domain_status_dict = test_all_domains(get_all_domain())
        print("\n网页端域名测试结果:")
        available = [d for d, s in domain_status_dict.items() if s == "ok"]
        for domain, status in domain_status_dict.items():
            print(f"{domain}: {status}")
        print(f"\n共找到 {len(available)} 个可用网页域名")
    except Exception as e:
        print(f"测试过程发生错误: {e}")
