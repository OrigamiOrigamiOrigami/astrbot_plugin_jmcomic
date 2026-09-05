import asyncio
import importlib.util
import logging
import os
import re
import time
import traceback

from astrbot.api.all import CommandResult
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from .album import resolve_album_page_count, search_albums_sync
from .deps import reload_jmcomic_modules, update_jmcomic_package, yaml
from .download_pdf import cleanup_old_files_sync, download_comic_task, format_cleanup_size
from .proxy_util import get_default_proxy_config, get_proxy_host
from .text_util import (
    R18G_BLOCK_MESSAGE,
    album_has_r18g,
    format_search_results,
    is_r18g_search_query,
    parse_comic_id,
    parse_jm_command_args,
    parse_search_command,
)

logger = logging.getLogger("astrbot")


async def show_album_info(plugin, comic_id: str, event: AstrMessageEvent):
    """仅查询本子详情，不下载"""
    try:
        album_detail, client, _ = await plugin._fetch_album(comic_id)
        if plugin.filter_r18g and album_has_r18g(album_detail):
            return CommandResult().message(R18G_BLOCK_MESSAGE)
        await plugin._send_album_preview_split(event, album_detail, comic_id, client)
        return CommandResult().message(f"已查询漫画 {comic_id} 的详情")
    except Exception as e:
        logger.error(f"查询漫画详情失败: {str(e)}")
        return CommandResult().message(plugin._format_jm_error(str(e), comic_id))


async def search_comics(plugin, mode: str, query: str, page: int, event: AstrMessageEvent):
    """按标签或标题搜索本子"""
    if plugin.filter_r18g and is_r18g_search_query(query):
        return CommandResult().message("不支持搜索 R-18G 内容")

    try:
        loop = asyncio.get_event_loop()
        result_page = await loop.run_in_executor(
            None, search_albums_sync, plugin.option_file, mode, query, page
        )
        text = format_search_results(
            result_page,
            mode,
            query,
            page,
            plugin.search_max_results,
            plugin.filter_r18g,
        )
        return CommandResult().message(text)
    except Exception as e:
        logger.error(f"搜索失败 [{mode}={query} p{page}]: {str(e)}")
        return CommandResult().message(plugin._format_jm_error(str(e)))


async def cleanup_old_files(plugin, force_all=False):
    """清理旧文件（线程池执行，不阻塞事件循环）"""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            cleanup_old_files_sync,
            plugin.download_dir,
            plugin.cleanup_days,
            force_all,
        )
    except Exception as e:
        logger.error(f"清理旧文件失败: {str(e)}")
        logger.error(traceback.format_exc())
        return None


async def manual_cleanup_task(plugin, event: AstrMessageEvent):
    """后台执行全量清理并回消息"""
    message = event
    try:
        result = await cleanup_old_files(plugin, force_all=True)
        if result:
            result_msg = (
                f"清理完成！\n"
                f"删除了 {result['deleted_files']}/{result['total_files']} 个文件\n"
                f"释放空间: {format_cleanup_size(result['freed_size'])}"
            )
        else:
            result_msg = "文件清理完成"
        if message and hasattr(message, 'reply'):
            await message.reply(result_msg)
    except Exception as e:
        logger.error(f"手动清理任务异常: {str(e)}")
        logger.error(traceback.format_exc())
        if message and hasattr(message, 'reply'):
            try:
                await message.reply("清理失败")
            except Exception:
                pass


async def manual_cleanup(plugin, event: AstrMessageEvent):
    """手动清理文件（立即返回，后台删除）"""
    try:
        task = asyncio.create_task(manual_cleanup_task(plugin, event))
        task.add_done_callback(plugin._handle_task_exception)
        return CommandResult().message("开始清理所有下载文件，完成后会通知…")
    except Exception as e:
        logger.error(f"手动清理异常: {str(e)}")
        return CommandResult().message("清理失败")


def update_jmcomic_sync():
    """手动同步升级 jmcomic，返回用户可读的结果消息"""
    success, old_version, new_version, error = update_jmcomic_package()
    if not success:
        return f"jmcomic 更新失败: {error}"
    if old_version and new_version and old_version != new_version:
        reload_jmcomic_modules()
        return f"jmcomic 更新成功: {old_version} -> {new_version}"
    return f"jmcomic 已是最新版本: {new_version or old_version or 'unknown'}"


