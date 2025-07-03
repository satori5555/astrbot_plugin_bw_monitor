import time
import threading
from datetime import datetime
from typing import Dict, Tuple, Optional, List
import requests
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ==================================
# 配置区
# ==================================

REFRESH_INTERVAL = 30      # 轮询间隔秒数
TIMEOUT = 10               # 请求超时
HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# ==================================
# 主插件类
# ==================================

@register("bw_monitor", "YourName", "BW余票监控插件", "1.0.0")
class BWMonitor(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.monitor_switch: Dict[Tuple[str, str], bool] = {}  # (type, id) -> on/off
        self.monitor_projects: Dict[Tuple[str, str], set] = {} # (type, id) -> set of project ids
        self.last_status: Dict[str, Dict[Tuple[str, str], str]] = {} # pid -> {(sid, tid): status}
        self.monitor_thread = threading.Thread(target=self.run_monitor_loop, daemon=True)
        self.monitor_thread.start()

    async def initialize(self):
        logger.info("BW Monitor Plugin Initialized")

    async def terminate(self):
        logger.info("BW Monitor Plugin Terminated")

    # ----------------------------
    # 命令 /bw on
    # ----------------------------
    @filter.command("bw on")
    async def bw_on(self, event: AstrMessageEvent):
        key = self.get_ctx_key(event)
        self.monitor_switch[key] = True
        yield event.plain_result("✅ 已开启 BW 余票监控。")

    # ----------------------------
    # 命令 /bw off
    # ----------------------------
    @filter.command("bw off")
    async def bw_off(self, event: AstrMessageEvent):
        key = self.get_ctx_key(event)
        self.monitor_switch[key] = False
        yield event.plain_result("✅ 已关闭 BW 余票监控。")

    # ----------------------------
    # 命令 /bw add <项目ID>
    # ----------------------------
    @filter.command("bw add")
    async def bw_add(self, event: AstrMessageEvent):
        args = event.get_command_args()
        if not args or not args[0].isdigit():
            yield event.plain_result("⚠️ 用法：/bw add <项目ID>")
            return
        pid = args[0]
        key = self.get_ctx_key(event)
        self.monitor_projects.setdefault(key, set()).add(pid)
        yield event.plain_result(f"✅ 已添加监控项目 {pid}")

    # ----------------------------
    # 命令 /bw rm <项目ID>
    # ----------------------------
    @filter.command("bw rm")
    async def bw_rm(self, event: AstrMessageEvent):
        args = event.get_command_args()
        if not args or not args[0].isdigit():
            yield event.plain_result("⚠️ 用法：/bw rm <项目ID>")
            return
        pid = args[0]
        key = self.get_ctx_key(event)
        if pid in self.monitor_projects.get(key, set()):
            self.monitor_projects[key].remove(pid)
            yield event.plain_result(f"✅ 已移除监控项目 {pid}")
        else:
            yield event.plain_result(f"⚠️ 未监控项目 {pid}")

    # ----------------------------
    # 命令 /bw list
    # ----------------------------
    @filter.command("bw list")
    async def bw_list(self, event: AstrMessageEvent):
        key = self.get_ctx_key(event)
        pids = self.monitor_projects.get(key, set())
        if not pids:
            yield event.plain_result("当前未监控任何项目。")
        else:
            txt = "当前监控项目ID：\n" + "\n".join(pids)
            yield event.plain_result(txt)

    # ----------------------------
    # 实际轮询逻辑
    # ----------------------------
    def run_monitor_loop(self):
        while True:
            try:
                for key, is_on in list(self.monitor_switch.items()):
                    if not is_on:
                        continue
                    pids = self.monitor_projects.get(key, set())
                    for pid in pids:
                        self.check_project(pid, key)
            except Exception as e:
                logger.warning(f"[BWMonitor] 轮询异常: {e}")
            time.sleep(REFRESH_INTERVAL)

    def check_project(self, pid: str, ctx_key: Tuple[str, str]):
        url = f"https://show.bilibili.com/api/ticket/project/getV2?version=134&id={pid}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            json_data = resp.json()
        except Exception as e:
            logger.warning(f"项目 {pid} 请求失败: {e}")
            return

        data = json_data.get("data", {})
        if not data:
            return

        # 解析票种
        new_status = {}
        for session in data.get("session_list", []):
            sid = str(session.get("session_id"))
            for ticket in session.get("ticket_list", []):
                tid = str(ticket.get("ticket_id"))
                name = ticket.get("desc", "")
                status = ticket.get("sale_flag", {}).get("display_name", "")
                new_status[(sid, tid)] = (name, status)

                old_status = self.last_status.setdefault(pid, {}).get((sid, tid))
                if old_status is not None and old_status != status:
                    # 发生变化
                    dt = datetime.now()
                    date_str = dt.strftime("%Y-%m-%d")
                    weekday = "日一二三四五六"[dt.weekday()]
                    msg = f"[{date_str} 星期{weekday}({sid})] {name}({tid}) 状态变化：{old_status} -> {status}"
                    self.send_message(ctx_key, msg)

                # 更新状态
                self.last_status[pid][(sid, tid)] = status

    def send_message(self, ctx_key: Tuple[str, str], text: str):
        """向群或私聊发送消息"""
        target_type, target_id = ctx_key
        if target_type == "group":
            self.context.push_group_message(int(target_id), text)
        elif target_type == "user":
            self.context.push_private_message(int(target_id), text)

    def get_ctx_key(self, event: AstrMessageEvent) -> Tuple[str, str]:
        """根据事件判断是群聊还是私聊"""
        if event.is_group_message():
            return ("group", str(event.get_group_id()))
        else:
            return ("user", str(event.get_user_id()))
