from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import CommandResult

import os
import logging
import asyncio
import re
import traceback
import glob
import io
import time
import socket
import sys
import json
import shutil
import random
import importlib.util
import subprocess

logger = logging.getLogger("astrbot")

JMCOMIC_PACKAGE = "jmcomic"


def get_package_version(package_name: str):
    try:
        from importlib.metadata import version
        return version(package_name)
    except Exception:
        return None


def update_jmcomic_package():
    """升级 jmcomic 到 PyPI 最新版（pip install jmcomic -U）"""
    old_version = get_package_version(JMCOMIC_PACKAGE)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-U", JMCOMIC_PACKAGE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        new_version = get_package_version(JMCOMIC_PACKAGE)
        return True, old_version, new_version, None
    except subprocess.CalledProcessError as e:
        return False, old_version, get_package_version(JMCOMIC_PACKAGE), str(e)
    except Exception as e:
        return False, old_version, get_package_version(JMCOMIC_PACKAGE), str(e)


def reload_jmcomic_modules():
    """pip 升级后重新加载 jmcomic 模块"""
    global jmcomic, DirRule
    import importlib
    import jmcomic as jm_module
    importlib.reload(jm_module)
    from jmcomic.jm_option import DirRule as reloaded_dir_rule
    jmcomic = jm_module
    DirRule = reloaded_dir_rule
    return jmcomic


# 依赖检查和自动安装
def check_and_install_dependencies():
    """检查并自动安装缺失的依赖"""
    required_packages = {
        'PIL': 'pillow',
        'yaml': 'pyyaml',
        'img2pdf': 'img2pdf',
        'jmcomic': 'jmcomic>=2.6.20'
    }
    
    missing_packages = []
    
    for module_name, package_name in required_packages.items():
        try:
            __import__(module_name)
        except ImportError:
            missing_packages.append(package_name)
            logger.warning(f"缺少依赖: {package_name}")
    
    if missing_packages:
        logger.info(f"检测到缺失的依赖包: {', '.join(missing_packages)}")
        logger.info("正在自动安装依赖...")
        
        try:
            # 使用 pip 安装缺失的包
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install"] + missing_packages,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info("依赖安装成功！")
            
            # 重新导入已安装的模块
            for module_name in required_packages.keys():
                try:
                    globals()[module_name] = __import__(module_name)
                except ImportError as e:
                    logger.error(f"导入模块 {module_name} 失败: {e}")
            
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"自动安装依赖失败: {e}")
            logger.error("请手动运行: pip install " + " ".join(missing_packages))
            return False
        except Exception as e:
            logger.error(f"安装依赖时发生错误: {e}")
            return False
    
    return True

# 执行依赖检查
if not check_and_install_dependencies():
    logger.error("JMComic 插件依赖未满足，插件可能无法正常工作")

# 导入依赖（如果安装成功）
try:
    from PIL import Image
    import yaml
    import img2pdf
    import jmcomic
    from jmcomic.jm_option import DirRule
except ImportError as e:
    logger.error(f"导入依赖失败: {e}")
    logger.error("请手动安装: pip install pillow pyyaml img2pdf jmcomic")
    # 创建占位符，避免后续代码报错
    Image = None
    yaml = None
    img2pdf = None
    jmcomic = None
    DirRule = None

def get_proxy_host():
    """
    自动检测环境并返回合适的代理主机地址
    Docker环境使用 host.docker.internal，本地环境使用 127.0.0.1
    """
    try:
        if os.path.exists('/.dockerenv'):
            logger.info("检测到Docker环境，使用 host.docker.internal 作为代理主机")
            return 'host.docker.internal'

        try:
            socket.gethostbyname('host.docker.internal')
            logger.info("能够解析 host.docker.internal，判断为Docker环境")
            return 'host.docker.internal'
        except socket.gaierror:
            pass

        # 默认为本地环境
        logger.info("检测到本地环境，使用 127.0.0.1 作为代理主机")
        return '127.0.0.1'

    except Exception as e:
        logger.warning(f"环境检测失败，使用默认的 127.0.0.1: {str(e)}")
        return '127.0.0.1'

def get_default_proxy_config(port=7890):
    """获取默认的代理配置，自动适配环境"""
    proxy_host = get_proxy_host()
    return {
        'http': f'http://{proxy_host}:{port}',
        'https': f'http://{proxy_host}:{port}'
    }

# jmcomic 日志级别：off=关闭, summary=仅本子/章节/错误, full=全部
JMCOMIC_SUMMARY_LOG_TOPICS = [
    'album.before', 'album.after',
    'photo.before', 'photo.after',
    'image.failed', 'photo.failed',
    'req.fallback', 'req.error',
    'api.update_domain.error',
]


def apply_jmcomic_log_config(config_data: dict, log_level: str):
    """根据日志级别更新 option.yml 中的 jmcomic 日志配置"""
    plugins = config_data.setdefault('plugins', {})

    def remove_log_filter():
        for hook in list(plugins.keys()):
            if isinstance(plugins[hook], list):
                plugins[hook] = [
                    p for p in plugins[hook]
                    if not (isinstance(p, dict) and p.get('plugin') == 'log_topic_filter')
                ]

    if log_level == 'full':
        config_data['log'] = True
        remove_log_filter()
    elif log_level == 'summary':
        config_data['log'] = True
        remove_log_filter()
        after_init = plugins.setdefault('after_init', [])
        after_init.append({
            'plugin': 'log_topic_filter',
            'kwargs': {'whitelist': JMCOMIC_SUMMARY_LOG_TOPICS},
        })
    else:
        config_data['log'] = False
        remove_log_filter()

    return config_data


def get_album_title(album_detail, comic_id: str) -> str:
    """从 jmcomic 本子详情对象提取标题（兼容不同版本 API）"""
    fallback = f"漫画_{comic_id}"
    if album_detail is None:
        return fallback
    for attr in ('name', 'album_name', 'title'):
        value = getattr(album_detail, attr, None)
        if value:
            return str(value).strip()
    try:
        for _, title in album_detail.iter_id_title():
            if title:
                return str(title).strip()
    except Exception:
        pass
    return fallback


def _truncate_list(items, limit=8) -> str:
    items = [str(i) for i in items if i]
    if not items:
        return ""
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" 等{len(items)}个"


def parse_comic_id(text: str):
    """从纯数字或混合文本中提取漫画 ID（兼容 jmv 风格输入）"""
    if not text:
        return None
    text = text.strip()
    if text.isdigit():
        return text
    numbers = re.findall(r'\d{4,}', text) or re.findall(r'\d+', text)
    if not numbers:
        return None
    return max(numbers, key=len)


R18G_TAG_PATTERN = re.compile(r'r[\s\-_]?18[\s\-_]?g', re.I)
R18G_BLOCK_MESSAGE = "该内容为 R-18G，已拒绝展示或下载"


def _normalize_tag_key(tag: str) -> str:
    return re.sub(r'[\s\-_]', '', (tag or '').lower())


def is_r18g_tag(tag: str) -> bool:
    """判断单个标签是否为 R-18G"""
    tag = (tag or '').strip()
    if not tag:
        return False
    if R18G_TAG_PATTERN.search(tag):
        return True
    return _normalize_tag_key(tag) == 'r18g'


def tags_contain_r18g(tags) -> bool:
    """判断标签列表是否包含 R-18G"""
    if not tags:
        return False
    if isinstance(tags, str):
        tags = re.split(r'[\s,，、/|]+', tags)
    return any(is_r18g_tag(tag) for tag in tags)


def album_has_r18g(album_detail) -> bool:
    """判断本子详情是否带有 R-18G 标签"""
    return tags_contain_r18g(getattr(album_detail, 'tags', None) or [])


def is_r18g_search_query(query: str) -> bool:
    """判断搜索关键词是否在请求 R-18G 内容"""
    query = (query or '').strip()
    if not query:
        return False
    if is_r18g_tag(query):
        return True
    return 'r18g' in _normalize_tag_key(query)


