import asyncio
import glob
import logging
import os
import time
import traceback

from . import deps
from .text_util import get_album_title

logger = logging.getLogger("astrbot")


def format_cleanup_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"


def cleanup_old_files_sync(download_dir, cleanup_days, force_all=False, extra_dirs=None):
    """同步清理 downloads（应在线程池中调用，避免阻塞事件循环）。

    ``extra_dirs``：额外目录（如旧版插件目录 ``plugins/jmcomic/downloads``），
    与主目录一并清理，避免「主目录清完、遗留缓存还在」。
    """
    if force_all:
        logger.info("开始清理所有文件...")
    else:
        logger.info("开始清理旧文件...")

    current_time = time.time()
    cutoff_time = current_time - (cleanup_days * 24 * 60 * 60)

    dirs: list[str] = []
    for d in [download_dir, *(extra_dirs or [])]:
        if not d:
            continue
        norm = os.path.normpath(os.path.abspath(d))
        if norm not in dirs:
            dirs.append(norm)

    existing = [d for d in dirs if os.path.isdir(d)]
    if not existing:
        logger.warning("下载目录不存在，跳过清理（tried=%s）", dirs)
        return None

    total_files = 0
    deleted_files = 0
    total_size = 0
    freed_size = 0
    failed_deletes = 0

    for download_dir in existing:
        logger.info("清理目录: %s", download_dir)
        for root, dirs_walk, files in os.walk(download_dir):
            for file in files:
                total_files += 1
                file_path = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(file_path)
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    should_delete = force_all or (mtime < cutoff_time)
                    if should_delete:
                        try:
                            os.remove(file_path)
                            deleted_files += 1
                            freed_size += file_size
                            logger.info(f"已删除文件: {file_path}")
                        except Exception as e:
                            failed_deletes += 1
                            logger.error(f"删除文件失败 {file_path}: {str(e)}")
                except Exception as e:
                    logger.error(f"获取文件信息失败 {file_path}: {str(e)}")

        for root, dirs_walk, files in os.walk(download_dir, topdown=False):
            for dir_name in dirs_walk:
                dir_path = os.path.join(root, dir_name)
                try:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                        logger.info(f"已删除空目录: {dir_path}")
                except Exception as e:
                    logger.error(f"删除空目录失败 {dir_path}: {str(e)}")

    result_msg = (
        f"清理完成:\n"
        f"- 目录数: {len(existing)}\n"
        f"- 总文件数: {total_files}\n"
        f"- 删除文件数: {deleted_files}\n"
        f"- 删除失败: {failed_deletes}\n"
        f"- 总空间: {format_cleanup_size(total_size)}\n"
        f"- 释放空间: {format_cleanup_size(freed_size)}"
    )
    if force_all:
        result_msg += "\n- 清理模式: 全部清理"
    else:
        result_msg += f"\n- 清理模式: 仅清理 {cleanup_days} 天前的文件"
    logger.info(result_msg)

    return {
        'total_files': total_files,
        'deleted_files': deleted_files,
        'failed_deletes': failed_deletes,
        'total_size': total_size,
        'freed_size': freed_size,
        'force_all': force_all,
        'dirs': existing,
    }