async def update_jmcomic_lib(plugin, event: AstrMessageEvent):
    """手动更新 jmcomic 库"""
    try:
        message = event
        if hasattr(message, 'reply'):
            await message.reply("正在更新 jmcomic 库，请稍候...")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, update_jmcomic_sync)
        return CommandResult().message(result)
    except Exception as e:
        logger.error(f"更新 jmcomic 异常: {str(e)}")
        return CommandResult().message(f"更新 jmcomic 失败: {str(e)}")


async def auto_update_jmcomic(plugin):
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


async def start_cleanup_task(plugin):
    """启动自动清理任务"""
    try:
        while True:
            # 获取当前时间
            now = time.localtime()
            # 每天凌晨2点执行清理
            if now.tm_hour == 2 and now.tm_min == 0:
                await cleanup_old_files(plugin)

            # 等待1分钟
            await asyncio.sleep(60)
    except Exception as e:
        logger.error(f"自动清理任务异常: {str(e)}")
        logger.error(traceback.format_exc())


async def startup_tasks(plugin):
    """插件启动时的后台任务"""
    if plugin.auto_update_jmcomic:
        await auto_update_jmcomic(plugin)
    asyncio.create_task(start_cleanup_task(plugin))


async def update_domains(plugin, event: AstrMessageEvent):
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
        asyncio.create_task(test_domains_task_with_notification(plugin, event))

        # 立即返回，不等待测试完成
        return CommandResult().message("域名测试任务已启动，将在后台进行测试...")
    except Exception as e:
        logger.error(f"测试域名异常: {str(e)}")
        logger.error(traceback.format_exc())
        return CommandResult().message(f"测试域名失败: {str(e)}")


async def test_domains_task_with_notification(plugin, event: AstrMessageEvent):
    """测试域名任务并发送通知"""
    try:
        # 执行域名测试
        result = await test_domains_task(plugin)

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


async def test_domains_task(plugin):
    """测试域名任务"""
    try:
        logger.info("开始测试禁漫天堂可用域名")

        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'domain_tester.py')

        try:
            spec = importlib.util.spec_from_file_location("domain_tester", script_path)
            tester = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tester)

            if hasattr(tester, "configure_proxy"):
                tester.configure_proxy(
                    plugin.proxy_address if plugin.use_proxy else None,
                    use_proxy=bool(plugin.use_proxy),
                )

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
        if available_domains and plugin.client_impl == "html":
            try:
                with open(plugin.option_file, 'r', encoding='utf-8') as f:
                    config_data = yaml.safe_load(f)

                if 'client' in config_data:
                    config_data['client']['domain'] = available_domains

                if plugin.use_proxy and 'client' in config_data and 'postman' in config_data['client']:
                    proxy_host = get_proxy_host()
                    proxy_match = re.match(r'(https?://)([^:]+):(\d+)', plugin.proxy_address or '')
                    if proxy_match:
                        adjusted_proxy = f"{proxy_match.group(1)}{proxy_host}:{proxy_match.group(3)}"
                        config_data['client']['postman']['meta_data']['proxies'] = {
                            'http': adjusted_proxy,
                            'https': adjusted_proxy
                        }

                with open(plugin.option_file, 'w', encoding='utf-8') as f:
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


def update_domains_in_config(plugin, domains):
    """更新配置文件中的域名设置"""
    try:
        # 读取配置文件
        with open(plugin.option_file, 'r', encoding='utf-8') as f:
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
            if plugin.use_proxy:
                # 检测Docker环境并调整代理地址
                proxy_host = get_proxy_host()

                if plugin.proxy_address:
                    # 解析用户配置的代理地址，替换主机部分
                    proxy_match = re.match(r'(https?://)([^:]+):(\d+)', plugin.proxy_address)
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
                            'http': plugin.proxy_address,
                            'https': plugin.proxy_address
                        }
                else:
                    # 使用自动检测的代理配置
                    default_proxy = get_default_proxy_config(plugin.proxy_port)
                    config_data['client']['postman']['meta_data']['proxies'] = default_proxy

        # 写回配置文件
        with open(plugin.option_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, allow_unicode=True)

        logger.info(f"更新域名配置成功，配置了 {len(domains)} 个自定义域名")
    except Exception as e:
        logger.error(f"更新域名配置失败: {str(e)}")