def collect_search_items(result_page, filter_r18g: bool = True):
    """
    收集搜索结果，可选过滤 R-18G。
    返回 (items, hidden_count)，items 为 [(album_id, title), ...]
    """
    all_items = list(result_page.iter_id_title_tag())
    if not filter_r18g:
        return [(album_id, title) for album_id, title, _ in all_items], 0

    items = []
    hidden = 0
    for album_id, title, tags in all_items:
        if tags_contain_r18g(tags):
            hidden += 1
            continue
        items.append((album_id, title))
    return items, hidden


def resolve_album_page_count(album_detail, client=None) -> int:
    """
    获取本子总页数。
    jmcomic API 客户端会把 page_count 硬编码为 0，需从各章节实际页数汇总。
    """
    count = int(getattr(album_detail, 'page_count', 0) or 0)
    if count > 0:
        return count
    if client is None:
        return 0

    episode_list = getattr(album_detail, 'episode_list', None) or []
    if not episode_list:
        return 0

    total = 0
    for pid, _, _ in episode_list:
        try:
            photo = client.get_photo_detail(str(pid), fetch_album=False)
            total += len(photo)
        except Exception as e:
            logger.warning(f"统计章节 {pid} 页数失败: {e}")
    return total


def format_album_info(album_detail, comic_id: str, client=None, page_count=None, max_desc_len: int = 400) -> str:
    """格式化本子详情文本（标题、作者、标签、简介等）"""
    album_id = getattr(album_detail, 'album_id', comic_id)
    lines = [
        f"📖 {get_album_title(album_detail, comic_id)}",
        f"🆔 JM{album_id}",
    ]

    authors = getattr(album_detail, 'authors', None) or []
    if authors:
        lines.append(f"✍️ {_truncate_list(authors)}")

    if page_count is None:
        page_count = resolve_album_page_count(album_detail, client)
    views = getattr(album_detail, 'views', None)
    likes = getattr(album_detail, 'likes', None)
    meta_parts = []
    if page_count > 0:
        meta_parts.append(f"📄 {page_count}页")
    if views:
        meta_parts.append(f"👀 {views}")
    if likes:
        meta_parts.append(f"❤️ {likes}")
    if meta_parts:
        lines.append(" · ".join(meta_parts))

    episode_list = getattr(album_detail, 'episode_list', None) or []
    if episode_list:
        lines.append(f"📑 共 {len(episode_list)} 章")

    tags = getattr(album_detail, 'tags', None) or []
    if tags:
        lines.append(f"🏷️ {_truncate_list(tags, 10)}")

    actors = getattr(album_detail, 'actors', None) or []
    if actors:
        lines.append(f"🎭 {_truncate_list(actors, 6)}")

    works = getattr(album_detail, 'works', None) or []
    if works:
        lines.append(f"📚 {_truncate_list(works, 6)}")

    description = (getattr(album_detail, 'description', None) or "").strip()
    if description:
        if len(description) > max_desc_len:
            description = description[:max_desc_len] + "..."
        lines.append(f"\n📝 {description}")

    return "\n".join(lines)


def download_album_cover(client, comic_id: str, save_dir: str):
    """下载本子封面到本地，返回文件路径（优先 3:4 比例，避免 1:1 正方形缩略图）"""
    try:
        os.makedirs(save_dir, exist_ok=True)
        cover_path = os.path.join(save_dir, f"cover_{comic_id}.jpg")
        # _3x4 为禁漫列表页封面比例（约 3:4），默认无后缀则为 400x400 正方形
        for size in ('_3x4', ''):
            try:
                client.download_album_cover(comic_id, cover_path, size=size)
                if os.path.exists(cover_path) and os.path.getsize(cover_path) > 0:
                    with Image.open(cover_path) as img:
                        w, h = img.size
                    logger.info(f"封面下载成功 size={size or 'default'}: {w}x{h}")
                    if size == '_3x4' or w != h:
                        return cover_path
            except Exception as e:
                logger.warning(f"下载封面失败 size={size or 'default'}: {e}")
        if os.path.exists(cover_path) and os.path.getsize(cover_path) > 0:
            return cover_path
    except Exception as e:
        logger.warning(f"下载封面失败: {e}")
    return None


def rotate_image_180(image_path: str) -> str:
    """将图片旋转180度并保存为新文件"""
    root, ext = os.path.splitext(image_path)
    rotated_path = f"{root}_rot180{ext or '.jpg'}"
    with Image.open(image_path) as img:
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.rotate(180, expand=True).save(rotated_path, "JPEG", quality=90)
    return rotated_path


def fetch_album_detail_sync(option_file: str, comic_id: str):
    """在线程池中获取本子详情，避免阻塞事件循环"""
    option = jmcomic.create_option_by_file(option_file)
    client = option.new_jm_client()
    album_detail = client.get_album_detail(comic_id)
    return album_detail, client, get_album_title(album_detail, comic_id)


def prepare_album_preview_sync(cover_dir: str, comic_id: str, album_detail, client):
    """在线程池中准备简介文本与封面路径"""
    page_count = resolve_album_page_count(album_detail, client)
    info_text = format_album_info(album_detail, comic_id, page_count=page_count)
    cover_path = download_album_cover(client, comic_id, cover_dir)
    return info_text, cover_path


SEARCH_MODE_ALIASES = {
    'tag': 'tag',
    'tags': 'tag',
    '标签': 'tag',
    'title': 'title',
    'name': 'title',
    'work': 'title',
    '标题': 'title',
    '作品': 'title',
}


def parse_search_command(command_args: list):
    """
    解析 jm search 子命令。
    返回 (mode, query, page, error_msg)
    """
    if len(command_args) < 3:
        return None, None, None, (
            "用法:\n"
            "jm search tag <标签> [页码]\n"
            "jm search title <标题关键词> [页码]\n"
            "示例: jm search tag 无修正\n"
            "      jm search title 姐姐 2"
        )

    mode = SEARCH_MODE_ALIASES.get(command_args[1].lower())
    if not mode:
        return None, None, None, (
            f"未知搜索类型: {command_args[1]}\n"
            "支持: tag / 标签, title / 标题"
        )

    rest = command_args[2:]
    page = 1
    if rest and rest[-1].isdigit():
        page = max(1, int(rest[-1]))
        rest = rest[:-1]

    query = ' '.join(rest).strip()
    if not query:
        return None, None, None, "搜索关键词不能为空"

    return mode, query, page, None


def search_albums_sync(option_file: str, mode: str, query: str, page: int = 1):
    """在线程池中按标签或标题搜索本子"""
    option = jmcomic.create_option_by_file(option_file)
    client = option.new_jm_client()
    if mode == 'tag':
        return client.search_tag(query, page=page)
    return client.search_work(query, page=page)


def format_search_results(
    result_page,
    mode: str,
    query: str,
    page: int,
    max_results: int = 10,
    filter_r18g: bool = True,
) -> str:
    """格式化搜索结果为可读文本"""
    mode_label = '标签' if mode == 'tag' else '标题'
    mode_cmd = 'tag' if mode == 'tag' else 'title'
    items, hidden_count = collect_search_items(result_page, filter_r18g)

    if not items:
        suffix = "（已过滤 R-18G 内容）" if hidden_count > 0 else ""
        return f"🔍 {mode_label}「{query}」第 {page} 页：未找到结果{suffix}"

    total = int(getattr(result_page, 'total', 0) or 0)
    try:
        total_pages = max(1, int(result_page.page_count))
    except Exception:
        total_pages = 1

    lines = [
        f"🔍 {mode_label}搜索「{query}」第 {page}/{total_pages} 页",
    ]
    if total > 0:
        lines[0] += f"（约 {total} 条）"
    lines.append("")

    show_count = min(len(items), max_results)
    for i, (album_id, title) in enumerate(items[:show_count], 1):
        title = (title or '无标题').replace('\n', ' ').strip()
        if len(title) > 48:
            title = title[:45] + '...'
        lines.append(f"{i}. JM{album_id} - {title}")

    if len(items) > show_count:
        lines.append(f"... 本页还有 {len(items) - show_count} 条未显示")
    if hidden_count > 0:
        lines.append(f"🚫 已隐藏 {hidden_count} 条 R-18G 内容")

    next_page = page + 1 if page < total_pages else None
    lines.append("")
    lines.append("💡 下载: jm <ID>")
    if next_page:
        lines.append(f"💡 下一页: jm search {mode_cmd} {query} {next_page}")

    return '\n'.join(lines)

