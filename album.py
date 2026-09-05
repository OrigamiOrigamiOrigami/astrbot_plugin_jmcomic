import logging
import os

from . import deps
from .text_util import format_album_info, get_album_title

logger = logging.getLogger("astrbot")


def resolve_album_page_count(album_detail, client=None, stop_at: int = None) -> int:
    """
    获取本子总页数。
    jmcomic API 客户端会把 page_count 硬编码为 0，需从各章节实际页数汇总。
    stop_at: 若提供，累计超过该值后提前返回（用于下载上限快速判定）。
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
            if stop_at is not None and total > stop_at:
                return total
        except Exception as e:
            logger.warning(f"统计章节 {pid} 页数失败: {e}")
    return total


def download_album_cover(client, comic_id: str, save_dir: str):
    """下载本子封面缩略图（CDN albums 图，通常约 400px）"""
    try:
        os.makedirs(save_dir, exist_ok=True)
        cover_path = os.path.join(save_dir, f"cover_{comic_id}.jpg")
        # _3x4 约 400x533；无后缀多为 400x400。都不大，选面积更大的
        best_path = None
        best_area = 0
        for size in ('_3x4', ''):
            try:
                client.download_album_cover(comic_id, cover_path, size=size)
                if os.path.exists(cover_path) and os.path.getsize(cover_path) > 0:
                    with deps.Image.open(cover_path) as img:
                        w, h = img.size
                    area = w * h
                    logger.info(f"封面缩略图 size={size or 'default'}: {w}x{h}")
                    if area > best_area:
                        best_area = area
                        best_path = cover_path
                        if size == '_3x4':
                            # 先拿到 _3x4；若 default 更大后面会覆盖
                            pass
            except Exception as e:
                logger.warning(f"下载封面失败 size={size or 'default'}: {e}")
        return best_path
    except Exception as e:
        logger.warning(f"下载封面失败: {e}")
    return None


def download_preview_cover(client, comic_id: str, save_dir: str, album_detail=None):
    """
    预览卡高清图：CDN albums 封面只有约 400px，改用第一章首页原图。
    失败则回退缩略图。
    """
    try:
        os.makedirs(save_dir, exist_ok=True)
        hires_path = os.path.join(save_dir, f"cover_hires_{comic_id}.jpg")

        if album_detail is None:
            album_detail = client.get_album_detail(comic_id)

        photo_id = None
        episode_list = getattr(album_detail, "episode_list", None) or []
        if episode_list:
            photo_id = str(episode_list[0][0])
        else:
            photo_id = str(getattr(album_detail, "album_id", comic_id))

        photo = client.get_photo_detail(photo_id, fetch_album=False, fetch_scramble_id=True)
        if photo is None or len(photo) == 0:
            raise Exception("章节无图片")

        first_image = photo[0]
        client.download_by_image_detail(first_image, hires_path, decode_image=True)
        if not (os.path.exists(hires_path) and os.path.getsize(hires_path) > 0):
            raise Exception("首页文件为空")

        with deps.Image.open(hires_path) as img:
            w, h = img.size
            # 竖图过高时取顶部 3:4，更像封面且保留高像素
            target_h = int(w * 4 / 3)
            if h > target_h > 0:
                img = img.crop((0, 0, w, target_h))
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(hires_path, "JPEG", quality=92, optimize=True)
                w, h = img.size

        logger.info(f"预览高清封面(第一章首页): {w}x{h}")
        return hires_path
    except Exception as e:
        logger.warning(f"高清封面下载失败，回退缩略图: {e}")
        return download_album_cover(client, comic_id, save_dir)


def rotate_image_180(image_path: str) -> str:
    """将图片旋转180度并保存为新文件"""
    root, ext = os.path.splitext(image_path)
    rotated_path = f"{root}_rot180{ext or '.jpg'}"
    with deps.Image.open(image_path) as img:
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.rotate(180, expand=True).save(rotated_path, "JPEG", quality=90)
    return rotated_path


def fetch_album_detail_sync(option_file: str, comic_id: str):
    """在线程池中获取本子详情，避免阻塞事件循环"""
    option = deps.jmcomic.create_option_by_file(option_file)
    client = option.new_jm_client()
    album_detail = client.get_album_detail(comic_id)
    return album_detail, client, get_album_title(album_detail, comic_id)


def prepare_album_preview_sync(cover_dir: str, comic_id: str, album_detail, client):
    """在线程池中准备简介文本、封面路径与页数"""
    page_count = resolve_album_page_count(album_detail, client)
    info_text = format_album_info(album_detail, comic_id, page_count=page_count)
    # 预览卡用第一章首页高清图（albums CDN 只有约 400px）
    cover_path = download_preview_cover(client, comic_id, cover_dir, album_detail)
    return info_text, cover_path, page_count


def search_albums_sync(option_file: str, mode: str, query: str, page: int = 1):
    """在线程池中按标签或标题搜索本子"""
    option = deps.jmcomic.create_option_by_file(option_file)
    client = option.new_jm_client()
    if mode == 'tag':
        return client.search_tag(query, page=page)
    return client.search_work(query, page=page)
