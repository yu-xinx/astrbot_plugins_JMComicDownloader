import re
import os
import asyncio
from typing import TYPE_CHECKING

# 导入 jmcomic 相关的库
try:
    from jmcomic import jm_option, jm_client_new, JmcomicUI
    from jmcomic.cl_api import (
        JmOption, JmcomicClient, DownloadResult,
        LoginResult, SearchResult, JmAlbumDetail
    )
except ImportError:
    print("jmcomic 库未安装，请使用 'pip install jmcomic' 进行安装")
    jm_option = None

# 导入 AstrBot 相关的库
from astrbot.api.event import filter
from astrbot.api.star import Context, register
from astrbot.api.plugin import Plugin
from astrbot.api.message import Plain, File

if TYPE_CHECKING:
    from astrbot.api.bot import Bot

# 插件元数据
__name__ = "JMComicDownloader"
__version__ = "1.2.0"

@register
class JMComicDownloader(Plugin):
    """
    JMComic 漫画下载插件
    - /jm [ID] : 下载漫画
    - /jm_search [关键词] : 搜索漫画
    - /jm_status : 检查登录状态
    """

    def __init__(self, bot: "Bot", config: dict):
        super().__init__(bot, config)

        if jm_option is None:
            self.log.error("jmcomic 库未加载，插件无法启动")
            return

        # 1. 从插件配置中读取 jmcomic 配置文件路径
        config_path = self.config.get("jm_config_path", "data/plugin_data/JMComicDownloader/jmcomic.yml")
        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)
        self.log.info(f"将使用 jmcomic 配置文件: {os.path.abspath(config_path)}")
        
        # 2. 从该文件加载 JmOption
        self.option: JmOption = jm_option.JmOption.load(config_path)

        # 3. 使用配置覆盖 JmOption
        self.download_dir = self.config.get("download_dir", "data/plugin_data/JMComicDownloader/pdf")
        os.makedirs(self.download_dir, exist_ok=True)
        self.option.dir.download = self.download_dir
        self.log.info(f"漫画 PDF 将下载到: {os.path.abspath(self.download_dir)}")

        # 设置 PDF 打包选项
        self.option.download.post_processor.plugin = 'pdf_packer'
        self.option.download.post_processor.impl = 'JmPdfPacker'
        self.option.download.misc.use_meta_file = True
        self.option.dir.album_name_format = "${album_id}"
        self.option.plugin.pdf_packer.filename = '${album_dir_name}.pdf'
        self.log.info(f"PDF 文件将被命名为 [ID].pdf (例如: 350234.pdf)")

        # 设置登录信息
        username = self.config.get("username", None)
        password = self.config.get("password", None)
        
        if username and password:
            self.option.account.username = username
            self.option.account.password = password
            self.log.info(f"已从插件配置加载用户名: {username}")
        elif self.option.account.username:
            self.log.info(f"将使用 jmcomic.yml 中的用户名: {self.option.account.username}")
        else:
            self.log.warning("未配置 jmcomic 用户名和密码，将以未登录状态运行。")

        # 初始化客户端
        self.ui = JmcomicUI(self.option)
        self.client: JmcomicClient = jm_client_new(self.option)
        self.log.info("JMComicDownloader 插件已加载，客户端已初始化。")

    @filter("/jm {album_id}")
    async def handle_jm_command(self, ctx: Context):
        """处理 /jm 命令"""
        if jm_option is None:
            await ctx.send("错误：jmcomic 库未正确安装，请联系管理员。")
            return

        album_id = ctx.state.get("album_id", "").strip()
        if not album_id.isdigit():
            await ctx.send("请提供有效的漫画 ID。")
            return

        await ctx.send(f"收到！准备处理漫画 ID: {album_id}...")
        asyncio.create_task(self.process_download(ctx, album_id))

    @filter("/jm_status")
    async def handle_jm_status(self, ctx: Context):
        """处理 /jm_status 命令"""
        if jm_option is None:
            await ctx.send("错误：jmcomic 库未正确安装。")
            return

        await ctx.send("正在检查登录状态...")

        try:
            loop = asyncio.get_event_loop()
            login_result: LoginResult = await loop.run_in_executor(
                None,
                self.client.check_login
            )
            
            if login_result.is_login:
                msg = (
                    f"登录成功！\n"
                    f"用户: {login_result.username}\n"
                    f"Email: {login_result.email}\n"
                    f"VIP: {login_result.vip}"
                )
            else:
                msg = (
                    f"未登录。\n"
                    f"信息: {login_result.msg}\n"
                    f"请检查配置中的登录信息或 cookies 是否有效。"
                )
            
            await ctx.send(msg)

        except Exception as e:
            self.log.exception(f"检查登录状态时出错: {e}")
            await ctx.send(f"检查登录状态时出错: {e}")

    @filter("/jm_search {keyword}")
    async def handle_jm_search(self, ctx: Context):
        """处理 /jm_search 命令"""
        if jm_option is None:
            await ctx.send("错误：jmcomic 库未正确安装，请联系管理员。")
            return

        keyword = ctx.state.get("keyword", "").strip()
        if not keyword:
            await ctx.send("请输入搜索关键词。用法: /jm_search [关键词]")
            return

        await ctx.send(f"正在搜索: '{keyword}'...")

        try:
            loop = asyncio.get_event_loop()
            search_result: SearchResult = await loop.run_in_executor(
                None,
                self.client.search_album,
                keyword
            )

            if not search_result.ok:
                await ctx.send(f"搜索失败: {search_result.msg}")
                return

            album_list: list[JmAlbumDetail] = search_result.album_list
            if not album_list:
                await ctx.send(f"未找到与 '{keyword}' 相关的结果。")
                return

            max_results = 5
            reply_msg = f"搜索 '{keyword}' 的结果 (前 {len(album_list[:max_results])} 条):\n"
            reply_msg += "--------------------------\n"

            for i, album in enumerate(album_list[:max_results]):
                authors = ", ".join(album.author_list) if album.author_list else "N/A"
                reply_msg += f"ID: {album.id}\n"
                reply_msg += f"标题: {album.title}\n"
                reply_msg += f"作者: {authors}\n"
                reply_msg += "--------------------------\n"
            
            reply_msg += "使用 /jm [ID] 来下载。"
            await ctx.send(reply_msg)

        except Exception as e:
            self.log.exception(f"搜索 '{keyword}' 时发生错误: {e}")
            await ctx.send(f"搜索时出错: {e}")

    async def process_download(self, ctx: Context, album_id: str):
        """处理下载逻辑"""
        try:
            self.log.info(f"正在为 {album_id} 搜索本地缓存...")
            
            expected_pdf_name = f"{album_id}.pdf"
            expected_pdf_path = os.path.join(self.download_dir, expected_pdf_name)

            pdf_path = None
            if os.path.exists(expected_pdf_path):
                pdf_path = expected_pdf_path
            else:
                search_result = self.ui.search_cache(album_id)
                if search_result.ok:
                    pdf_path = next((fp for fp in search_result.file_list if fp.endswith(f"{album_id}.pdf")), None)

            if pdf_path:
                self.log.info(f"找到 {album_id} 的缓存: {pdf_path}")
                await ctx.send("找到本地缓存，准备发送...")
                await self.send_file(ctx, pdf_path, album_id)
                return

            self.log.info(f"未找到 {album_id} 的缓存，开始下载...")
            await ctx.send(f"开始下载漫画 {album_id}，这可能需要一些时间...")

            loop = asyncio.get_event_loop()
            dl_result: DownloadResult = await loop.run_in_executor(
                None,
                self.client.download_album,
                album_id
            )

            if not dl_result.ok:
                await ctx.send(f"下载失败: {dl_result.msg}")
                return

            final_pdf_path = dl_result.album.file_path
            if not final_pdf_path or not final_pdf_path.endswith(".pdf"):
                self.log.warning(f"下载成功，但返回的路径 '{final_pdf_path}' 不符合预期。")
                final_pdf_path = os.path.join(self.download_dir, f"{album_id}.pdf")
                
                if not os.path.exists(final_pdf_path):
                    self.log.error(f"致命错误：PDF 文件 {final_pdf_path} 未生成。")
                    await ctx.send(f"下载成功，但 PDF 打包失败。请检查后台日志。")
                    return

            self.log.info(f"下载完成，文件位于: {final_pdf_path}")
            await self.send_file(ctx, final_pdf_path, album_id)

        except Exception as e:
            self.log.exception(f"处理 {album_id} 时发生错误: {e}")
            await ctx.send(f"处理时发生错误: {e}")

    async def send_file(self, ctx: Context, file_path: str, album_id: str):
        """发送文件"""
        try:
            await ctx.send(File(path=file_path))
            await ctx.send(f"漫画 {album_id} 已发送完成。")
        except Exception as e:
            self.log.error(f"发送文件失败: {e}")
            await ctx.send(f"发送文件失败: {e}")