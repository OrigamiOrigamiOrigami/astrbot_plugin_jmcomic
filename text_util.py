import re

# jm / JM + 空格子命令，或 jm123456 / JM123456 无空格 ID
JM_COMMAND_PATTERN = re.compile(r"^[jJ][mM](?:\s+(.+)|(\d+))\s*$")

R18G_TAG_PATTERN = re.compile(r'r[\s\-_]?18[\s\-_]?g', re.I)
R18G_BLOCK_MESSAGE = "该内容为 R-18G，已拒绝展示或下载"

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

# 添加常用User-Agents列表
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0'
]


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


def parse_jm_command_args(message_text: str):
    """
    解析 jm/JM 指令参数。
    支持: jm 123456 / JM 123456 / jm123456 / JM123456 / jm info ...
    返回 command_args 列表，不匹配则返回 None。
    """
    if not message_text:
        return None
    match = JM_COMMAND_PATTERN.match(message_text.strip())
    if not match:
        return None
    body = match.group(1) if match.group(1) is not None else match.group(2)
    if not body:
        return None
    return body.split()


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
        from .album import resolve_album_page_count
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
