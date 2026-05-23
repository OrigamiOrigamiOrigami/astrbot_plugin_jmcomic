from jmcomic import *
import os
import time
import random
import socket
from urllib3.exceptions import InsecureRequestWarning
import urllib3

urllib3.disable_warnings(InsecureRequestWarning)

os.environ['CURL_CA_BUNDLE'] = ''
os.environ['PYTHONHTTPSVERIFY'] = '0'

PUB_PAGE_URLS = [
    'https://jmcomicgo.org',
    'https://jmcomicoi.net',
    'https://jmcomic-fb.vip',
]

GITHUB_PAGE_RANGE = range(300, 309)

FALLBACK_DOMAINS = [
    'jmcomic-zzz.one',
    'jmcomic-zzz.org',
    'comic18j-bubu.net',
    'comic18j-bubu.club',
    'comic18j-robo.cc',
]


def get_proxy_host():
    try:
        if os.path.exists('/.dockerenv'):
            return 'host.docker.internal'
        try:
            socket.gethostbyname('host.docker.internal')
            return 'host.docker.internal'
        except socket.gaierror:
            pass
        return '127.0.0.1'
    except Exception:
        return '127.0.0.1'


proxy_host = get_proxy_host()
proxy_config = {
    'http': f'http://{proxy_host}:7890',
    'https': f'http://{proxy_host}:7890',
}

meta_data = {
    'proxies': proxy_config,
    'verify': False,
    'timeout': 15,
    'headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
}

disable_jm_log()


def _is_valid_domain(domain: str) -> bool:
    if not domain or '/' in domain:
        return False
    if domain.startswith('jm365'):
        return False
    if domain.startswith('t.me'):
        return False
    return True


def _add_domains(domain_set, domains):
    for domain in domains:
        if _is_valid_domain(domain):
            domain_set.add(domain)


def fetch_domains_from_pub_pages(domain_set):
    import requests

    for url in PUB_PAGE_URLS:
        for attempt in range(3):
            try:
                time.sleep(random.uniform(0.3, 1))
                response = requests.get(
                    url,
                    proxies=proxy_config,
                    verify=False,
                    timeout=10,
                    headers=meta_data['headers'],
                    allow_redirects=True,
                )
                if response.status_code == 200:
                    _add_domains(domain_set, JmcomicText.analyse_jm_pub_html(response.text))
                    print(f"成功从发布页 {response.url} 获取域名")
                    break
                print(f"请求 {url} 返回状态码: {response.status_code}")
            except Exception as e:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                print(f"获取发布页域名失败 {url}: {e}")


def fetch_domains_from_github(domain_set):
    import requests

    template = 'https://jmcmomic.github.io/go/{}.html'
    for i in GITHUB_PAGE_RANGE:
        url = template.format(i)
        for attempt in range(3):
            try:
                time.sleep(random.uniform(0.3, 1))
                response = requests.get(
                    url,
                    allow_redirects=False,
                    proxies=proxy_config,
                    verify=False,
                    timeout=10,
                    headers=meta_data['headers'],
                )
                if response.status_code == 200:
                    _add_domains(domain_set, JmcomicText.analyse_jm_pub_html(response.text))
                    print(f"成功从 GitHub 页面 {url} 获取域名")
                    break
            except Exception as e:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                print(f"获取 GitHub 域名失败 {url}: {e}")


def fetch_domains_via_jmcomic(domain_set):
    try:
        _add_domains(domain_set, JmModuleConfig.get_html_domain_all_via_github())
        print(f"通过 jmcomic 内置方法获取到 {len(domain_set)} 个域名")
    except Exception as e:
        print(f"jmcomic 内置域名获取失败: {e}")


def get_all_domain():
    domain_set = set()

    print("正在从禁漫发布页获取最新域名...")
    fetch_domains_from_pub_pages(domain_set)

    print("正在从 GitHub 获取最新域名...")
    fetch_domains_from_github(domain_set)
    fetch_domains_via_jmcomic(domain_set)

    if not domain_set:
        print("未能动态获取域名，使用兜底域名列表")
        domain_set.update(FALLBACK_DOMAINS)

    print(f"总共获取到 {len(domain_set)} 个域名")
    return domain_set


def test_domain(domain: str, max_retries=2):
    import requests

    test_url = f"https://{domain}/album/422866"
    status = '不可用'

    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(0.3, 1))
            response = requests.get(
                test_url,
                proxies=proxy_config,
                verify=False,
                timeout=10,
                headers=meta_data['headers'],
                allow_redirects=True,
            )
            if response.status_code == 200 and ("JM" in response.text or "禁漫" in response.text or "album" in response.text.lower()):
                print(f'测试域名 {domain}: ok')
                return 'ok'
            if attempt < max_retries - 1:
                time.sleep(1 + attempt)
                continue
            print(f'测试域名 {domain}: 不可用 - 状态码 {response.status_code}')
            return status
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1 + attempt)
                continue
            print(f'测试域名 {domain}: 不可用 - {e}')
            return status

    return status


def test_api_domain():
    try:
        option = JmOption.default()
        option.client.impl = 'api'
        option.client.postman.meta_data.proxies = proxy_config
        option.client.postman.meta_data.verify = False
        client = option.new_jm_client()
        client.get_album_detail('422866')
        print(f"API 客户端可用，当前域名: {client.domain_list}")
        return True, client.domain_list
    except Exception as e:
        print(f"API 客户端测试失败: {e}")
        return False, []


def test_all_domains(domains=None):
    if domains is None:
        domains = get_all_domain()

    print(f'获取到 {len(domains)} 个域名，开始测试')
    domain_status_dict = {}

    for domain in domains:
        try:
            domain_status_dict[domain] = test_domain(domain)
        except Exception as ex:
            print(f"测试域名失败: {domain} - {ex}")
            domain_status_dict[domain] = '不可用'

    return domain_status_dict


if __name__ == "__main__":
    try:
        api_ok, api_domains = test_api_domain()
        print(f"\nAPI 客户端: {'可用' if api_ok else '不可用'}")
        if api_domains:
            print("API 域名:", api_domains)

        domain_set = get_all_domain()
        domain_status_dict = test_all_domains(domain_set)

        print("\n网页端域名测试结果:")
        available_domains = []
        for domain, status in domain_status_dict.items():
            print(f'{domain}: {status}')
            if status == 'ok':
                available_domains.append(domain)

        print(f"\n共找到 {len(available_domains)} 个可用网页域名:")
        for domain in available_domains:
            print(f"- {domain}")
    except Exception as e:
        print(f"测试过程发生错误: {e}")