async def create_pdf(
    comic_dir,
    album_title,
    comic_id,
    download_dir,
    compress_quality,
    max_image_dimension,
    finalize_fn,
):
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
        max_dimension = max_image_dimension

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
                    img = deps.Image.open(image_file)

                    # 如果图片过大，进行缩放
                    width, height = img.size
                    if width > max_dimension or height > max_dimension:
                        # 计算比例
                        ratio = min(max_dimension/width, max_dimension/height)
                        new_width = int(width * ratio)
                        new_height = int(height * ratio)
                        # 缩放图片
                        img = img.resize((new_width, new_height), deps.Image.LANCZOS)

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
                    f.write(deps.img2pdf.convert(processed_image_files))
                logger.info(f"使用img2pdf成功创建压缩PDF文件: {pdf_path}")
            except Exception as img2pdf_err:
                logger.error(f"使用img2pdf创建PDF失败: {str(img2pdf_err)}")
                # 备用方案：使用PIL库转换
                images = []
                for image_file in processed_image_files:
                    try:
                        img = deps.Image.open(image_file)
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
                    f.write(deps.img2pdf.convert(image_files))
                logger.info(f"使用原始图片成功创建PDF文件: {pdf_path}")
            except Exception as img2pdf_err:
                logger.error(f"使用img2pdf创建PDF失败: {str(img2pdf_err)}")
                # 最后的备用方案：使用PIL库转换
                try:
                    images = []
                    for image_file in image_files:
                        try:
                            img = deps.Image.open(image_file)
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

        finalize_fn(pdf_path)
        return pdf_path
    except Exception as e:
        logger.error(f"创建PDF异常: {str(e)}")
        logger.error(traceback.format_exc())
        # 重新抛出异常，确保调用方能收到错误信息
        raise Exception(f"创建PDF失败: {str(e)}")


async def download_comic_task(plugin, comic_id: str, event, group_id: str = None, album_title: str = None):
    """漫画下载任务。返回最终状态字符串（经全局闸门，默认同时只下一个）。"""
    from .delivery import mark_delivered, recently_delivered
    from .download_gate import get_download_gate

    message = event
    channel = f"group:{group_id}" if group_id else (
        f"private:{event.get_sender_id()}" if hasattr(event, "get_sender_id") else "private"
    )
    if not group_id and hasattr(event, "get_group_id"):
        group_id = event.get_group_id()
        channel = f"group:{group_id}" if group_id else channel

    limit = int(getattr(plugin, "global_download_concurrency", 1) or 1)
    gate = get_download_gate(limit)
    label = f"{channel}:{comic_id}"

    async with gate.slot(label) as ahead:
        # 入队提示已在 commands 里发过；这里只在真正轮到时提一声，避免数字对不上
        if ahead > 0:
            tip = f"轮到漫画 {comic_id} 了，开始下载…"
            logger.info("jmcomic %s (waited_ahead=%s)", tip, ahead)
            send_text = getattr(plugin, "_send_text", None)
            if callable(send_text):
                try:
                    await send_text(event, tip)
                except Exception as e:
                    logger.warning("jmcomic 开下提示发送失败: %s", e)
            elif message and hasattr(message, "reply"):
                try:
                    await message.reply(tip)
                except Exception:
                    pass
        return await _download_comic_task_body(
            plugin, comic_id, event, group_id, album_title, channel=channel
        )


