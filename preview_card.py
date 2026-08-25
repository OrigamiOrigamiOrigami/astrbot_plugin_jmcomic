"""用 AstrBot HtmlRenderer 合成 JM 封面+简介预览卡。"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

logger = logging.getLogger("astrbot")

# 竖卡：封面置顶 + 底部渐变叠字 + 下方浅色信息区（参考 astrbot_plugin_parser）
# html/body 与卡片同色、height:auto，减轻 t2i full_page 大块白边
_ALBUM_CARD_TMPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: 900px;
    height: auto;
    margin: 0;
    padding: 0;
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    background: #111111;
    color: #1a1a1a;
  }
  .card {
    width: 900px;
    background: #111111;
    overflow: hidden;
    display: inline-block;
    vertical-align: top;
  }
  .hero {
    position: relative;
    width: 100%;
    background: #111111;
    line-height: 0;
    font-size: 0;
  }
  .hero img {
    display: block;
    width: 100%;
    height: auto;
    max-height: 1200px;
    object-fit: cover;
    object-position: center top;
  }
  .hero-fade {
    position: absolute;
    left: 0; right: 0; bottom: 0;
    padding: 48px 18px 14px;
    background: linear-gradient(to top, rgba(0,0,0,0.88) 0%, rgba(0,0,0,0.45) 55%, rgba(0,0,0,0) 100%);
    color: #fff;
    line-height: 1.35;
    font-size: 16px;
  }
  .badge-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  .jm-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 15px;
    font-weight: 700;
    color: #fff;
    background: #e85d4c;
  }
  .author {
    font-size: 17px;
    color: rgba(255,255,255,0.9);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title {
    font-size: 26px;
    font-weight: 700;
    line-height: 1.3;
    word-break: break-word;
    text-shadow: 0 1px 3px rgba(0,0,0,0.35);
  }
  .body {
    padding: 16px 18px 18px;
    background: #f0f0f2;
    color: #222;
    line-height: 1.4;
    font-size: 15px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 10px;
  }
  .chips:last-child { margin-bottom: 0; }
  .chip {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    background: #e4e4e8;
    color: #333;
    font-size: 14px;
    font-weight: 500;
  }
  .chip.meta {
    background: #ffe8e4;
    color: #b33a2b;
  }
  .desc {
    font-size: 15px;
    line-height: 1.55;
    color: #444;
    white-space: pre-wrap;
    word-break: break-word;
    margin-top: 4px;
  }
  .text-only {
    padding: 18px 20px;
    background: #f0f0f2;
  }
  .text-only .jm-pill { margin-bottom: 0; }
  .text-only .author { color: #666; }
  .text-only .title {
    color: #1a1a1a;
    text-shadow: none;
    margin-top: 8px;
  }
</style>
</head>
<body>
  <div class="card">
    {% if cover_data_uri %}
    <div class="hero">
      <img src="{{ cover_data_uri }}" alt="cover"/>
      <div class="hero-fade">
        <div class="badge-row">
          <span class="jm-pill">JM{{ album_id }}</span>
          {% if author %}<span class="author">{{ author }}</span>{% endif %}
        </div>
        {% if title %}<div class="title">{{ title }}</div>{% endif %}
      </div>
    </div>
    {% if meta_chips or tag_chips or description %}
    <div class="body">
      {% if meta_chips %}
      <div class="chips">
        {% for chip in meta_chips %}<span class="chip meta">{{ chip }}</span>{% endfor %}
      </div>
      {% endif %}
      {% if tag_chips %}
      <div class="chips">
        {% for chip in tag_chips %}<span class="chip">{{ chip }}</span>{% endfor %}
      </div>
      {% endif %}
      {% if description %}<div class="desc">{{ description }}</div>{% endif %}
    </div>
    {% endif %}
    {% else %}
    <div class="text-only">
      <div class="badge-row">
        <span class="jm-pill">JM{{ album_id }}</span>
        {% if author %}<span class="author">{{ author }}</span>{% endif %}
      </div>
      {% if title %}<div class="title">{{ title }}</div>{% endif %}
      {% if meta_chips or tag_chips %}
      <div class="chips" style="margin-top:12px;">
        {% for chip in meta_chips %}<span class="chip meta">{{ chip }}</span>{% endfor %}
        {% for chip in tag_chips %}<span class="chip">{{ chip }}</span>{% endfor %}
      </div>
      {% endif %}
      {% if description %}<div class="desc" style="margin-top:10px;">{{ description }}</div>{% endif %}
    </div>
    {% endif %}
  </div>
</body>
</html>
"""


def _truncate_list(items, limit=8) -> list[str]:
    items = [str(i).strip() for i in (items or []) if i]
    if len(items) <= limit:
        return items
    return items[:limit]