async def handle_jm_command(plugin, context: Context, event: AstrMessageEvent):
    """处理禁漫相关指令"""
    try:
        # 获取消息内容
        message_text = event.message_str if hasattr(event, 'message_str') else str(event)

        # 获取群号
        group_id = event.get_group_id() if hasattr(event, 'get_group_id') else None

        # 提取命令内容（支持 jm/JM、有空格/无空格）
        command_args = parse_jm_command_args(message_text)
        if not command_args:
            logger.warning(f"消息不匹配命令格式: {message_text}")
            return CommandResult().message(
                "指令格式错误，请使用 jm <漫画ID> / jm123456 / JM123456"
            )

        sub_cmd = command_args[0].lower()

        # 处理域名测试命令
        if sub_cmd == "domains":
            return await update_domains(plugin, event)

        # 处理清理命令
        elif sub_cmd == "cleanup":
            return await manual_cleanup(plugin, event)

        # 手动更新 jmcomic 库
        elif sub_cmd == "update":
            return await update_jmcomic_lib(plugin, event)

        # 仅查询详情，不下载
        elif sub_cmd == "info":
            if len(command_args) < 2:
                return CommandResult().message("用法: jm info <漫画ID>")
            comic_id = parse_comic_id(command_args[1])
            if not comic_id:
                return CommandResult().message("无法从输入中识别漫画ID")
            return await show_album_info(plugin, comic_id, event)

        # 搜索本子
        elif sub_cmd == "search":
            mode, query, page, err = parse_search_command(command_args)
            if err:
                return CommandResult().message(err)
            return await search_comics(plugin, mode, query, page, event)

        # 处理下载命令
        elif sub_cmd == "download":
            if len(command_args) < 2:
                return CommandResult().message("漫画ID不能为空")
            comic_id = parse_comic_id(command_args[1])
            if not comic_id:
                return CommandResult().message("无法从输入中识别漫画ID")
            return await download_comic(plugin, comic_id, event)

        # 如果第一个参数是纯数字，直接当作漫画ID下载
        elif sub_cmd.isdigit() or parse_comic_id(sub_cmd):
            comic_id = parse_comic_id(sub_cmd) or sub_cmd
            return await download_comic(plugin, comic_id, event)

        # 未知命令
        else:
            return CommandResult().message(
                f"未知命令: {sub_cmd}\n支持的命令:\n"
                f"- jm <漫画ID> / jm123456 / JM123456 - 下载漫画\n"
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


async def download_comic(
    plugin,
    comic_id: str,
    event: AstrMessageEvent,
    wait: bool = False,
):
    """下载漫画。

    wait=False（默认）：后台任务，立刻返回「正在上传/已启动」（命令行用法）。
    wait=True：等下载+上传结束再返回最终结果（companion 等需要诚实 ACK 的调用方）。
    """
    try:
        group_id = event.get_group_id() if hasattr(event, 'get_group_id') else None

        try:
            album_detail, client, album_title = await plugin._fetch_album(comic_id)
            if plugin.filter_r18g and album_has_r18g(album_detail):
                return CommandResult().message(R18G_BLOCK_MESSAGE)

            # 本地已有 PDF 时直接复用，不再做页数限制、不发预览
            pdf_path = plugin._pdf_path(comic_id)
            pdf_exists = os.path.exists(pdf_path)

            if not pdf_exists and plugin.max_download_pages > 0:
                loop = asyncio.get_event_loop()
                page_count = await loop.run_in_executor(
                    None,
                    resolve_album_page_count,
                    album_detail,
                    client,
                    plugin.max_download_pages,
                )
                if page_count > plugin.max_download_pages:
                    return CommandResult().message(
                        f"本子共约 {page_count} 页，超过上限 {plugin.max_download_pages} 页，"
                        f"已拒绝下载。\n"
                        f"可用 jm info {comic_id} 查看详情"
                    )

            if pdf_exists:
                logger.info(f"本地已有缓存 PDF，跳过预览直接上传: {pdf_path}")
            else:
                await plugin._send_album_preview_split(event, album_detail, comic_id, client)

            if wait:
                outcome = await download_comic_task(
                    plugin, comic_id, event, group_id, album_title
                )
                if not outcome:
                    return CommandResult().message(f"漫画 {comic_id} 下载失败")
                return CommandResult().message(str(outcome))

            task = asyncio.create_task(
                download_comic_task(plugin, comic_id, event, group_id, album_title)
            )
            task.add_done_callback(plugin._handle_task_exception)

            if pdf_exists:
                return CommandResult().message(f"漫画 {comic_id} 已有缓存，正在上传…")
            return plugin._build_download_start_result(comic_id)

        except Exception as test_e:
            logger.error(f"连接测试失败: {str(test_e)}")
            return CommandResult().message(plugin._format_jm_error(str(test_e), comic_id))

    except Exception as e:
        logger.error(f"下载漫画任务异常: {str(e)}")
        logger.error(traceback.format_exc())
        return CommandResult().message("下载失败")
