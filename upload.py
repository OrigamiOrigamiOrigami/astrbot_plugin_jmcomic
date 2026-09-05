import asyncio
import logging
import os
import re
import time
import traceback

logger = logging.getLogger("astrbot")


class FileUploader:
    def __init__(self, upload_path_map, max_base64_upload_mb, upload_sync_max_wait_sec):
        self.upload_path_map = upload_path_map
        self.max_base64_upload_mb = max_base64_upload_mb
        self.upload_sync_max_wait_sec = upload_sync_max_wait_sec

    def upload_fail_hint(self, comic_id: str = None) -> str:
        """生成上传失败时的用户提示"""
        lines = ["上传群文件失败。"]
        if not self.upload_path_map:
            lines.append(
                "AstrBot 与 NapCat 不在同一文件系统时，请配置 upload_path_map："
                "Docker路径前缀=NapCat可见路径前缀"
                "（若 NapCat 在虚拟机，填虚拟机里的共享目录路径）"
            )
        else:
            lines.append(
                "请检查 upload_path_map 是否指向 NapCat 进程能直接打开的路径"
                "（虚拟机请填虚拟机内路径，不是宿主机路径）"
            )
        lines.append("大文件不要依赖 base64（易 WebSocket 超时）")
        if self.upload_path_map:
            lines.append(
                "若路径上传偶发失败，可能是 VMware 共享目录尚未同步到虚拟机，"
                "可稍后在虚拟机执行 ls 映射路径确认文件是否出现"
            )
        if comic_id:
            lines.append(f"文件可能已保存在 downloads/jm_{comic_id}.pdf")
        return "\n".join(lines)

    def finalize_written_file(self, file_path: str):
        """强制刷盘，减轻 Docker→宿主机→hgfs 同步延迟"""
        try:
            with open(file_path, 'rb') as f:
                os.fsync(f.fileno())
        except Exception as e:
            logger.debug(f"文件 fsync 失败: {e}")

    async def wait_file_stable(
        self,
        file_path: str,
        stable_seconds: float = 1.5,
        timeout: float = 30.0,
    ) -> bool:
        """等待文件写入完成且大小稳定"""
        deadline = time.time() + timeout
        last_size = -1
        stable_start = None
        while time.time() < deadline:
            if not os.path.isfile(file_path):
                await asyncio.sleep(0.25)
                continue
            size = os.path.getsize(file_path)
            if size > 0 and size == last_size:
                if stable_start is None:
                    stable_start = time.time()
                elif time.time() - stable_start >= stable_seconds:
                    return True
            else:
                last_size = size
                stable_start = None
            await asyncio.sleep(0.25)
        return os.path.isfile(file_path) and os.path.getsize(file_path) > 0

    async def try_path_upload_once(
        self,
        bot,
        group_id: str,
        file_path: str,
        filename: str,
        mapped_only: bool = False,
    ) -> bool:
        for file_arg, note in self.build_upload_path_candidates(
            file_path, mapped_only=mapped_only
        ):
            try:
                logger.info(f"尝试路径上传 ({note}): {file_arg}")
                result = await self.try_upload_group_file(bot, group_id, file_arg, filename)
                logger.info(f"路径上传成功 ({note}): {result}")
                return True
            except Exception as method_err:
                logger.warning(f"路径上传失败 ({note}): {method_err}")
        return False

    def map_to_host_path(self, file_path: str):
        """
        将 Docker 内路径映射为协议端（NapCat）可直接打开的路径。
        upload_path_map 格式: Docker前缀=协议端可见前缀
        例: /AstrBot/data=/mnt/shared/main_bot/data
        （NapCat 在虚拟机时，右侧必须是虚拟机内能 ls 到的路径）
        """
        mapping = self.upload_path_map
        if not mapping or '=' not in mapping:
            return None

        src, dst = mapping.split('=', 1)
        src = src.strip().replace('\\', '/').rstrip('/')
        dst = dst.strip().rstrip('/\\')
        if not src or not dst:
            return None

        normalized = os.path.abspath(file_path).replace('\\', '/')
        if not normalized.startswith(src):
            # 兼容相对路径展开后仍不匹配的情况
            alt = file_path.replace('\\', '/')
            if alt.startswith(src):
                normalized = alt
            else:
                return None

        rest = normalized[len(src):].lstrip('/')
        # 保留用户配置的路径风格，子路径统一用 /
        dst_norm = dst.replace('\\', '/')
        mapped = f"{dst_norm}/{rest}" if rest else dst_norm
        return mapped

    def build_upload_path_candidates(self, file_path: str, mapped_only: bool = False):
        """生成协议端可尝试的文件路径列表（优先映射后的 file://）"""
        candidates = []
        seen = set()

        def add(path: str, note: str):
            if not path or path in seen:
                return
            seen.add(path)
            candidates.append((path, note))

        mapped_path = self.map_to_host_path(file_path)
        if mapped_path:
            # NapCat 认 file://；裸路径常报「识别URL失败」，故 file:// 优先
            add(f"file:///{mapped_path.lstrip('/')}", "协议端 file://")
            if re.match(r'^[A-Za-z]:/', mapped_path.replace('\\', '/')):
                hp = mapped_path.replace('\\', '/')
                add(f"file:///{hp}", "协议端 file:///盘符")
            if not mapped_only:
                add(mapped_path, "协议端映射路径")
                if not mapped_path.startswith('/'):
                    add(mapped_path.replace('/', '\\'), "协议端映射路径(反斜杠)")

        if mapped_only:
            return candidates

        add(file_path, "容器绝对路径")
        add(f"file://{file_path}", "容器 file://")

        try:
            current_dir = os.getcwd()
            if file_path.startswith(current_dir):
                add(os.path.relpath(file_path, current_dir), "相对工作目录路径")
        except Exception:
            pass

        return candidates

    def sync_wait_plan(self, file_size_mb: float):
        """
        共享目录同步等待计划：首等 + 指数退避重试。
        大文件在 hgfs 上可见往往更慢。
        """
        max_wait = max(5.0, self.upload_sync_max_wait_sec)
        # 首等：约 2s + 0.2s/MB，上限 12s
        initial = min(12.0, 2.0 + file_size_mb * 0.2)
        delays = [initial]
        elapsed = initial
        step = 3.0
        while elapsed + step <= max_wait:
            delays.append(step)
            elapsed += step
            step = min(step * 1.5, 15.0)
        if len(delays) == 1 and max_wait > initial:
            delays.append(max_wait - initial)
        return delays

    async def try_upload_group_file(self, bot, group_id: str, file_arg: str, filename: str):
        return await bot.call_action(
            action="upload_group_file",
            group_id=group_id,
            file=file_arg,
            name=filename,
        )

    async def upload_via_base64(self, bot, group_id: str, file_path: str, filename: str):
        import base64
        with open(file_path, 'rb') as f:
            file_base64 = base64.b64encode(f.read()).decode('utf-8')
        return await bot.call_action(
            action="upload_group_file",
            group_id=group_id,
            file=f"base64://{file_base64}",
            name=filename,
        )

    async def upload_to_group_file(self, bot, group_id: str, file_path: str, filename: str):
        """上传文件到群文件：优先协议端可见路径，大文件避免 base64 超时"""
        try:
            logger.info(f"开始上传文件到群 {group_id}: {file_path}")

            if not os.path.exists(file_path):
                logger.error(f"文件不存在: {file_path}")
                return False

            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            logger.info(f"待上传文件大小: {file_size_mb:.2f}MB")

            if not await self.wait_file_stable(file_path):
                logger.warning(f"文件大小未稳定，仍尝试上传: {file_path}")

            # 1) 路径上传；有映射时按计划等待并重试（缓解 hgfs 延迟）
            if self.upload_path_map:
                delays = self.sync_wait_plan(file_size_mb)
                total = sum(delays)
                logger.info(
                    f"共享目录同步等待计划: {len(delays)} 次尝试，"
                    f"累计约 {total:.0f}s（上限 {self.upload_sync_max_wait_sec}s）"
                )
                for attempt, wait_sec in enumerate(delays, start=1):
                    if wait_sec > 0:
                        logger.info(
                            f"等待共享目录同步 {wait_sec:.1f}s "
                            f"（第 {attempt}/{len(delays)} 次）"
                        )
                        await asyncio.sleep(wait_sec)
                    # 首次试全部候选；之后只重试映射 file://（其余路径注定失败）
                    mapped_only = attempt > 1
                    if await self.try_path_upload_once(
                        bot, group_id, file_path, filename, mapped_only=mapped_only
                    ):
                        return True
            else:
                if await self.try_path_upload_once(bot, group_id, file_path, filename):
                    return True

            # 2) base64 兜底：仅小文件，避免 WebSocket 超时
            allow_b64 = self.max_base64_upload_mb <= 0 or file_size_mb <= self.max_base64_upload_mb
            if allow_b64:
                try:
                    logger.info(
                        f"路径均不可用，尝试 base64 上传 "
                        f"({file_size_mb:.2f}MB, 上限 {self.max_base64_upload_mb}MB)"
                    )
                    result = await self.upload_via_base64(bot, group_id, file_path, filename)
                    logger.info(f"base64 上传成功: {result}")
                    return True
                except Exception as base64_err:
                    logger.error(f"base64 上传失败: {base64_err}")
            else:
                mapped = self.map_to_host_path(file_path) or "(未配置)"
                logger.error(
                    f"文件 {file_size_mb:.2f}MB 超过 base64 上限 "
                    f"{self.max_base64_upload_mb}MB，已跳过 base64。"
                    f"路径上传在约 {self.upload_sync_max_wait_sec:.0f}s 内仍失败 "
                    f"（映射: {mapped}）。请在虚拟机执行: ls -lh \"{mapped}\" ；"
                    f"若文件始终不存在，检查 VMware 共享文件夹是否正常；"
                    f"若文件已存在仍 ENOENT，检查 NapCat 是否能访问该挂载点"
                    f"（例如 NapCat 在虚拟机 Docker 内需额外挂载）"
                )

            logger.error("所有上传方法都失败了")
            return False

        except Exception as e:
            logger.error(f"上传文件到群失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def upload_private_file(self, bot, user_id: str, file_path: str, filename: str):
        """私聊上传文件，同样优先协议端可见路径映射"""
        if not os.path.exists(file_path):
            return False

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        mapped = self.map_to_host_path(file_path)

        if mapped:
            delays = self.sync_wait_plan(file_size_mb)
            for attempt, wait_sec in enumerate(delays, start=1):
                if wait_sec > 0:
                    await asyncio.sleep(wait_sec)
                candidates = self.build_upload_path_candidates(
                    file_path, mapped_only=(attempt > 1)
                )
                for file_arg, note in candidates:
                    try:
                        logger.info(f"私聊路径上传 ({note}): {file_arg}")
                        await bot.call_action(
                            action="upload_private_file",
                            user_id=user_id,
                            file=file_arg,
                            name=filename,
                        )
                        return True
                    except Exception as e:
                        logger.warning(f"私聊路径上传失败 ({note}): {e}")
        else:
            for file_arg, note in self.build_upload_path_candidates(file_path):
                try:
                    logger.info(f"私聊路径上传 ({note}): {file_arg}")
                    await bot.call_action(
                        action="upload_private_file",
                        user_id=user_id,
                        file=file_arg,
                        name=filename,
                    )
                    return True
                except Exception as e:
                    logger.warning(f"私聊路径上传失败 ({note}): {e}")

        allow_b64 = self.max_base64_upload_mb <= 0 or file_size_mb <= self.max_base64_upload_mb
        if allow_b64:
            try:
                import base64
                with open(file_path, 'rb') as f:
                    file_base64 = base64.b64encode(f.read()).decode('utf-8')
                await bot.call_action(
                    action="upload_private_file",
                    user_id=user_id,
                    file=f"base64://{file_base64}",
                    name=filename,
                )
                return True
            except Exception as e:
                logger.error(f"私聊 base64 上传失败: {e}")
        return False
