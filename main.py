import re
import os
import asyncio
from typing import TYPE_CHECKING

# 导入 jmcomic 相关的库
# 你需要先通过 pip install jmcomic 来安装
try:
    from jmcomic import jm_option, jm_client_new, JmcomicUI
    from jmcomic.cl_api import (
        JmOption, JmcomicClient, DownloadResult,
        LoginResult, SearchResult, JmAlbumDetail  # 导入新增的类型
    )
except ImportError:
    print("jmcomic 库未安装，请使用 'pip install jmcomic' 进行安装")
    jm_option = None

# 导入 AstrBot 相关的库
from astrbot.core import plugin, BasePlugin
from astrbot.core.event.message import MessageEvent

if TYPE_CHECKING:
    from astrbot.core.bot import Bot


# 插件元数据
__name__ = "JMComicDownloader"
__version__ = "1.2.0"  # 版本号更新


@plugin.register
class JMComicDownloader(BasePlugin):
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
        # 确保配置文件所在的目录存在
        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)
        self.log.info(f"将使用 jmcomic 配置文件: {os.path.abspath(config_path)}")
        
        # 2. 从该文件加载 JmOption，如果文件不存在则创建一个新的
        self.option: JmOption = jm_option.JmOption.load(config_path)

        # 3. 使用 AstrBot 插件配置覆盖 JmOption
        
        # 3.1. 设置下载目录
        self.download_dir = self.config.get("download_dir", "data/plugin_data/JMComicDownloader/pdf")
        os.makedirs(self.download_dir, exist_ok=True)
        self.option.dir.download = self.download_dir
        self.log.info(f"漫画 PDF 将下载到: {os.path.abspath(self.download_dir)}")

        # 3.2. 设置下载后的处理插件为 PDF 打包器 (保持不变)
        self.option.download.post_processor.plugin = 'pdf_packer'
        self.option.download.post_processor.impl = 'JmPdfPacker'
        self.option.download.misc.use_meta_file = True

        # 3.3. ★ 新增：设置下载文件夹和PDF的文件命名规则
        # 将下载的图片文件夹命名格式设置为 [ID]
        self.option.dir.album_name_format = "${album_id}"
        # 将 PDF 打包器的输出文件名格式设置为 [文件夹名].pdf (即 [ID].pdf)
        self.option.plugin.pdf_packer.filename = '${album_dir_name}.pdf'
        self.log.info(f"PDF 文件将被命名为 [ID].pdf (例如: 350234.pdf)")

        # 3.4. 从 AstrBot 配置设置登录凭据 (原 3.3)
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

        # 4. 初始化 jmcomic 客户端和 UI (客户端会尝试使用凭据自动登录) (原 4)
        self.ui = JmcomicUI(self.option)
        self.client: JmcomicClient = jm_client_new(self.option)
        self.log.info("JMComicDownloader 插件已加载，客户端已初始化。")

    async def send_reply(self, event: MessageEvent, message: str):
        """
        统一的回复函数，自动判断群聊或私聊
        """
        if event.message_type == "group":
            await self.bot.send_group_message(event.group_id, message)
        else:
            await self.bot.send_private_message(event.user_id, message)

    @plugin.on_command_re(r"/jm\s*(.*)", "jm")
    async def handle_jm_command(self, event: MessageEvent, match: "re.Match"):
        """
        处理 /jm 命令
        """
        if jm_option is None:
            await self.send_reply(event, "错误：jmcomic 库未正确安装，请联系管理员。")
            return

        text_content = match.group(1).strip()
        # 提取消息中所有整数
        album_ids = re.findall(r"\d+", text_content)

        # 构造 @用户 的 CQ 码（如果是私聊则为空字符串）
        at_user = f"[CQ:at,qq={event.user_id}]" if event.message_type == "group" else ""

        if not album_ids:
            await self.send_reply(event, f"{at_user} 请提供至少一个有效的漫画 ID。".strip())
            return

        await self.send_reply(event, f"{at_user} 收到！准备处理 {len(album_ids)} 个漫画 ID...".strip())

        # 为每个 ID 创建一个独立的下载任务
        for album_id in album_ids:
            asyncio.create_task(self.process_download(event, album_id, at_user))

    @plugin.on_command("/jm_status", "jminfo")
    async def handle_jm_status(self, event: MessageEvent):
        """
        处理 /jm_status 命令，检查登录状态
        """
        if jm_option is None:
            await self.send_reply(event, "错误：jmcomic 库未正确安装。")
            return

        at_user = f"[CQ:at,qq={event.user_id}]" if event.message_type == "group" else ""
        await self.send_reply(event, f"{at_user} 正在检查登录状态...".strip())

        try:
            loop = asyncio.get_event_loop()
            # check_login 是阻塞IO
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
                    f"请检查 config.yml 中的 'username' 和 'password' 配置，"
                    f"或 jmcomic.yml 中的 cookies 是否有效。"
                )
            
            await self.send_reply(event, f"{at_user} {msg}".strip())

        except Exception as e:
            self.log.exception(f"检查登录状态时出错: {e}")
            await self.send_reply(event, f"{at_user} 检查登录状态时出错: {e}".strip())

    @plugin.on_command_re(r"/jm_search\s+(.*)", "jmsearch")
    async def handle_jm_search(self, event: MessageEvent, match: "re.Match"):
        """
        处理 /jm_search 命令
        """
        if jm_option is None:
            await self.send_reply(event, "错误：jmcomic 库未正确安装，请联系管理员。")
            return

        search_query = match.group(1).strip()
        if not search_query:
            await self.send_reply(event, "请输入搜索关键词。用法: /jm_search [关键词]")
            return

        at_user = f"[CQ:at,qq={event.user_id}]" if event.message_type == "group" else ""
        await self.send_reply(event, f"{at_user} 正在搜索: '{search_query}'...".strip())

        try:
            loop = asyncio.get_event_loop()
            # search_album 是阻塞IO
            search_result: SearchResult = await loop.run_in_executor(
                None,
                self.client.search_album,
                search_query
            )

            if not search_result.ok:
                await self.send_reply(event, f"{at_user} 搜索失败: {search_result.msg}".strip())
                return

            album_list: list[JmAlbumDetail] = search_result.album_list
            if not album_list:
                await self.send_reply(event, f"{at_user} 未找到与 '{search_query}' 相关的结果。".strip())
                return

            # 格式化搜索结果
            max_results = 5  # 最多显示5条
            reply_msg = f"搜索 '{search_query}' 的结果 (前 {len(album_list[:max_results])} 条):\n"
            reply_msg += "--------------------------\n"

            for i, album in enumerate(album_list[:max_results]):
                authors = ", ".join(album.author_list) if album.author_list else "N/A"
                reply_msg += f"ID: {album.id}\n"
                reply_msg += f"标题: {album.title}\n"
                reply_msg += f"作者: {authors}\n"
                reply_msg += "--------------------------\n"
            
            reply_msg += "使用 /jm [ID] 来下载。"

            await self.send_reply(event, f"{at_user}\n{reply_msg}".strip())

        except Exception as e:
            self.log.exception(f"搜索 '{search_query}' 时发生未知错误: {e}")
            await self.send_reply(event, f"{at_user} 搜索时出错: {e}".strip())

    async def process_download(self, event: MessageEvent, album_id: str, at_user: str):
        """
        处理单个漫画的下载逻辑（包括缓存检查）
        """
        try:
            # 1. 检查缓存
            self.log.info(f"正在为 {album_id} 搜索本地缓存...")
            # JmcomicUI.search_cache 会搜索 .metadata 文件和本地目录
            # 因为我们自定义了命名，所以直接在下载目录查找 [ID].pdf
            
            # ★ 更新：直接构造预期的 PDF 路径
            expected_pdf_name = f"{album_id}.pdf"
            expected_pdf_path = os.path.join(self.download_dir, expected_pdf_name)

            pdf_path = None
            if os.path.exists(expected_pdf_path):
                pdf_path = expected_pdf_path
            else:
                # 如果直接路径找不到，再尝试用 UI 搜索 (作为备用)
                search_result = self.ui.search_cache(album_id)
                if search_result.ok:
                    # 寻找已经打包好的 PDF 文件
                    pdf_path = next((fp for fp in search_result.file_list if fp.endswith(f"{album_id}.pdf")), None)

            if pdf_path:
                # 1.1 找到缓存
                self.log.info(f"找到 {album_id} 的缓存: {pdf_path}")
                await self.send_reply(event, f"找到 {album_id} 的本地缓存，准备发送...")
                await self.send_file_and_notify(event, pdf_path, album_id, at_user)
                return

            # 2. 缓存未命中，执行下载
            self.log.info(f"未找到 {album_id} 的缓存，开始下载...")
            await self.send_reply(event, f"开始下载漫画 {album_id}，这可能需要一些时间...")

            # jmcomic 的下载是阻塞IO，必须在线程池中运行
            loop = asyncio.get_event_loop()
            dl_result: DownloadResult = await loop.run_in_executor(
                None,  # 使用默认的 ThreadPoolExecutor
                self.client.download_album,  # 要执行的阻塞函数
                album_id  # 传递给函数的参数
            )

            # 3. 处理下载结果
            await self.send_reply(event, f"下载 {album_id} 失败: {dl.msg}")
            return

            # 4. 获取 PDF 文件路径
            # ★ 更新：路径现在应该是确定的
            final_pdf_path = dl_result.album.file_path
            
            # 健壮性检查：如果 file_path 不对，我们自己构造
            if not final_pdf_path or not final_pdf_path.endswith(".pdf"):
                self.log.warning(f"下载 {album_id} 成功，但 Jmcomic 返回的路径 '{final_pdf_path}' 不符合预期。")
                final_pdf_path = os.path.join(self.download_dir, f"{album_id}.pdf")
                
                if not os.path.exists(final_pdf_path):
                    self.log.error(f"致命错误：PDF 文件 {final_pdf_path} 未在指定位置生成。")
                    await self.send_reply(event, f"下载 {album_id} 成功，但 PDF 打包失败。请检查后台日志。")
                    return

            # 5. 发送文件
            self.log.info(f"下载 {album_id} 完成，文件位于: {final_pdf_path}")
            await self.send_file_and_notify(event, final_pdf_path, album_id, at_user)

        except Exception as e:
            self.log.exception(f"处理 {album_id} 时发生未知错误: {e}")
            await self.send_reply(event, f"处理 {album_id} 时发生严重错误: {e}")

    async def send_file_and_notify(self, event: MessageEvent, file_path: str, album_id: str, at_user: str):
        """
        统一的文件发送函数，自动判断群聊或私聊，并在发送后 @ 用户
        """
        file_name = os.path.basename(file_path)
        try:
            if event.message_type == "group":
                # 发送群文件
                await self.bot.upload_group_file(
                    event.group_id,
                    file_path=file_path,
                    name=file_name
                )
            else:
                # 发送私聊文件
                await self.bot.upload_private_file(
                    event.user_id,
                    file_path=file_path,
                    name=file_name
                )
            
            # 发送完成通知
            await self.send_reply(event, f"{at_user} 漫画 {album_id} ({file_name}) 已发送完成。".strip())

        except Exception as e:
            self.log.error(f"发送文件 {file_name} (ID: {album_id}) 时失败: {e}")
            await self.send_reply(event, f"发送文件 {album_id} 时失败: {e}")