async def _download_comic_task_body(
    plugin,
    comic_id: str,
    event,
    group_id: str = None,
    album_title: str = None,
    *,
    channel: str,
):
    """实际下载/上传逻辑（已持有全局闸门）。"""
    from .delivery import mark_delivered, recently_delivered

    message = event
    error_msg = None
    uploader = plugin.uploader

    try:
        # 再次检查群聊状态（以防传入的group_id为None）
        if not group_id and hasattr(event, 'get_group_id'):
            group_id = event.get_group_id()
            channel = f"group:{group_id}" if group_id else channel

        if recently_delivered(channel, comic_id):
            return f"漫画 {comic_id} 刚才已发过，请查收（未重复发送）"

        download_dir = plugin.download_dir
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
                if not bot:
                    return f"漫画 {comic_id} 上传失败：无法获取 bot"
                upload_success = await uploader.upload_to_group_file(
                    bot,
                    group_id,
                    pdf_path,
                    f"jm_{comic_id}.pdf"
                )
                if upload_success:
                    mark_delivered(channel, comic_id)
                    if message and hasattr(message, 'reply'):
                        await message.reply(f"漫画 {comic_id} 已上传到群文件，请查收！")
                    return f"漫画 {comic_id} 已上传到群文件，请查收！"
                if message and hasattr(message, 'reply'):
                    await message.reply(uploader.upload_fail_hint(comic_id))
                return f"漫画 {comic_id} 上传群文件失败"
            else:
                # 私聊情况下直接发送文件
                # 获取用户ID
                user_id = event.get_sender_id()

                # 如果获取到了用户ID,尝试发送文件
                if bot and user_id and pdf_path and os.path.exists(pdf_path):
                    try:
                        # 尝试发送文件
                        ok = await uploader.upload_private_file(
                            bot, user_id, pdf_path, f"jm_{comic_id}.pdf"
                        )
                        if ok:
                            mark_delivered(channel, comic_id)
                            return f"漫画 {comic_id} 已发送，请查收"
                        raise Exception("私聊文件上传失败")
                    except Exception as upload_err:
                        logger.error(f"发送文件失败: {str(upload_err)}")

                        # 尝试发送普通消息而不是文件
                        try:
                            await bot.call_action(
                                action="send_private_msg",
                                user_id=user_id,
                                message=(
                                    f"漫画 {comic_id} 已下载完成，但无法发送文件。\n"
                                    f"{uploader.upload_fail_hint(comic_id)}"
                                )
                            )
                            return f"漫画 {comic_id} 上传失败：无法发送文件"
                        except Exception as msg_err:
                            logger.error(f"发送普通消息也失败: {str(msg_err)}")

                # 如果无法发送,返回本地路径
                return f"漫画 {comic_id} 上传失败：文件仅保存在本地 {pdf_path}"

        # 创建漫画下载目录
        save_dir = os.path.join(download_dir, f"{comic_id}")
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        # 漫画标题变量
        if not album_title:
            album_title = f"漫画_{comic_id}"

        try:
            # 使用配置文件创建option对象
            option = deps.jmcomic.create_option_by_file(plugin.option_file)

            # 设置保存目录
            option.dir_rule = deps.DirRule(
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
                await loop.run_in_executor(None, deps.jmcomic.download_album, comic_id, option)
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
        pdf_path = await plugin._create_pdf(save_dir, album_title, comic_id)

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
                    upload_success = await uploader.upload_to_group_file(
                        bot,
                        group_id,
                        pdf_path,
                        f"jm_{comic_id}.pdf"
                    )
                    if upload_success:
                        mark_delivered(channel, comic_id)
                        if message and hasattr(message, 'reply'):
                            await message.reply(f"漫画 {comic_id} 已上传到群文件，请查收！")
                        return f"漫画 {comic_id} 已上传到群文件，请查收！"
                    if message and hasattr(message, 'reply'):
                        await message.reply(uploader.upload_fail_hint(comic_id))
                    return f"漫画 {comic_id} 上传群文件失败"
                logger.warning("无法获取bot对象，无法上传群文件")
                return f"漫画 {comic_id} 上传失败：无法获取 bot"
            if message and hasattr(message, 'reply'):
                await message.reply(f"创建PDF文件失败，请直接查看下载的图片。")
            return f"漫画 {comic_id} 创建PDF失败"
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
                    ok = await uploader.upload_private_file(
                        bot, user_id, pdf_path, f"jm_{comic_id}.pdf"
                    )
                    if ok:
                        mark_delivered(channel, comic_id)
                        return f"漫画 {comic_id} 已发送，请查收"
                    raise Exception("私聊文件上传失败")
                except Exception as upload_err:
                    logger.error(f"发送文件失败: {str(upload_err)}")

                    # 尝试发送普通消息而不是文件
                    try:
                        await bot.call_action(
                            action="send_private_msg",
                            user_id=user_id,
                            message=(
                                f"漫画 {comic_id} 已下载完成，但无法发送文件。\n"
                                f"{uploader.upload_fail_hint(comic_id)}"
                            )
                        )
                        return f"漫画 {comic_id} 上传失败：无法发送文件"
                    except Exception as msg_err:
                        logger.error(f"发送普通消息也失败: {str(msg_err)}")
            else:
                logger.error(f"无法获取用户ID或bot对象: user_id={user_id}, bot={bot}")

            return f"漫画 {comic_id} 上传失败：文件仅保存在本地 {pdf_path}"
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

        return None
