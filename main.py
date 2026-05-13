import asyncio
import json
import os
from typing import Dict, Tuple, Optional, List
import aiohttp
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp
import html

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

TIMEOUT = 50
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
}


@register("astrbot_plugin_bw_monitor", "YourName", "BW余票监控插件", "2.0.0")
class Main(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)

        # 从 config dict 读取
        self.refresh_interval = config.get("refresh_interval", 3)
        self.admin_enabled = config.get("admin_enabled", False)
        self.default_enabled_sessions = config.get("default_enabled_sessions", [])

        self.data = {}  # chat_key -> { switch, projects }
        self.last_data: Dict[str, Dict[str, List[List[str]]]] = {}
        self.session_id_map: Dict[str, str] = {}
        self.monitoring = False
        self.monitor_task: Optional[asyncio.Task] = None
        # 全局项目轮询游标：所有会话共用同一个轮询节奏，避免一次循环内并发查询所有项目。
        self.project_poll_cursor = 0

        self.load_settings()

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            logger.info("[BWMonitor] 已写入 settings.json")
        except Exception as e:
            logger.error(f"[BWMonitor] 写入 settings.json 失败: {e}")

    def load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            logger.info("[BWMonitor] settings.json 不存在，自动生成默认配置。")

            self.data = {}
            for sess in self.default_enabled_sessions:
                self.data[sess] = {
                    "switch": True,
                    "projects": []
                }
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            logger.info("[BWMonitor] 成功加载 settings.json 配置。")
        except Exception as e:
            logger.warning(f"[BWMonitor] 加载 settings.json 失败: {e}")
            self.data = {}

    def get_chat_key(self, event: AstrMessageEvent) -> str:
        logger.debug(f"[DEBUG] unified_msg_origin = {event.unified_msg_origin}")
        return event.unified_msg_origin or "user:unknown"

    def ensure_chat(self, chat_key: str):
        if chat_key not in self.data:
            self.data[chat_key] = {
                "switch": False,
                "projects": []
            }

    def normalize_projects(self, cfg: dict) -> List[str]:
        """兼容旧配置，过滤空项目，保证 projects 始终是字符串 ID 列表。"""
        projects = cfg.get("projects", [])
        if not isinstance(projects, list):
            projects = []
        normalized = []
        for pid in projects:
            pid = str(pid).strip()
            if pid:
                normalized.append(pid)
        cfg["projects"] = normalized
        return normalized

    def get_enabled_project_sessions(self) -> Dict[str, List[str]]:
        """
        返回当前需要轮询的项目映射。

        结构：
            project_id -> [chat_key1, chat_key2, ...]

        这样可以保证：
        1. 多个会话监控同一个项目时，只向 B 站接口查询一次；
        2. 所有项目共用同一个全局轮询间隔，每次只查询一个项目。
        """
        project_sessions: Dict[str, List[str]] = {}
        for chat_key, cfg in list(self.data.items()):
            if not isinstance(cfg, dict) or not cfg.get("switch", False):
                continue

            for pid in self.normalize_projects(cfg):
                if not pid:
                    continue
                if pid not in project_sessions:
                    project_sessions[pid] = []
                if chat_key not in project_sessions[pid]:
                    project_sessions[pid].append(chat_key)

        return project_sessions

    def has_enabled_projects(self) -> bool:
        return bool(self.get_enabled_project_sessions())

    def start_monitor_if_needed(self):
        if self.monitor_task and not self.monitor_task.done():
            return
        if not self.has_enabled_projects():
            self.monitoring = False
            self.monitor_task = None
            return
        self.monitoring = True
        self.monitor_task = asyncio.create_task(self.run_monitor_loop())

    async def initialize(self):
        logger.info("BW Monitor Plugin Initialized")

    async def terminate(self):
        logger.info("BW Monitor Plugin Terminated")
        if self.monitor_task:
            self.monitor_task.cancel()

    @filter.command("bw on")
    async def bw_on(self, event: AstrMessageEvent):
        if self.admin_enabled and not event.is_admin:
            yield event.plain_result("⛔ 权限不足，仅管理员可执行该命令。")
            return

        chat_key = self.get_chat_key(event)
        self.ensure_chat(chat_key)

        self.data[chat_key]["switch"] = True
        self.session_id_map[chat_key] = event.unified_msg_origin
        self.save_settings()

        self.start_monitor_if_needed()

        if self.has_enabled_projects():
            yield event.plain_result("✅ 已开启 BW 余票监控。")
        else:
            yield event.plain_result("✅ 已开启 BW 余票监控。当前没有监控项目，添加项目后才会启动轮询。")

    @filter.command("bw off")
    async def bw_off(self, event: AstrMessageEvent):
        if self.admin_enabled and not event.is_admin:
            yield event.plain_result("⛔ 权限不足，仅管理员可执行该命令。")
            return

        chat_key = self.get_chat_key(event)
        self.ensure_chat(chat_key)

        self.data[chat_key]["switch"] = False
        self.save_settings()
        if not self.has_enabled_projects():
            self.monitoring = False
        yield event.plain_result("✅ 已关闭 BW 余票监控。")

    @filter.command("bw add")
    async def bw_add(self, event: AstrMessageEvent):
        if self.admin_enabled and not event.is_admin:
            yield event.plain_result("⛔ 权限不足，仅管理员可执行该命令。")
            return

        chat_key = self.get_chat_key(event)
        self.ensure_chat(chat_key)

        if not self.data[chat_key]["switch"]:
            yield event.plain_result("⚠️ 请先开启 BW 余票监控。")
            return

        args = self.get_command_args(event)
        if not args or len(args) < 2 or not args[1].isdigit():
            yield event.plain_result("⚠️ 用法：/bw add <项目ID>")
            return
        pid = args[1]

        projects = self.normalize_projects(self.data[chat_key])
        if pid not in projects:
            projects.append(pid)
            self.session_id_map[chat_key] = event.unified_msg_origin
            self.save_settings()
            self.start_monitor_if_needed()
            yield event.plain_result(f"✅ 已添加监控项目 {pid}")
        else:
            yield event.plain_result(f"⚠️ 项目 {pid} 已在监控列表中")

    @filter.command("bw rm")
    async def bw_rm(self, event: AstrMessageEvent):
        if self.admin_enabled and not event.is_admin:
            yield event.plain_result("⛔ 权限不足，仅管理员可执行该命令。")
            return

        chat_key = self.get_chat_key(event)
        self.ensure_chat(chat_key)

        if not self.data[chat_key]["switch"]:
            yield event.plain_result("⚠️ 请先开启 BW 余票监控。")
            return

        args = self.get_command_args(event)
        if not args or len(args) < 2 or not args[1].isdigit():
            yield event.plain_result("⚠️ 用法：/bw rm <项目ID>")
            return
        pid = args[1]

        projects = self.normalize_projects(self.data[chat_key])
        if pid in projects:
            projects.remove(pid)
            self.save_settings()
            if not self.has_enabled_projects():
                self.monitoring = False
            yield event.plain_result(f"✅ 已移除监控项目 {pid}")
        else:
            yield event.plain_result(f"⚠️ 未监控项目 {pid}")

    @filter.command("bw list")
    async def bw_list(self, event: AstrMessageEvent):
        chat_key = self.get_chat_key(event)
        self.ensure_chat(chat_key)

        if not self.data[chat_key]["switch"]:
            yield event.plain_result("⚠️ 请先开启 BW 余票监控。")
            return

        pids = self.normalize_projects(self.data[chat_key])
        if not pids:
            yield event.plain_result("当前未监控任何项目。")
        else:
            txt = "当前监控项目ID：\n" + "\n".join(pids)
            yield event.plain_result(txt)

    @filter.command("bw now")
    async def bw_now(self, event: AstrMessageEvent):
        chat_key = self.get_chat_key(event)
        self.ensure_chat(chat_key)

        if not self.data[chat_key]["switch"]:
            yield event.plain_result("⚠️ 请先开启 BW 余票监控。")
            return

        args = self.get_command_args(event)
        if not args or len(args) < 2 or not args[1].isdigit():
            yield event.plain_result("⚠️ 用法：/bw now <项目ID>")
            return
        pid = args[1]

        self.session_id_map[chat_key] = event.unified_msg_origin
        await self.query_and_push_once(pid, chat_key)
        yield event.plain_result(f"✅ 已查询并推送项目 {pid} 的最新票务状态。")

    async def run_monitor_loop(self):
        while self.monitoring:
            try:
                project_sessions = self.get_enabled_project_sessions()

                # 没有任何开启且有项目的会话时，直接停止轮询，避免空转和重复 WARN。
                if not project_sessions:
                    self.monitoring = False
                    self.monitor_task = None
                    self.project_poll_cursor = 0
                    logger.info("[BWMonitor] 当前没有需要轮询的项目，已停止轮询。")
                    break

                project_ids = list(project_sessions.keys())
                if self.project_poll_cursor >= len(project_ids):
                    self.project_poll_cursor = 0

                # 所有项目共用同一个轮询时间：一次循环只查询一个项目。
                pid = project_ids[self.project_poll_cursor]
                self.project_poll_cursor = (self.project_poll_cursor + 1) % len(project_ids)

                chat_keys = project_sessions.get(pid, [])
                await self.check_project_for_sessions(pid, chat_keys)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[BWMonitor] 轮询异常: {e}")

            await asyncio.sleep(self.refresh_interval)

    async def check_project_for_sessions(self, pid: str, chat_keys: List[str]):
        """
        查询一个项目一次，然后把结果分发给所有监控该项目的会话。
        每个会话仍然独立保存 last_data，避免新加入会话收不到首次状态。
        """
        if not chat_keys:
            return

        try:
            name, tickets = await self.advanced_project_query(pid)
            if not tickets:
                return

            for chat_key in chat_keys:
                if chat_key not in self.last_data:
                    self.last_data[chat_key] = {}

                last_tickets = self.last_data[chat_key].get(pid, [])
                last_map = {desc: sale_flag for desc, sale_flag in last_tickets}

                changes = []
                for item in tickets:
                    desc, sale_flag = item
                    last_flag = last_map.get(desc)
                    if last_flag is None:
                        changes.append(f"[新增] {desc} - {sale_flag}")
                    elif sale_flag != last_flag:
                        changes.append(f"[变动] {desc} - {last_flag} → {sale_flag}")

                if last_tickets == []:
                    text = self.format_tickets(name, tickets)
                    await self.send_message(chat_key, text)
                elif changes:
                    text = f"🎫 项目名称：{name}\n" + "\n".join(changes)
                    await self.send_message(chat_key, text)

                self.last_data[chat_key][pid] = tickets
        except Exception as e:
            logger.warning(f"[BWMonitor] 项目 {pid} 查询异常: {e}")

    async def check_project(self, pid: str, chat_key: str):
        await self.check_project_for_sessions(pid, [chat_key])

    async def query_and_push_once(self, pid: str, chat_key: str):
        name, tickets = await self.advanced_project_query(pid)
        if not tickets:
            await self.send_message(chat_key, f"项目 {pid} 未获取到票务数据。")
            return

        text = self.format_tickets(name, tickets)
        await self.send_message(chat_key, text)

    async def advanced_project_query(self, project_id: str) -> Tuple[str, List[List[str]]]:
        tickets = []
        SALE_STATUS_MAP = {
            1: "未开售",
            2: "售卖中",
            3: "已停售",
            4: "已售罄",
            5: "不可售",
            6: "库存紧张",
            8: "暂时售罄",
            9: "无购买资格"
        }

        url = f"https://show.bilibili.com/api/ticket/project/getV2?version=134&id={project_id}"
        resp = await self.fetch_json(url)
        if resp.get("code") != 0:
            return f"项目 {project_id}", []

        data = resp.get("data") or {}
        project_name = data.get("name") or f"项目 {project_id}"

        changfan_url = f"https://show.bilibili.com/api/ticket/linkgoods/list?project_id={project_id}&page_type=0"
        changfan_resp = await self.fetch_json(changfan_url)

        changfan_data = changfan_resp.get("data") or {}
        if changfan_resp.get("code") == 0 and changfan_data.get("total", 0) > 0:
            for linkgood in changfan_data.get("list", []):
                linkgood_id = linkgood.get("id")
                if not linkgood_id:
                    continue

                detail_url = f"https://show.bilibili.com/api/ticket/linkgoods/detail?link_id={linkgood_id}"
                detail_resp = await self.fetch_json(detail_url)
                if detail_resp.get("code") != 0:
                    continue

                detail_data = detail_resp.get("data") or {}
                screens = detail_data.get("specs_list", [])
                for screen in screens:
                    screen_name = screen.get("name", "")
                    for sku in screen.get("ticket_list", []):
                        desc = html.unescape(sku.get("desc", ""))
                        sale_flag = SALE_STATUS_MAP.get(sku.get("sale_flag_number", 0), "未知状态")
                        price = sku.get("price", 0) / 100
                        tickets.append([f"{screen_name} {desc} ¥{price}", sale_flag])
        else:
            sales_dates = data.get("sales_dates", [])
            if sales_dates:
                for sales_date in sales_dates:
                    date_str = sales_date.get("date")
                    if not date_str:
                        continue
                    info_url = f"https://show.bilibili.com/api/ticket/project/infoByDate?id={project_id}&date={date_str}"
                    info_resp = await self.fetch_json(info_url)
                    if info_resp.get("code") != 0:
                        continue

                    info_data = info_resp.get("data") or {}
                    screens = info_data.get("screen_list", [])
                    for screen in screens:
                        screen_name = screen.get("name", "")
                        for sku in screen.get("ticket_list", []):
                            desc = html.unescape(sku.get("desc", ""))
                            sale_flag = SALE_STATUS_MAP.get(sku.get("sale_flag_number", 0), "未知状态")
                            price = sku.get("price", 0) / 100
                            tickets.append([f"{date_str} {screen_name} {desc} ¥{price}", sale_flag])
            else:
                screens = data.get("screen_list", [])
                for screen in screens:
                    screen_name = screen.get("name", "")
                    for sku in screen.get("ticket_list", []):
                        desc = html.unescape(sku.get("desc", ""))
                        sale_flag = SALE_STATUS_MAP.get(sku.get("sale_flag_number", 0), "未知状态")
                        price = sku.get("price", 0) / 100
                        tickets.append([f"{screen_name} {desc} ¥{price}", sale_flag])

        return project_name, tickets

    async def fetch_json(self, url: str) -> dict:
        connector = aiohttp.TCPConnector(verify_ssl=False)
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            async with session.get(url, headers=HEADERS) as response:
                try:
                    return await response.json()
                except Exception as e:
                    logger.warning(f"[BWMonitor] 接口返回非 JSON：{url}，错误：{e}")
                    return {}

    def format_tickets(self, name: str, tickets: List[List[str]]) -> str:
        result = f"🎫 项目名称：{name}\n"
        for item in tickets:
            result += f"{item[0]} - {item[1]}\n"
        return result.strip()

    async def send_message(self, chat_key: str, text: str):
        session_id = self.session_id_map.get(chat_key) or chat_key
        if not session_id:
            logger.warning(f"找不到 session_id，无法发送：{chat_key}")
            return

        message_chain = MessageChain([Comp.Plain(text)])
        try:
            await self.context.send_message(session_id, message_chain)
        except Exception as e:
            logger.error(f"发送消息到 {session_id} 失败：{e}")

    def get_command_args(self, event: AstrMessageEvent) -> List[str]:
        text = ""

        if hasattr(event, "message_str") and isinstance(event.message_str, str):
            text = event.message_str.strip()
        elif hasattr(event, "message") and isinstance(event.message, str):
            text = event.message.strip()
        elif hasattr(event, "message_chain") and isinstance(event.message_chain, list):
            for seg in event.message_chain:
                if hasattr(seg, "text") and seg.text:
                    text += seg.text

        if not text:
            return []

        if text.startswith("/"):
            text = text[1:]

        parts = text.strip().split()

        if len(parts) <= 1:
            return []
        else:
            return parts[1:]
