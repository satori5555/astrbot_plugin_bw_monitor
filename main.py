import asyncio
import json
import os
from typing import Dict, Tuple, Optional, List
import requests
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

# 禁用 SSL 警告
import urllib3
urllib3.disable_warnings()

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

REFRESH_INTERVAL = 1
TIMEOUT = 50
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
}


@register("astrbot_plugin_bw_monitor", "YourName", "BW余票监控插件", "2.0.0")
class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)

        self.data = {}  # chat_key -> { switch, projects }
        self.last_data: Dict[str, Dict[str, List[List[str]]]] = {}
        self.session_id_map: Dict[str, str] = {}
        self.monitoring = False
        self.monitor_task: Optional[asyncio.Task] = None

        self.load_settings()

    def save_settings(self):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        logger.info("[BWMonitor] 已写入 settings.json")

    def load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            logger.info("[BWMonitor] settings.json 不存在，已创建空文件。")
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            self.data = {}
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            logger.info("[BWMonitor] 成功加载 settings.json 配置。")
        except Exception as e:
            logger.warning(f"[BWMonitor] 加载 settings.json 失败: {e}")
            self.data = {}

    def get_chat_key(self, event: AstrMessageEvent) -> str:
        logger.info(f"[DEBUG] unified_msg_origin = {event.unified_msg_origin}")
        return event.unified_msg_origin or "user:unknown"

    def ensure_chat(self, chat_key: str):
        if chat_key not in self.data:
            self.data[chat_key] = {
                "switch": False,
                "projects": []
            }

    async def initialize(self):
        logger.info("BW Monitor Plugin Initialized")

    async def terminate(self):
        logger.info("BW Monitor Plugin Terminated")
        if self.monitor_task:
            self.monitor_task.cancel()

    @filter.command("bw on")
    async def bw_on(self, event: AstrMessageEvent):
        chat_key = self.get_chat_key(event)
        self.ensure_chat(chat_key)

        self.data[chat_key]["switch"] = True
        self.session_id_map[chat_key] = event.unified_msg_origin
        self.save_settings()

        if not self.monitoring:
            self.monitoring = True
            self.monitor_task = asyncio.create_task(self.run_monitor_loop())

        yield event.plain_result("✅ 已开启 BW 余票监控。")

    @filter.command("bw off")
    async def bw_off(self, event: AstrMessageEvent):
        chat_key = self.get_chat_key(event)
        self.ensure_chat(chat_key)

        self.data[chat_key]["switch"] = False
        self.save_settings()
        yield event.plain_result("✅ 已关闭 BW 余票监控。")

    @filter.command("bw add")
    async def bw_add(self, event: AstrMessageEvent):
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

        if pid not in self.data[chat_key]["projects"]:
            self.data[chat_key]["projects"].append(pid)
            self.session_id_map[chat_key] = event.unified_msg_origin
            self.save_settings()
            yield event.plain_result(f"✅ 已添加监控项目 {pid}")
        else:
            yield event.plain_result(f"⚠️ 项目 {pid} 已在监控列表中")

    @filter.command("bw rm")
    async def bw_rm(self, event: AstrMessageEvent):
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

        if pid in self.data[chat_key]["projects"]:
            self.data[chat_key]["projects"].remove(pid)
            self.save_settings()
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

        pids = self.data[chat_key]["projects"]
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
                for chat_key, cfg in self.data.items():
                    if not cfg.get("switch", False):
                        continue
                    for pid in cfg.get("projects", []):
                        await self.check_project(pid, chat_key)
            except Exception as e:
                logger.warning(f"[BWMonitor] 轮询异常: {e}")
            await asyncio.sleep(REFRESH_INTERVAL)

    async def check_project(self, pid: str, chat_key: str):
        name, tickets = self.advanced_project_query(pid)
        if not tickets:
            return

        if chat_key not in self.last_data:
            self.last_data[chat_key] = {}

        last_tickets = self.last_data[chat_key].get(pid)
        if last_tickets is None:
            text = self.format_tickets(name, tickets)
            self.send_message(chat_key, text)
        elif tickets != last_tickets:
            text = self.format_tickets(name, tickets)
            self.send_message(chat_key, text)

        self.last_data[chat_key][pid] = tickets

    async def query_and_push_once(self, pid: str, chat_key: str):
        name, tickets = self.advanced_project_query(pid)
        if not tickets:
            self.send_message(chat_key, f"项目 {pid} 未获取到票务数据。")
            return

        text = self.format_tickets(name, tickets)
        self.send_message(chat_key, text)

    def advanced_project_query(self, project_id: str) -> Tuple[str, List[List[str]]]:
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
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False).json()
        if resp.get("code") != 0:
            return f"项目 {project_id}", []

        data = resp["data"]
        project_name = data.get("name", f"项目 {project_id}")

        changfan_url = f"https://show.bilibili.com/api/ticket/linkgoods/list?project_id={project_id}&page_type=0"
        changfan_resp = requests.get(changfan_url, headers=HEADERS, timeout=TIMEOUT, verify=False).json()

        if changfan_resp.get("code") == 0 and changfan_resp["data"]["total"] > 0:
            for linkgood in changfan_resp["data"]["list"]:
                linkgood_id = linkgood["id"]

                detail_url = f"https://show.bilibili.com/api/ticket/linkgoods/detail?link_id={linkgood_id}"
                detail_resp = requests.get(detail_url, headers=HEADERS, timeout=TIMEOUT, verify=False).json()
                if detail_resp.get("code") != 0:
                    continue

                screens = detail_resp["data"]["specs_list"]
                for screen in screens:
                    screen_name = screen["name"]
                    for sku in screen["ticket_list"]:
                        desc = sku["desc"]
                        sale_flag = SALE_STATUS_MAP.get(sku["sale_flag_number"], "未知状态")
                        price = sku["price"] / 100
                        tickets.append([f"{screen_name} {desc} ¥{price}", sale_flag])

        else:
            sales_dates = data.get("sales_dates", [])
            if sales_dates:
                for sales_date in sales_dates:
                    date_str = sales_date["date"]
                    info_url = f"https://show.bilibili.com/api/ticket/project/infoByDate?id={project_id}&date={date_str}"
                    info_resp = requests.get(info_url, headers=HEADERS, timeout=TIMEOUT, verify=False).json()
                    if info_resp.get("code") != 0:
                        continue

                    screens = info_resp["data"]["screen_list"]
                    for screen in screens:
                        screen_name = screen["name"]
                        for sku in screen["ticket_list"]:
                            desc = sku["desc"]
                            sale_flag = SALE_STATUS_MAP.get(sku["sale_flag_number"], "未知状态")
                            price = sku["price"] / 100
                            tickets.append([f"{date_str} {screen_name} {desc} ¥{price}", sale_flag])
            else:
                screens = data.get("screen_list", [])
                for screen in screens:
                    screen_name = screen.get("name", "")
                    for sku in screen.get("ticket_list", []):
                        desc = sku.get("desc", "")
                        sale_flag = SALE_STATUS_MAP.get(sku.get("sale_flag_number", 0), "未知状态")
                        price = sku.get("price", 0) / 100
                        tickets.append([f"{screen_name} {desc} ¥{price}", sale_flag])

        return project_name, tickets

    def format_tickets(self, name: str, tickets: List[List[str]]) -> str:
        result = f"🎫 项目名称：{name}\n"
        for item in tickets:
            result += f"{item[0]} - {item[1]}\n"
        return result.strip()

    def send_message(self, chat_key: str, text: str):
        session_id = self.session_id_map.get(chat_key)
        if not session_id:
            logger.warning(f"找不到 session_id，无法发送：{chat_key}")
            return

        message_chain = MessageChain([Comp.Plain(text)])
        future = asyncio.run_coroutine_threadsafe(
            self.context.send_message(session_id, message_chain),
            asyncio.get_event_loop()
        )
        try:
            future.result(timeout=10)
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