# 添加常用User-Agents列表
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0'
]

@register("jmcomic", "Origami", "禁漫漫画下载插件", "1.0.3")
class Main(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}
        
        # 检查依赖是否已安装
        if jmcomic is None or yaml is None or Image is None or img2pdf is None:
            logger.error("JMComic 插件依赖未安装，插件无法初始化")
            logger.error("请运行: pip install pillow pyyaml img2pdf jmcomic")
            return
        
        # 配置项
        self.download_path = self.config.get("download_path", "./downloads")
        self.use_proxy = self.config.get("use_proxy", True)
        self.proxy_address = self.config.get("proxy_address", "http://127.0.0.1:7890")
        self.proxy_port = self.config.get("proxy_port", 7890)
        self.custom_domains = self.config.get("custom_domains", "")
        self.client_impl = self.config.get("client_impl", "api")
        self.auto_update_jmcomic = self.config.get("auto_update_jmcomic", True)
        self.cleanup_days = self.config.get("cleanup_days", 3)
        self.compress_quality = self.config.get("compress_quality", 85)
        self.max_image_dimension = self.config.get("max_image_dimension", 1200)
        self.timeout = self.config.get("timeout", 10)
        self.retry_times = self.config.get("retry_times", 10)
        self.jmcomic_log_level = self.config.get("jmcomic_log_level", "off")
        self.send_album_preview = self.config.get("send_album_preview", True)
        self.search_max_results = self.config.get("search_max_results", 10)
        self.filter_r18g = self.config.get("filter_r18g", True)
        
        # 获取配置文件路径
        self.option_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'option.yml')
        
        # 创建下载目录
        if not os.path.exists(self.download_path):
            try:
                os.makedirs(self.download_path)
                logger.info(f"创建下载目录成功: {self.download_path}")
            except Exception as e:
                logger.error(f"创建下载目录失败: {e}")
                
        # 检查配置文件是否存在
        if not os.path.exists(self.option_file):
            logger.error(f"配置文件不存在: {self.option_file}")
            return
        
        # 更新配置文件，禁用SSL验证
        try:
            # 读取配置文件
            with open(self.option_file, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
            
            # 更新SSL验证和代理设置
            if 'client' in config_data and 'postman' in config_data['client'] and 'meta_data' in config_data['client']['postman']:
                config_data['client']['postman']['meta_data']['verify'] = False
                
                # 配置超时时间
                config_data['client']['postman']['meta_data']['timeout'] = self.timeout
                
                # 配置代理
                if self.use_proxy:
                    # 检测Docker环境并调整代理地址
                    proxy_host = get_proxy_host()
                    
                    if self.proxy_address:
                        # 解析用户配置的代理地址，替换主机部分
                        import re
                        # 匹配 http://host:port 格式
                        proxy_match = re.match(r'(https?://)([^:]+):(\d+)', self.proxy_address)
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
                                'http': self.proxy_address,
                                'https': self.proxy_address
                            }
                    else:
                        # 使用自动检测的代理配置
                        default_proxy = get_default_proxy_config(self.proxy_port)
                        config_data['client']['postman']['meta_data']['proxies'] = default_proxy
                        logger.info(f"使用代理: {default_proxy}")
                else:
                    # 不使用代理，移除代理配置
                    if 'proxies' in config_data['client']['postman']['meta_data']:
                        del config_data['client']['postman']['meta_data']['proxies']
                
                # 配置客户端实现类型
                if 'client' in config_data:
                    config_data['client']['impl'] = self.client_impl
                    config_data['client']['retry_times'] = self.retry_times
                    if self.client_impl == 'api':
                        # API 客户端自动拉取最新域名，无需手动配置网页域名
                        config_data['client']['domain'] = []
                
                apply_jmcomic_log_config(config_data, self.jmcomic_log_level)
                if self.jmcomic_log_level == 'off' and jmcomic is not None:
                    jmcomic.disable_jm_log()
                
                # 写回配置文件
                with open(self.option_file, 'w', encoding='utf-8') as f:
                    yaml.dump(config_data, f, allow_unicode=True)
                
                # 记录最终的代理配置
                if 'client' in config_data and 'postman' in config_data['client'] and 'meta_data' in config_data['client']['postman']:
                    proxies = config_data['client']['postman']['meta_data'].get('proxies', {})
                    logger.info(f"配置文件已更新，代理设置: {proxies}")
            else:
                logger.warning("配置文件格式不符合预期，无法更新SSL和代理设置")
        except Exception as e:
            logger.error(f"更新配置文件SSL设置失败: {str(e)}")
        
        # 配置自定义域名（仅 html 客户端需要）
        if self.client_impl == "html" and self.custom_domains:
            domains = [d.strip() for d in self.custom_domains.split(',') if d.strip()]
            if domains:
                self._update_domains_in_config(domains)
                logger.info(f"已配置自定义网页域名: {domains}")
        elif self.client_impl == "api":
            logger.info("使用 API 客户端，域名将由 jmcomic 自动更新")
        
        # 注册命令
        self.context.register_commands(
            "jmcomic",
            r"^jm\s+(.+)$",
            "禁漫下载指令",
            1,
            lambda ctx, msg: self.handle_jm_command(ctx, msg),
            use_regex=True,
            ignore_prefix=True
        )
        
        # 启动后台任务：jmcomic 自动更新、过期文件清理
        asyncio.create_task(self._startup_tasks())
    
    def _build_download_start_result(self, comic_id: str):
        """构建下载启动时的 CommandResult（仅状态文本）"""
        return CommandResult().message(
            f"漫画 {comic_id} 下载任务已启动，将在后台进行下载..."
        )

    async def _send_text(self, event: AstrMessageEvent, text: str):
        """发送纯文本消息"""
        if not text:
            return
        try:
            await event.send(CommandResult().message(text))
        except Exception as e:
            logger.warning(f"文本消息发送失败: {e}")

    async def _send_cover_image(self, event: AstrMessageEvent, cover_path: str):
        """发送封面图片，失败时旋转180度后重试"""
        if not cover_path or not os.path.exists(cover_path):
            return False

        async def try_send(path: str) -> bool:
            try:
                await event.send(CommandResult().file_image(os.path.abspath(path)))
                return True
            except Exception as e:
                logger.warning(f"封面发送失败 ({path}): {e}")
                return False

        if await try_send(cover_path):
            return True

        try:
            rotated_path = rotate_image_180(cover_path)
            logger.info(f"封面发送失败，已旋转180度重试: {rotated_path}")
            return await try_send(rotated_path)
        except Exception as e:
            logger.warning(f"封面旋转后仍发送失败: {e}")
            return False

    async def _send_album_preview_split(self, event: AstrMessageEvent, album_detail, comic_id: str, client):
        """分开发送本子简介与封面图"""
        if not self.send_album_preview or album_detail is None:
            return

        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        cover_dir = os.path.join(plugin_dir, "downloads", "_covers")
        loop = asyncio.get_event_loop()
        info_text, cover_path = await loop.run_in_executor(
            None,
            prepare_album_preview_sync,
            cover_dir,
            comic_id,
            album_detail,
            client,
        )

        await self._send_text(event, info_text)
        if cover_path:
            await asyncio.sleep(0.3)
            await self._send_cover_image(event, cover_path)

    async def _fetch_album(self, comic_id: str):
        """异步获取本子详情"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, fetch_album_detail_sync, self.option_file, comic_id
        )

    def _format_jm_error(self, error_msg: str, comic_id: str = None) -> str:
        """将底层异常转为用户可读提示"""
        if "403" in error_msg or "Forbidden" in error_msg or "ip地区禁止访问" in error_msg:
            return "访问被拒绝，请使用代理"
        if "Connection" in error_msg or "请求失败" in error_msg:
            return "网络连接失败"
        if "MissingAlbumPhotoException" in error_msg or "请求的本子不存在" in error_msg:
            return f"漫画ID {comic_id} 不存在" if comic_id else "漫画不存在"
        return "连接失败，请检查网络或使用代理"

    async def show_album_info(self, comic_id: str, event: AstrMessageEvent):
        """仅查询本子详情，不下载"""
        try:
            album_detail, client, _ = await self._fetch_album(comic_id)
            if self.filter_r18g and album_has_r18g(album_detail):
                return CommandResult().message(R18G_BLOCK_MESSAGE)
            await self._send_album_preview_split(event, album_detail, comic_id, client)
            return CommandResult().message(f"已查询漫画 {comic_id} 的详情")
        except Exception as e:
            logger.error(f"查询漫画详情失败: {str(e)}")
            return CommandResult().message(self._format_jm_error(str(e), comic_id))

    async def search_comics(self, mode: str, query: str, page: int, event: AstrMessageEvent):
        """按标签或标题搜索本子"""
        if self.filter_r18g and is_r18g_search_query(query):
            return CommandResult().message("不支持搜索 R-18G 内容")

        try:
            loop = asyncio.get_event_loop()
            result_page = await loop.run_in_executor(
                None, search_albums_sync, self.option_file, mode, query, page
            )
            text = format_search_results(
                result_page,
                mode,
                query,
                page,
                self.search_max_results,
                self.filter_r18g,
            )
            return CommandResult().message(text)
        except Exception as e:
            logger.error(f"搜索失败 [{mode}={query} p{page}]: {str(e)}")
            return CommandResult().message(self._format_jm_error(str(e)))
    
    async def _startup_tasks(self):
        """插件启动时的后台任务"""
        if self.auto_update_jmcomic:
            await self._auto_update_jmcomic()
        asyncio.create_task(self._start_cleanup_task())
    
    async def _auto_update_jmcomic(self):
        """启动时自动升级 jmcomic 库"""
        try:
            loop = asyncio.get_event_loop()
            success, old_version, new_version, error = await loop.run_in_executor(
                None, update_jmcomic_package
            )
            if success:
                if old_version and new_version and old_version != new_version:
                    reload_jmcomic_modules()
                    logger.info(f"jmcomic 已自动更新: {old_version} -> {new_version}")
                else:
                    logger.info(f"jmcomic 已是最新版本: {new_version or old_version or 'unknown'}")
            else:
                logger.warning(f"jmcomic 自动更新失败: {error}")
        except Exception as e:
            logger.warning(f"jmcomic 自动更新异常: {e}")
    
    def _update_jmcomic_sync(self):
        """手动同步升级 jmcomic，返回用户可读的结果消息"""
        success, old_version, new_version, error = update_jmcomic_package()
        if not success:
            return f"jmcomic 更新失败: {error}"
        if old_version and new_version and old_version != new_version:
            reload_jmcomic_modules()
            return f"jmcomic 更新成功: {old_version} -> {new_version}"
        return f"jmcomic 已是最新版本: {new_version or old_version or 'unknown'}"
    
    async def _cleanup_old_files(self, force_all=False):
        """清理旧文件
        
        Args:
            force_all: 如果为True，删除所有文件；如果为False，只删除超过cleanup_days天的文件
        """
        try:
            if force_all:
                logger.info("开始清理所有文件...")
            else:
                logger.info("开始清理旧文件...")
            
            # 获取当前时间
            current_time = time.time()
            # 计算清理时间阈值
            cutoff_time = current_time - (self.cleanup_days * 24 * 60 * 60)
            
            # 获取下载目录
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            download_dir = os.path.join(plugin_dir, "downloads")
            
            if not os.path.exists(download_dir):
                logger.warning("下载目录不存在，跳过清理")
                return
            
            # 统计信息
            total_files = 0
            deleted_files = 0
            total_size = 0
            freed_size = 0
            
            # 遍历目录（含封面缓存 _covers）
            for root, dirs, files in os.walk(download_dir):
                for file in files:
                    total_files += 1
                    file_path = os.path.join(root, file)
                    
                    # 获取文件修改时间
                    try:
                        mtime = os.path.getmtime(file_path)
                        file_size = os.path.getsize(file_path)
                        total_size += file_size
                        
                        # 判断是否需要删除：force_all=True 删除所有，否则只删除超过阈值的
                        should_delete = force_all or (mtime < cutoff_time)
                        
                        if should_delete:
                            try:
                                os.remove(file_path)
                                deleted_files += 1
                                freed_size += file_size
                                logger.info(f"已删除文件: {file_path}")
                            except Exception as e:
                                logger.error(f"删除文件失败 {file_path}: {str(e)}")
                    except Exception as e:
                        logger.error(f"获取文件信息失败 {file_path}: {str(e)}")
            
            # 清理空目录
            for root, dirs, files in os.walk(download_dir, topdown=False):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        if not os.listdir(dir_path):  # 如果目录为空
                            os.rmdir(dir_path)
                            logger.info(f"已删除空目录: {dir_path}")
                    except Exception as e:
                        logger.error(f"删除空目录失败 {dir_path}: {str(e)}")
            
            # 转换文件大小为可读格式
            def format_size(size):
                for unit in ['B', 'KB', 'MB', 'GB']:
                    if size < 1024:
                        return f"{size:.2f}{unit}"
                    size /= 1024
                return f"{size:.2f}TB"
            
            # 记录清理结果
            result_msg = (
                f"清理完成:\n"
                f"- 总文件数: {total_files}\n"
                f"- 删除文件数: {deleted_files}\n"
                f"- 总空间: {format_size(total_size)}\n"
                f"- 释放空间: {format_size(freed_size)}"
            )
            
            if force_all:
                result_msg += f"\n- 清理模式: 全部清理"
            else:
                result_msg += f"\n- 清理模式: 仅清理 {self.cleanup_days} 天前的文件"
            
            logger.info(result_msg)
            
            # 返回清理结果供调用者使用
            return {
                'total_files': total_files,
                'deleted_files': deleted_files,
                'total_size': total_size,
                'freed_size': freed_size,
                'force_all': force_all
            }
            
        except Exception as e:
            logger.error(f"清理旧文件失败: {str(e)}")
            logger.error(traceback.format_exc())
    
    async def handle_jm_command(self, context: Context, event: AstrMessageEvent):
        """处理禁漫相关指令"""
        try:
            # 获取消息内容
            message_text = event.message_str if hasattr(event, 'message_str') else str(event)

            # 获取群号
            group_id = event.get_group_id() if hasattr(event, 'get_group_id') else None

            # 提取命令内容
            match = re.match(r"^jm\s+(.+)$", message_text)
            if not match:
                logger.warning(f"消息不匹配命令格式: {message_text}")
                return CommandResult().message("指令格式错误，请使用 jm <漫画ID> 或 jm download <漫画ID>")
            
            # 解析子命令
            command_args = match.group(1).split()
            if not command_args:
                return CommandResult().message("指令格式错误，请使用 jm <漫画ID> 或 jm download <漫画ID>")
            
            sub_cmd = command_args[0].lower()
            
            # 处理域名测试命令
            if sub_cmd == "domains":
                return await self.update_domains(event)
            
            # 处理清理命令
            elif sub_cmd == "cleanup":
                return await self.manual_cleanup(event)
            
            # 手动更新 jmcomic 库
            elif sub_cmd == "update":
                return await self.update_jmcomic_lib(event)

            # 仅查询详情，不下载
            elif sub_cmd == "info":
                if len(command_args) < 2:
                    return CommandResult().message("用法: jm info <漫画ID>")
                comic_id = parse_comic_id(command_args[1])
                if not comic_id:
                    return CommandResult().message("无法从输入中识别漫画ID")
                return await self.show_album_info(comic_id, event)

            # 搜索本子
            elif sub_cmd == "search":
                mode, query, page, err = parse_search_command(command_args)
                if err:
                    return CommandResult().message(err)
                return await self.search_comics(mode, query, page, event)
            
            # 处理下载命令
            elif sub_cmd == "download":
                if len(command_args) < 2:
                    return CommandResult().message("漫画ID不能为空")
                comic_id = parse_comic_id(command_args[1])
                if not comic_id:
                    return CommandResult().message("无法从输入中识别漫画ID")
                return await self.download_comic(comic_id, event)
            
            # 如果第一个参数是纯数字，直接当作漫画ID下载
            elif sub_cmd.isdigit() or parse_comic_id(sub_cmd):
                comic_id = parse_comic_id(sub_cmd) or sub_cmd
                return await self.download_comic(comic_id, event)
            
            # 未知命令
            else:
                return CommandResult().message(
                    f"未知命令: {sub_cmd}\n支持的命令:\n"
                    f"- jm <漫画ID> - 下载漫画\n"
                    f"- jm info <ID> - 仅查看详情\n"
                    f"- jm search tag|title <关键词> [页码] - 搜索\n"
                    f"- jm domains - 测试域名\n"
                    f"- jm update - 更新 jmcomic 库\n"
                    f"- jm cleanup - 清理旧文件"
                )
                
        except Exception as e:
            logger.error(f"处理命令异常: {str(e)}")
            logger.error(traceback.format_exc())
            return CommandResult().message(f"处理命令出错: {str(e)}")
    
    async def download_comic(self, comic_id: str, event: AstrMessageEvent):
        """下载漫画"""
        try:
            group_id = event.get_group_id() if hasattr(event, 'get_group_id') else None

            try:
                album_detail, client, album_title = await self._fetch_album(comic_id)
                if self.filter_r18g and album_has_r18g(album_detail):
                    return CommandResult().message(R18G_BLOCK_MESSAGE)

                await self._send_album_preview_split(event, album_detail, comic_id, client)

                task = asyncio.create_task(
                    self._download_comic_task(comic_id, event, group_id, album_title)
                )
                task.add_done_callback(self._handle_task_exception)

                return self._build_download_start_result(comic_id)

            except Exception as test_e:
                logger.error(f"连接测试失败: {str(test_e)}")
                return CommandResult().message(self._format_jm_error(str(test_e), comic_id))

        except Exception as e:
            logger.error(f"下载漫画任务异常: {str(e)}")
            logger.error(traceback.format_exc())
            return CommandResult().message("下载失败")
    
    async def manual_cleanup(self, event: AstrMessageEvent):
        """手动清理文件"""
        try:
            message = event
            if hasattr(message, 'reply'):
                await message.reply("开始清理所有下载文件...")
            
            # 执行清理，force_all=True 表示删除所有文件
            result = await self._cleanup_old_files(force_all=True)
            
            if result:
                # 转换文件大小为可读格式
                def format_size(size):
                    for unit in ['B', 'KB', 'MB', 'GB']:
                        if size < 1024:
                            return f"{size:.2f}{unit}"
                        size /= 1024
                    return f"{size:.2f}TB"
                
                result_msg = (
                    f"清理完成！\n"
                    f"删除了 {result['deleted_files']}/{result['total_files']} 个文件\n"
                    f"释放空间: {format_size(result['freed_size'])}"
                )
                
                if hasattr(message, 'reply'):
                    await message.reply(result_msg)
                
                return CommandResult().message(result_msg)
            else:
                if hasattr(message, 'reply'):
                    await message.reply("文件清理完成")
                return CommandResult().message("清理任务已完成")
        except Exception as e:
            logger.error(f"手动清理异常: {str(e)}")
            return CommandResult().message("清理失败")
    
    def _handle_task_exception(self, task):
        """处理异步任务异常的回调函数"""
        try:
            # 获取任务的异常（如果有的话）
            exception = task.exception()
            if exception:
                logger.error(f"异步任务异常: {str(exception)}")
                # 异常已经在任务内部处理并发送给用户了，这里只记录日志
        except Exception as e:
            logger.error(f"处理任务异常回调失败: {str(e)}")
    
    async def _download_comic_task(self, comic_id: str, event: AstrMessageEvent, group_id: str = None, album_title: str = None):
        """漫画下载任务"""
        message = event
        error_msg = None
        
        try:
            # 再次检查群聊状态（以防传入的group_id为None）
            if not group_id and hasattr(event, 'get_group_id'):
                group_id = event.get_group_id()
            
            # 使用插件目录下的downloads文件夹作为保存位置
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            download_dir = os.path.join(plugin_dir, "downloads")
            # 检查目录是否存在，不存在则创建
            if not os.path.exists(download_dir):
                os.makedirs(download_dir)
            
            # 检查PDF是否已经存在
            pdf_filename = f"jm_{comic_id}.pdf"
            pdf_path = os.path.join(download_dir, pdf_filename)
            
            if os.path.exists(pdf_path):
                # 获取bot对象
                bot = None
                if hasattr(message, 'bot'):
                    bot = message.bot
                elif hasattr(event, 'bot'):
                    bot = event.bot
                
                # 根据群聊状态发送消息
                if group_id:
                    # 群聊消息，简短回复并上传群文件
                    if bot:
                        # 上传文件
                        upload_success = await self._upload_to_group_file(
                            bot, 
                            group_id, 
                            pdf_path, 
                            f"jm_{comic_id}.pdf"
                        )
                        
                        if upload_success:
                            if message and hasattr(message, 'reply'):
                                await message.reply(f"漫画 {comic_id} 已上传到群文件，请查收！")
                        else:
                            if message and hasattr(message, 'reply'):
                                await message.reply(f"上传群文件失败，可能是文件太大或没有权限。")
                    
                    return f"漫画 {comic_id} 已下载完成"
                else:
                    # 私聊情况下直接发送文件
                    # 获取用户ID
                    user_id = event.get_sender_id()
                    
                    # 如果获取到了用户ID,尝试发送文件
                    if bot and user_id and pdf_path and os.path.exists(pdf_path):
                        try:
                            # 尝试发送文件
                            await bot.call_action(
                                action="upload_private_file",
                                user_id=user_id,
                                file=pdf_path,
                                name=f"jm_{comic_id}.pdf"
                            )
                            return f"漫画 {comic_id} 已发送，请查收"
                        except Exception as upload_err:
                            logger.error(f"发送文件失败: {str(upload_err)}")
                            
                            # 尝试发送普通消息而不是文件
                            try:
                                await bot.call_action(
                                    action="send_private_msg",
                                    user_id=user_id,
                                    message=f"漫画 {comic_id} 已下载完成，但无法发送文件。\n路径: {pdf_path}"
                                )
                                return f"漫画 {comic_id} 已下载完成，已通知用户"
                            except Exception as msg_err:
                                logger.error(f"发送普通消息也失败: {str(msg_err)}")
                    
                    # 如果无法发送,返回本地路径
                    return f"漫画 {comic_id} 已下载完成，文件路径: {pdf_path}"
            
            # 创建漫画下载目录
            save_dir = os.path.join(download_dir, f"{comic_id}")
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)
            
            # 漫画标题变量
            if not album_title:
                album_title = f"漫画_{comic_id}"
            
            try:
                # 使用配置文件创建option对象
                option = jmcomic.create_option_by_file(self.option_file)
                
                # 设置保存目录
                option.dir_rule = DirRule(
                    base_dir=save_dir,
                    rule='Bd'  # 直接使用根目录
                )
                
                # 若启动阶段未拿到标题，再尝试获取一次
                if album_title == f"漫画_{comic_id}":
                    try:
                        client = option.new_jm_client()
                        album_detail = client.get_album_detail(comic_id)
                        album_title = get_album_title(album_detail, comic_id)
                    except Exception as e:
                        logger.warning(f"获取漫画详情失败: {str(e)}")
                
                try:
                    # 使用线程池执行同步的下载任务，避免阻塞事件循环
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, jmcomic.download_album, comic_id, option)
                except Exception as e:
                    logger.error(f"使用jmcomic库下载失败: {str(e)}")
                    raise  # 重新抛出异常，让外层处理
                
                # 检查是否下载成功
                # 再次检查是否有图片文件
                final_image_files = []
                for root, dirs, files in os.walk(save_dir):
                    for ext in ['*.jpg', '*.jpeg', '*.png', '*.webp']:
                        final_image_files.extend(glob.glob(os.path.join(root, ext)))
                
                # 如果没有找到任何图片文件，说明下载失败
                if not final_image_files:
                    logger.warning(f"漫画 {comic_id} 下载失败，未找到任何图片文件")
                    raise Exception("下载失败：无法从禁漫网站获取图片，可能需要科学上网或配置代理。")
            except Exception as e:
                logger.error(f"下载失败: {str(e)}")
                error_msg = str(e)
                
                # 提供更友好的错误消息
                if "RegularNotMatchException" in error_msg:
                    error_msg = "禁漫网站结构已变更或域名失效，无法解析页面内容，请稍后再试"
                elif "403" in error_msg or "Forbidden" in error_msg:
                    error_msg = "无法访问禁漫网站，可能需要科学上网或网站域名已变更"
                elif "Connection" in error_msg:
                    error_msg = "连接禁漫网站失败，请检查网络连接"
                elif "请求重试全部失败" in error_msg or "RequestRetryAllFailException" in error_msg:
                    error_msg = "所有域名均不可用，请执行 jm domains 更新域名，或升级 jmcomic 后重试"
                elif "没有配置域名" in error_msg:
                    error_msg = "未配置可用域名，请执行 jm domains 自动获取，或将 client_impl 改为 api"
                elif "MissingAlbumPhotoException" in error_msg or "请求的本子不存在" in error_msg:
                    error_msg = f"漫画ID {comic_id} 不存在或已被删除\n请检查ID是否正确，或使用 jm download 命令下载漫画"
                
                raise Exception(f"下载漫画失败: {error_msg}")
            
            # 创建PDF文件
            pdf_path = await self._create_pdf(save_dir, album_title, comic_id)
            
            # 成功下载的消息处理
            if group_id:
                # 通知下载完成
                if message and hasattr(message, 'reply'):
                    await message.reply(f"漫画 {comic_id} 下载完成！")
                
                # 上传PDF到群文件
                if pdf_path and os.path.exists(pdf_path):
                    # 获取bot对象
                    bot = None
                    if hasattr(message, 'bot'):
                        bot = message.bot
                    elif hasattr(event, 'bot'):
                        bot = event.bot
                    
                    if bot:
                        # 上传文件
                        upload_success = await self._upload_to_group_file(
                            bot, 
                            group_id, 
                            pdf_path, 
                            f"jm_{comic_id}.pdf"
                        )
                        
                        if upload_success:
                            if message and hasattr(message, 'reply'):
                                await message.reply(f"漫画 {comic_id} 已上传到群文件，请查收！")
                        else:
                            if message and hasattr(message, 'reply'):
                                await message.reply(f"上传群文件失败，可能是文件太大或没有权限。")
                    else:
                        logger.warning("无法获取bot对象，无法上传群文件")
                else:
                    if message and hasattr(message, 'reply'):
                        await message.reply(f"创建PDF文件失败，请直接查看下载的图片。")
                
                # 群聊中只返回简单消息
                return f"漫画 {comic_id} 已下载完成"
            else:
                # 私聊情况下直接发送文件
                # 获取bot对象
                bot = None
                if hasattr(message, 'bot'):
                    bot = message.bot
                elif hasattr(event, 'bot'):
                    bot = event.bot
                
                # 获取用户ID
                user_id = event.get_sender_id()
                
                # 如果获取到了用户ID,尝试发送文件
                if bot and user_id and pdf_path and os.path.exists(pdf_path):
                    try:
                        # 尝试发送文件
                        await bot.call_action(
                            action="upload_private_file",
                            user_id=user_id,
                            file=pdf_path,
                            name=f"jm_{comic_id}.pdf"
                        )
                        return f"漫画 {comic_id} 已发送，请查收"
                    except Exception as upload_err:
                        logger.error(f"发送文件失败: {str(upload_err)}")
                        
                        # 尝试发送普通消息而不是文件
                        try:
                            await bot.call_action(
                                action="send_private_msg",
                                user_id=user_id,
                                message=f"漫画 {comic_id} 已下载完成，但无法发送文件。\n路径: {pdf_path}"
                            )
                            return f"漫画 {comic_id} 已下载完成，已通知用户"
                        except Exception as msg_err:
                            logger.error(f"发送普通消息也失败: {str(msg_err)}")
                else:
                    logger.error(f"无法获取用户ID或bot对象: user_id={user_id}, bot={bot}")
                
                # 如果无法发送,返回本地路径
                return f"漫画 {comic_id} 已下载完成，文件路径: {pdf_path}"
        except Exception as e:
            logger.error(f"下载失败: {str(e)}")
            error_msg = str(e)
            
            # 提供简洁的错误消息
            if "RegularNotMatchException" in error_msg:
                error_msg = "网站结构已变更，请稍后再试"
            elif "403" in error_msg or "Forbidden" in error_msg or "ip地区禁止访问" in error_msg:
                error_msg = "访问被拒绝，请使用代理"
            elif "Connection" in error_msg:
                error_msg = "网络连接失败"
            elif "not found client impl class" in error_msg:
                error_msg = "客户端配置错误"
            elif "MissingAlbumPhotoException" in error_msg or "请求的本子不存在" in error_msg:
                error_msg = f"漫画ID {comic_id} 不存在"
            elif "下载失败：无法从禁漫网站获取图片" in error_msg:
                error_msg = "无法获取图片，请使用代理"
            
            # 尝试向用户发送错误反馈
            try:
                if hasattr(message, 'reply'):
                    await message.reply(f"下载漫画 {comic_id} 失败：{error_msg}")
            except Exception as reply_err:
                logger.error(f"发送错误反馈失败: {str(reply_err)}")
            
            # 不再重新抛出异常，因为错误已经发送给用户了
            # 这样可以避免 "Task exception was never retrieved" 的警告
            return None
    
    async def _create_pdf(self, comic_dir, album_title, comic_id):
        """创建PDF文件并上传到群文件"""
        try:
            logger.info(f"开始创建漫画 {comic_id} 的PDF文件，目录: {comic_dir}")
            
            # 检查目录是否存在
            if not os.path.exists(comic_dir) or not os.path.isdir(comic_dir):
                logger.error(f"漫画目录不存在: {comic_dir}")
                raise Exception(f"漫画目录不存在: {comic_dir}")
            
            # 列出目录内容
            items = os.listdir(comic_dir)
            logger.info(f"目录内容({len(items)}个): {items[:10] if len(items) > 10 else items}")
                
            # 获取所有图片文件
            image_files = []
            for root, dirs, files in os.walk(comic_dir):
                logger.info(f"搜索目录: {root}, 包含 {len(files)} 个文件")
                for file in files:
                    if file.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        image_files.append(os.path.join(root, file))
                
            if not image_files:
                logger.error(f"目录中没有找到图片文件: {comic_dir}")
                
                # 尝试查找子目录
                subdirs = [d for d in os.listdir(comic_dir) if os.path.isdir(os.path.join(comic_dir, d))]
                logger.info(f"检查子目录: {subdirs}")
                
                # 尝试遍历一层子目录查找图片
                for subdir in subdirs:
                    subdir_path = os.path.join(comic_dir, subdir)
                    logger.info(f"检查子目录: {subdir_path}")
                    if os.path.exists(subdir_path):
                        sub_files = os.listdir(subdir_path)
                        logger.info(f"子目录 {subdir} 包含 {len(sub_files)} 个文件")
                        for file in sub_files:
                            if file.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                                image_files.append(os.path.join(subdir_path, file))
                
                if not image_files:
                    logger.error("在子目录中也没有找到图片文件")
                    raise Exception("目录中没有找到图片文件")
                
            # 按文件名排序
            image_files.sort()
            
            logger.info(f"找到 {len(image_files)} 张图片，开始转换PDF")
            logger.info(f"图片示例: {image_files[:3] if len(image_files) > 3 else image_files}")
            
            # 图片压缩参数（从配置读取）
            compress_quality = self.compress_quality
            max_dimension = self.max_image_dimension
            
            # 准备PDF文件路径 - 使用插件目录下的downloads文件夹
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            download_dir = os.path.join(plugin_dir, "downloads")
            pdf_filename = f"jm_{comic_id}.pdf"
            pdf_path = os.path.join(download_dir, pdf_filename)
            
            # 转换为PDF
            try:
                # 预处理：压缩图片，创建临时图片文件
                processed_image_files = []
                temp_dir = os.path.join(download_dir, f"temp_{comic_id}")
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)
                
                for i, image_file in enumerate(image_files):
                    try:
                        # 打开图片
                        img = Image.open(image_file)
                        
                        # 如果图片过大，进行缩放
                        width, height = img.size
                        if width > max_dimension or height > max_dimension:
                            # 计算比例
                            ratio = min(max_dimension/width, max_dimension/height)
                            new_width = int(width * ratio)
                            new_height = int(height * ratio)
                            # 缩放图片
                            img = img.resize((new_width, new_height), Image.LANCZOS)
                        
                        # 确保为RGB模式，JPEG需要
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        
                        # 保存为临时文件
                        temp_file = os.path.join(temp_dir, f"temp_{i:04d}.jpg")
                        img.save(temp_file, "JPEG", quality=compress_quality, optimize=True)
                        processed_image_files.append(temp_file)
                    except Exception as img_err:
                        logger.error(f"处理图片失败 {image_file}: {str(img_err)}")
                        # 如果处理失败，尝试使用原图
                        processed_image_files.append(image_file)
                
                # 尝试使用img2pdf库转换（保持图片质量）
                try:
                    with open(pdf_path, "wb") as f:
                        f.write(img2pdf.convert(processed_image_files))
                    logger.info(f"使用img2pdf成功创建压缩PDF文件: {pdf_path}")
                except Exception as img2pdf_err:
                    logger.error(f"使用img2pdf创建PDF失败: {str(img2pdf_err)}")
                    # 备用方案：使用PIL库转换
                    images = []
                    for image_file in processed_image_files:
                        try:
                            img = Image.open(image_file)
                            # 确保为RGB模式
                            if img.mode != 'RGB':
                                img = img.convert('RGB')
                            images.append(img)
                        except Exception as img_err:
                            logger.error(f"处理图片失败 {image_file}: {str(img_err)}")
                    
                    if images:
                        images[0].save(
                            pdf_path, "PDF", resolution=100.0, 
                            save_all=True, append_images=images[1:]
                        )
                        logger.info(f"使用PIL成功创建PDF文件: {pdf_path}")
                    else:
                        logger.error("没有有效图片可以转换为PDF")
                        return None
                
                # 清理临时文件
                try:
                    for temp_file in processed_image_files:
                        if os.path.exists(temp_file) and temp_file.startswith(temp_dir):
                            os.remove(temp_file)
                    if os.path.exists(temp_dir):
                        os.rmdir(temp_dir)
                except Exception as clean_err:
                    logger.warning(f"清理临时文件失败: {str(clean_err)}")
                
            except Exception as e:
                logger.error(f"创建PDF失败: {str(e)}")
                try:
                    # 如果压缩处理失败，尝试使用原始图片直接创建
                    with open(pdf_path, "wb") as f:
                        f.write(img2pdf.convert(image_files))
                    logger.info(f"使用原始图片成功创建PDF文件: {pdf_path}")
                except Exception as img2pdf_err:
                    logger.error(f"使用img2pdf创建PDF失败: {str(img2pdf_err)}")
                    # 最后的备用方案：使用PIL库转换
                    try:
                        images = []
                        for image_file in image_files:
                            try:
                                img = Image.open(image_file)
                                # 如果是非RGB模式，转换为RGB
                                if img.mode != 'RGB':
                                    img = img.convert('RGB')
                                images.append(img)
                            except Exception as img_err:
                                logger.error(f"处理图片失败 {image_file}: {str(img_err)}")
                        
                        if images:
                            images[0].save(
                                pdf_path, "PDF", resolution=100.0, 
                                save_all=True, append_images=images[1:]
                            )
                            logger.info(f"使用PIL成功创建PDF文件: {pdf_path}")
                        else:
                            logger.error("没有有效图片可以转换为PDF")
                            raise Exception("没有有效图片可以转换为PDF")
                    except Exception as pil_err:
                        logger.error(f"使用PIL创建PDF失败: {str(pil_err)}")
                        raise Exception(f"使用PIL创建PDF失败: {str(pil_err)}")
            
            return pdf_path
        except Exception as e:
            logger.error(f"创建PDF异常: {str(e)}")
            logger.error(traceback.format_exc())
            # 重新抛出异常，确保调用方能收到错误信息
            raise Exception(f"创建PDF失败: {str(e)}")

    async def _upload_to_group_file(self, bot, group_id: str, file_path: str, filename: str):
        """上传文件到群文件"""
        try:
            logger.info(f"开始上传文件到群 {group_id}: {file_path}")
            
            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.error(f"文件不存在: {file_path}")
                return False
            
            # Docker环境下的路径处理
            upload_file_path = file_path
            
            # 检测Docker环境并转换路径
            if os.path.exists('/.dockerenv'):
                logger.info("检测到Docker环境，尝试转换文件路径")
                
                # 方案1: 尝试使用相对于工作目录的路径
                try:
                    # 获取当前工作目录
                    current_dir = os.getcwd()
                    if file_path.startswith(current_dir):
                        # 转换为相对路径
                        relative_path = os.path.relpath(file_path, current_dir)
                        upload_file_path = relative_path
                        logger.info(f"Docker环境路径转换: {file_path} -> {upload_file_path}")
                except Exception as path_err:
                    logger.warning(f"路径转换失败: {str(path_err)}")
            
            # Docker环境下优先使用base64上传，因为路径问题较多
            if os.path.exists('/.dockerenv'):
                # 检查文件大小
                file_size = os.path.getsize(file_path)
                file_size_mb = file_size / (1024 * 1024)
                
                logger.info(f"Docker环境检测到，文件大小: {file_size_mb:.2f}MB，优先尝试base64上传")
                
                # 对于超大文件给出警告但仍然尝试
                if file_size_mb > 50:
                    logger.warning(f"文件较大({file_size_mb:.2f}MB)，base64上传可能较慢")
                
                try:
                    import base64
                    
                    with open(file_path, 'rb') as f:
                        file_data = f.read()
                        file_base64 = base64.b64encode(file_data).decode('utf-8')
                    
                    result = await bot.call_action(
                        action="upload_group_file",
                        group_id=group_id,
                        file=f"base64://{file_base64}",
                        name=filename
                    )
                    
                    logger.info(f"Docker环境base64上传成功，结果: {result}")
                    return True
                    
                except Exception as base64_err:
                    logger.warning(f"Docker环境base64上传失败，尝试其他方法: {str(base64_err)}")
            
            # 尝试其他上传方式（非Docker环境或base64失败时）
            upload_methods = [
                # 方法1: 使用原始路径
                {"file": file_path, "name": filename},
                # 方法2: 使用转换后的路径（Docker环境）
                {"file": upload_file_path, "name": filename} if upload_file_path != file_path else None,
                # 方法3: 使用file://协议
                {"file": f"file://{file_path}", "name": filename},
            ]
            
            # 过滤None值
            upload_methods = [method for method in upload_methods if method is not None]
            
            for i, method in enumerate(upload_methods):
                try:
                    logger.info(f"尝试上传方法 {i+1}: {method}")
                    
                    result = await bot.call_action(
                        action="upload_group_file",
                        group_id=group_id,
                        **method
                    )
                    
                    logger.info(f"上传成功，结果: {result}")
                    return True
                    
                except Exception as method_err:
                    logger.warning(f"上传方法 {i+1} 失败: {str(method_err)}")
                    continue
            
            # 如果前面的方法都失败，最后尝试base64编码上传（非Docker环境）
            if not os.path.exists('/.dockerenv'):
                try:
                    logger.info("最后尝试使用base64编码上传文件")
                    import base64
                    
                    with open(file_path, 'rb') as f:
                        file_data = f.read()
                        file_base64 = base64.b64encode(file_data).decode('utf-8')
                    
                    result = await bot.call_action(
                        action="upload_group_file",
                        group_id=group_id,
                        file=f"base64://{file_base64}",
                        name=filename
                    )
                    
                    logger.info(f"base64上传成功，结果: {result}")
                    return True
                    
                except Exception as base64_err:
                    logger.error(f"base64上传也失败: {str(base64_err)}")
            
            logger.error("所有上传方法都失败了")
            return False
            
        except Exception as e:
            logger.error(f"上传文件到群失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False
    
    async def update_jmcomic_lib(self, event: AstrMessageEvent):
        """手动更新 jmcomic 库"""
        try:
            message = event
            if hasattr(message, 'reply'):
                await message.reply("正在更新 jmcomic 库，请稍候...")
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._update_jmcomic_sync)
            return CommandResult().message(result)
        except Exception as e:
            logger.error(f"更新 jmcomic 异常: {str(e)}")
            return CommandResult().message(f"更新 jmcomic 失败: {str(e)}")
    
    async def update_domains(self, event: AstrMessageEvent):
        """测试并更新可用域名"""
        try:
            # 获取消息对象
            message = None
            if hasattr(event, 'message'):
                message = event.message
            elif hasattr(event, 'event'):
                message = event.event

            # 发送开始测试提示
            if message and hasattr(message, 'reply'):
                await message.reply("开始测试禁漫天堂可用域名，请耐心等待...")

            # 创建后台测试任务，不等待完成
            asyncio.create_task(self._test_domains_task_with_notification(event))

            # 立即返回，不等待测试完成
            return CommandResult().message("域名测试任务已启动，将在后台进行测试...")
        except Exception as e:
            logger.error(f"测试域名异常: {str(e)}")
            logger.error(traceback.format_exc())
            return CommandResult().message(f"测试域名失败: {str(e)}")
    
    async def _test_domains_task_with_notification(self, event: AstrMessageEvent):
        """测试域名任务并发送通知"""
        try:
            # 执行域名测试
            result = await self._test_domains_task()

            # 发送测试结果通知
            message = event
            if hasattr(message, 'reply'):
                await message.reply(result)

        except Exception as e:
            logger.error(f"域名测试任务异常: {str(e)}")
            # 发送错误通知
            message = event
            if hasattr(message, 'reply'):
                await message.reply(f"域名测试失败: {str(e)}")

    async def _test_domains_task(self):
        """测试域名任务"""
        try:
            logger.info("开始测试禁漫天堂可用域名")
            
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'domain_tester.py')
            
            try:
                spec = importlib.util.spec_from_file_location("domain_tester", script_path)
                tester = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tester)
                
                api_msg = ""
                if hasattr(tester, 'test_api_domain'):
                    api_ok, api_domains = tester.test_api_domain()
                    if api_ok:
                        api_msg = "API 客户端可用，当前域名:\n" + "\n".join([f"- {d}" for d in api_domains])
                    else:
                        api_msg = "API 客户端不可用，请检查代理或升级 jmcomic (pip install jmcomic -U)"
                
                if not hasattr(tester, 'get_all_domain') or not hasattr(tester, 'test_all_domains'):
                    logger.warning("domain_tester模块格式不符合预期")
                    return api_msg or "域名测试模块异常"
                
                domains = tester.get_all_domain()
                logger.info(f"获取到 {len(domains)} 个网页域名，开始测试")
                domain_status_dict = tester.test_all_domains(domains)
                available_domains = [domain for domain, status in domain_status_dict.items() if status == 'ok']
                
            except Exception as e:
                logger.error(f"导入domain_tester模块失败: {str(e)}")
                return f"域名测试失败: {str(e)}"
            
            html_msg = ""
            if available_domains and self.client_impl == "html":
                try:
                    with open(self.option_file, 'r', encoding='utf-8') as f:
                        config_data = yaml.safe_load(f)
                    
                    if 'client' in config_data:
                        config_data['client']['domain'] = available_domains
                    
                    if self.use_proxy and 'client' in config_data and 'postman' in config_data['client']:
                        proxy_host = get_proxy_host()
                        proxy_match = re.match(r'(https?://)([^:]+):(\d+)', self.proxy_address or '')
                        if proxy_match:
                            adjusted_proxy = f"{proxy_match.group(1)}{proxy_host}:{proxy_match.group(3)}"
                            config_data['client']['postman']['meta_data']['proxies'] = {
                                'http': adjusted_proxy,
                                'https': adjusted_proxy
                            }
                    
                    with open(self.option_file, 'w', encoding='utf-8') as f:
                        yaml.dump(config_data, f, allow_unicode=True)
                    
                    logger.info(f"更新配置文件成功，添加 {len(available_domains)} 个可用网页域名")
                except Exception as e:
                    return f"已找到 {len(available_domains)} 个可用网页域名，但更新配置文件失败: {str(e)}"
                
                html_msg = "可用网页域名：\n" + "\n".join([f"- {d}" for d in available_domains])
            elif available_domains:
                html_msg = "可用网页域名（当前使用 API 客户端，仅供参考）：\n" + "\n".join([f"- {d}" for d in available_domains])
            else:
                html_msg = "未找到可用网页域名（API 客户端通常不受影响）"
            
            parts = [p for p in [api_msg, html_msg] if p]
            return "域名测试完成\n\n" + "\n\n".join(parts)
            
        except Exception as e:
            logger.error(f"测试域名任务异常: {str(e)}")
            logger.error(traceback.format_exc())
            return f"测试域名失败: {str(e)}"

    def _update_domains_in_config(self, domains):
        """更新配置文件中的域名设置"""
        try:
            # 读取配置文件
            with open(self.option_file, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
            
            # 更新域名列表，把用户自定义的放在最前面
            if 'client' in config_data:
                if 'domain' not in config_data['client']:
                    config_data['client']['domain'] = []
                
                # 检查是否需要更新
                existing_domains = config_data['client']['domain']
                
                # 将用户自定义域名放在最前面
                updated_domains = domains.copy()
                
                # 添加现有域名（避免重复）
                for domain in existing_domains:
                    if domain not in updated_domains:
                        updated_domains.append(domain)
                
                config_data['client']['domain'] = updated_domains
                
                # 同时更新site_url，使用第一个域名
                if domains and 'site_url' in config_data['client']:
                    config_data['client']['site_url'] = f"https://{domains[0]}"
            
            # 确保代理配置不被覆盖 - 重新应用代理设置
            if 'client' in config_data and 'postman' in config_data['client'] and 'meta_data' in config_data['client']['postman']:
                if self.use_proxy:
                    # 检测Docker环境并调整代理地址
                    proxy_host = get_proxy_host()
                    
                    if self.proxy_address:
                        # 解析用户配置的代理地址，替换主机部分
                        proxy_match = re.match(r'(https?://)([^:]+):(\d+)', self.proxy_address)
                        if proxy_match:
                            protocol = proxy_match.group(1)
                            port = proxy_match.group(3)
                            # 使用检测到的代理主机
                            adjusted_proxy = f"{protocol}{proxy_host}:{port}"
                            config_data['client']['postman']['meta_data']['proxies'] = {
                                'http': adjusted_proxy,
                                'https': adjusted_proxy
                            }
                        else:
                            # 如果格式不匹配，直接使用原地址
                            config_data['client']['postman']['meta_data']['proxies'] = {
                                'http': self.proxy_address,
                                'https': self.proxy_address
                            }
                    else:
                        # 使用自动检测的代理配置
                        default_proxy = get_default_proxy_config(self.proxy_port)
                        config_data['client']['postman']['meta_data']['proxies'] = default_proxy
            
            # 写回配置文件
            with open(self.option_file, 'w', encoding='utf-8') as f:
                yaml.dump(config_data, f, allow_unicode=True)
            
            logger.info(f"更新域名配置成功，配置了 {len(domains)} 个自定义域名")
        except Exception as e:
            logger.error(f"更新域名配置失败: {str(e)}")

    async def _start_cleanup_task(self):
        """启动自动清理任务"""
        try:
            while True:
                # 获取当前时间
                now = time.localtime()
                # 每天凌晨2点执行清理
                if now.tm_hour == 2 and now.tm_min == 0:
                    await self._cleanup_old_files()
                
                # 等待1分钟
                await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"自动清理任务异常: {str(e)}")
            logger.error(traceback.format_exc())
