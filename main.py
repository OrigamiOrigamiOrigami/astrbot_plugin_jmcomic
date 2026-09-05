from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import CommandResult

import os
import logging
import asyncio

from .deps import Image, img2pdf, jmcomic, yaml
from .album import fetch_album_detail_sync, prepare_album_preview_sync, rotate_image_180
from .text_util import JM_COMMAND_PATTERN
from .paths import (
    apply_runtime_option,
    ensure_option_file,
    pdf_path,
    resolve_data_dir,
    resolve_download_dir,
)
from .upload import FileUploader
from .download_pdf import create_pdf
from . import commands as cmd

logger = logging.getLogger("astrbot")


@register("jmcomic", "OrigamiOrigamiOrigami", "禁漫漫画下载插件", "1.0.8")
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

        self.plugin_root = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = resolve_data_dir(self.plugin_root)

        # 配置项
        self.download_dir = resolve_download_dir(
            self.data_dir, self.config.get("download_path") or ""
        )
        self.download_path = self.download_dir
        self.legacy_download_dir = os.path.join(self.plugin_root, "downloads")
        self.use_proxy = self.config.get("use_proxy", True)
        self.proxy_address = self.config.get("proxy_address", "http://127.0.0.1:7890")
        self.proxy_port = self.config.get("proxy_port", 7890)
        self.custom_domains = self.config.get("custom_domains", "")
        self.client_impl = self.config.get("client_impl", "api")
        self.auto_update_jmcomic = bool(self.config.get("auto_update_jmcomic", False))
        self.cleanup_days = self.config.get("cleanup_days", 3)
        self.compress_quality = self.config.get("compress_quality", 85)
        self.max_image_dimension = self.config.get("max_image_dimension", 1200)
        self.timeout = self.config.get("timeout", 10)
        self.retry_times = self.config.get("retry_times", 10)
        self.jmcomic_log_level = self.config.get("jmcomic_log_level", "off")
        self.send_album_preview = self.config.get("send_album_preview", True)
        self.preview_card = self.config.get("preview_card", True)
        self.search_max_results = self.config.get("search_max_results", 10)
        self.filter_r18g = self.config.get("filter_r18g", True)
        self.max_download_pages = int(self.config.get("max_download_pages", 100) or 0)
        self.upload_path_map = (self.config.get("upload_path_map") or "").strip()
        self.max_base64_upload_mb = float(self.config.get("max_base64_upload_mb", 8) or 0)
        self.upload_sync_max_wait_sec = float(
            self.config.get("upload_sync_max_wait_sec", 60) or 60
        )
        if os.path.exists('/.dockerenv') and not self.upload_path_map:
            logger.warning(
                "检测到 Docker 环境但未配置 upload_path_map。"
                "若 NapCat 不在同一容器内，大文件群上传只能走 base64，容易超时；"
                "请配置 Docker路径=NapCat可见路径，"
                "例如 /AstrBot/data=/mnt/shared/main_bot/data"
            )

        self.uploader = FileUploader(
            self.upload_path_map,
            self.max_base64_upload_mb,
            self.upload_sync_max_wait_sec,
        )

        # option / 下载落在 plugin_data，更新插件不丢文件
        self.option_file = os.path.join(self.data_dir, "option.yml")
        ensure_option_file(self.plugin_root, self.data_dir, self.option_file)

        # 创建下载目录
        if not os.path.exists(self.download_dir):
            try:
                os.makedirs(self.download_dir)
                logger.info(f"创建下载目录成功: {self.download_dir}")
            except Exception as e:
                logger.error(f"创建下载目录失败: {e}")

        # 检查配置文件是否存在
        if not os.path.exists(self.option_file):
            logger.error(f"配置文件不存在: {self.option_file}")
            return

        apply_runtime_option(
            self.option_file,
            self.download_dir,
            {
                "use_proxy": self.use_proxy,
                "proxy_address": self.proxy_address,
                "proxy_port": self.proxy_port,
                "timeout": self.timeout,
                "retry_times": self.retry_times,
                "client_impl": self.client_impl,
                "jmcomic_log_level": self.jmcomic_log_level,
            },
        )

        # 配置自定义域名（仅 html 客户端需要）
        if self.client_impl == "html" and self.custom_domains:
            domains = [d.strip() for d in self.custom_domains.split(',') if d.strip()]
            if domains:
                self._update_domains_in_config(domains)
                logger.info(f"已配置自定义网页域名: {domains}")
        elif self.client_impl == "api":
            logger.info("使用 API 客户端，域名将由 jmcomic 自动更新")

        # 注册命令（大小写 JM/jm，支持 jm123456 无空格）
        self.context.register_commands(
            "jmcomic",
            JM_COMMAND_PATTERN.pattern,
            "禁漫下载指令",
            1,
            self.handle_jm_command,
            use_regex=True,
            ignore_prefix=True
        )

        # 启动后台任务：jmcomic 自动更新、过期文件清理
        asyncio.create_task(self._startup_tasks())

    def _pdf_path(self, comic_id: str) -> str:
        return pdf_path(self.download_dir, self.legacy_download_dir, comic_id)

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
        """发送本子预览：优先 HtmlRenderer 合成卡，失败回退文字+封面分发"""
        if not self.send_album_preview or album_detail is None:
            return

        cover_dir = os.path.join(self.download_dir, "_covers")
        loop = asyncio.get_event_loop()
        info_text, cover_path, page_count = await loop.run_in_executor(
            None,
            prepare_album_preview_sync,
            cover_dir,
            comic_id,
            album_detail,
            client,
        )

        if self.preview_card:
            render_album_preview_card = None
            try:
                from .preview_card import render_album_preview_card as _render
                render_album_preview_card = _render
            except ImportError:
                try:
                    from preview_card import render_album_preview_card as _render
                    render_album_preview_card = _render
                except ImportError as e:
                    logger.warning(f"无法导入 preview_card: {e}")

            if render_album_preview_card:
                card_path = await render_album_preview_card(
                    album_detail,
                    comic_id,
                    cover_path=cover_path,
                    page_count=page_count,
                )
                if card_path and await self._send_cover_image(event, card_path):
                    return
                logger.warning("预览卡发送失败，回退为文字+封面分发")

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

    def _handle_task_exception(self, task):
        """处理异步任务异常的回调函数"""
        try:
            exception = task.exception()
            if exception:
                logger.error(f"异步任务异常: {str(exception)}")
        except Exception as e:
            logger.error(f"处理任务异常回调失败: {str(e)}")

    async def _startup_tasks(self):
        await cmd.startup_tasks(self)

    def _update_domains_in_config(self, domains):
        cmd.update_domains_in_config(self, domains)

    async def _create_pdf(self, comic_dir, album_title, comic_id):
        return await create_pdf(
            comic_dir,
            album_title,
            comic_id,
            self.download_dir,
            self.compress_quality,
            self.max_image_dimension,
            self.uploader.finalize_written_file,
        )

    def _upload_fail_hint(self, comic_id: str = None) -> str:
        return self.uploader.upload_fail_hint(comic_id)

    def _finalize_written_file(self, file_path: str):
        return self.uploader.finalize_written_file(file_path)

    async def _upload_to_group_file(self, bot, group_id: str, file_path: str, filename: str):
        return await self.uploader.upload_to_group_file(bot, group_id, file_path, filename)

    async def _upload_private_file(self, bot, user_id: str, file_path: str, filename: str):
        return await self.uploader.upload_private_file(bot, user_id, file_path, filename)

    async def show_album_info(self, comic_id: str, event: AstrMessageEvent):
        return await cmd.show_album_info(self, comic_id, event)

    async def search_comics(self, mode: str, query: str, page: int, event: AstrMessageEvent):
        return await cmd.search_comics(self, mode, query, page, event)

    async def manual_cleanup(self, event: AstrMessageEvent):
        return await cmd.manual_cleanup(self, event)

    async def handle_jm_command(self, context: Context, event: AstrMessageEvent):
        return await cmd.handle_jm_command(self, context, event)

    async def download_comic(
        self,
        comic_id: str,
        event: AstrMessageEvent,
        wait: bool = False,
    ):
        return await cmd.download_comic(self, comic_id, event, wait=wait)

    async def update_jmcomic_lib(self, event: AstrMessageEvent):
        return await cmd.update_jmcomic_lib(self, event)

    async def update_domains(self, event: AstrMessageEvent):
        return await cmd.update_domains(self, event)