def cover_to_data_uri(cover_path: str | Path | None, max_width: int = 1080) -> str | None:
    """封面转 data URI；过大则缩小，减轻 t2i 负载。默认保留更高像素减轻发糊。"""
    if not cover_path:
        return None
    path = Path(cover_path)
    if not path.is_file():
        return None
    try:
        with Image.open(path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            elif img.mode == "RGBA":
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize(
                    (max_width, max(1, int(img.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            buf = BytesIO()
            # 质量提高，减少二次 JPEG 发糊
            img.save(buf, format="JPEG", quality=92, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.warning(f"封面转 data URI 失败: {e}")
        return None


def trim_canvas(
    image_path: str | Path,
    light_threshold: int = 250,
    dark_threshold: int = 40,
) -> str:
    """
    裁掉 t2i full_page 留下的近白边 / 近黑边（html 背景 #111 时右侧和底部常是大块黑边）。
    浅灰信息区 #f0f0f2≈240 不会被当成空白；封面里零星暗色也不会整行/整列被裁。
    """
    path = Path(image_path)
    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            pixels = rgb.load()

            def is_blank_pixel(x: int, y: int) -> bool:
                r, g, b = pixels[x, y]
                if r >= light_threshold and g >= light_threshold and b >= light_threshold:
                    return True
                # 画布残留的近黑（含 JPEG 压缩后的 #111）
                if r <= dark_threshold and g <= dark_threshold and b <= dark_threshold:
                    return True
                return False

            def row_blank(y: int) -> bool:
                blank = sum(1 for x in range(w) if is_blank_pixel(x, y))
                return blank / w >= 0.995

            def col_blank(x: int) -> bool:
                blank = sum(1 for y in range(h) if is_blank_pixel(x, y))
                return blank / h >= 0.995

            top = 0
            while top < h and row_blank(top):
                top += 1
            bottom = h - 1
            while bottom > top and row_blank(bottom):
                bottom -= 1
            left = 0
            while left < w and col_blank(left):
                left += 1
            right = w - 1
            while right > left and col_blank(right):
                right -= 1

            if top == 0 and left == 0 and bottom == h - 1 and right == w - 1:
                return str(path)
            if top >= bottom or left >= right:
                return str(path)

            cropped = rgb.crop((left, top, right + 1, bottom + 1))
            out = path.with_name(f"jm_card_{uuid.uuid4().hex}.jpg")
            cropped.save(out, format="JPEG", quality=92, optimize=True)
            logger.info(
                f"预览卡裁边: {w}x{h} -> {cropped.size[0]}x{cropped.size[1]} "
                f"(l={left},t={top},r={right},b={bottom})"
            )
            return str(out)
    except Exception as e:
        logger.warning(f"裁剪预览卡白边失败: {e}")
        return str(path)


def build_album_card_data(
    album_detail,
    comic_id: str,
    cover_path: str | None = None,
    page_count: int = 0,
    max_desc_len: int = 180,
) -> dict:
    """从本子详情组装 HtmlRenderer 模板数据。"""
    album_id = str(getattr(album_detail, "album_id", None) or comic_id)
    title = None
    for attr in ("name", "album_name", "title"):
        value = getattr(album_detail, attr, None)
        if value:
            title = str(value).strip()
            break
    if not title:
        title = f"漫画_{comic_id}"

    authors = getattr(album_detail, "authors", None) or []
    author = " / ".join(_truncate_list(authors, 3)) if authors else None

    meta_chips = []
    if page_count and int(page_count) > 0:
        meta_chips.append(f"{int(page_count)} 页")
    episode_list = getattr(album_detail, "episode_list", None) or []
    if episode_list and len(episode_list) > 1:
        meta_chips.append(f"{len(episode_list)} 章")
    views = getattr(album_detail, "views", None)
    if views:
        meta_chips.append(f"浏览 {views}")
    likes = getattr(album_detail, "likes", None)
    if likes:
        meta_chips.append(f"喜欢 {likes}")

    tags = getattr(album_detail, "tags", None) or []
    tag_chips = _truncate_list(tags, 10)

    description = (getattr(album_detail, "description", None) or "").strip()
    if description and len(description) > max_desc_len:
        description = description[:max_desc_len] + "..."

    return {
        "album_id": album_id,
        "title": title,
        "author": author,
        "meta_chips": meta_chips,
        "tag_chips": tag_chips,
        "description": description or None,
        "cover_data_uri": cover_to_data_uri(cover_path) if cover_path else None,
    }


async def render_album_preview_card(
    album_detail,
    comic_id: str,
    cover_path: str | None = None,
    page_count: int = 0,
    max_desc_len: int = 180,
) -> str | None:
    """
    用 AstrBot HtmlRenderer 合成预览卡，成功返回本地图片路径，失败返回 None。
    """
    try:
        from astrbot.api import html_renderer
    except Exception as e:
        logger.warning(f"无法导入 html_renderer: {e}")
        return None

    try:
        tmpl_data = await asyncio.to_thread(
            build_album_card_data,
            album_detail,
            comic_id,
            cover_path,
            page_count,
            max_desc_len,
        )
        if not any(
            tmpl_data.get(k)
            for k in ("title", "cover_data_uri", "meta_chips", "tag_chips", "description")
        ):
            return None

        rendered = await html_renderer.render_custom_template(
            _ALBUM_CARD_TMPL,
            tmpl_data,
            return_url=False,
        )
        if not rendered:
            return None

        path = Path(str(rendered))
        if not path.is_file() and str(rendered).startswith("http"):
            try:
                from astrbot.core.utils.io import download_image_by_url

                local = await download_image_by_url(str(rendered))
                path = Path(local)
            except Exception as e:
                logger.warning(f"下载 HtmlRenderer 远程图片失败: {e}")
                return None

        if not path.is_file():
            logger.warning(f"HtmlRenderer 返回无效路径: {rendered}")
            return None

        # 裁掉 t2i full_page 常见大块白边（与 parser 一致）
        return await asyncio.to_thread(trim_canvas, path)
    except Exception as e:
        logger.warning(f"JM 预览卡渲染失败: {e}")
        return None
