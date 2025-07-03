# main.py
from nakuru.entities.components import *
from nakuru import GroupMessage, FriendMessage
from cores.qqbot.global_object import AstrMessageEvent
import threading, time, requests
from datetime import datetime

class BWTicketPlugin:
    def __init__(self) -> None:
        # 监控开关和项目配置：以上下文键(user/group)为单位
        self.monitors = {}  # e.g. {("user", qq): {"projects": set(ids)}, ("group", gid): {"projects": set(ids)}}
        # 存储每个项目的票种状态：{project_id: {ticket_id: status_str, ...}, ...}
        self.last_status = {}
        # 启动后台线程处理轮询
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        print("BWTicketPlugin initialized.")

    def run(self, ame: AstrMessageEvent):
        msg = ame.message_str.strip()
        # 判断上下文类型：群组或私聊
        if isinstance(ame.message_obj, GroupMessage):
            ctx = ("group", ame.message_obj.group_id)
        else:
            ctx = ("user", ame.message_obj.user_id)
        # 确保监控字典存在此上下文
        self.monitors.setdefault(ctx, {"projects": set(), "enabled": False})

        # 命令处理
        if msg == "/bw on":
            self.monitors[ctx]["enabled"] = True
            return True, (True, "已开启票务监控。", "bw_on")
        if msg == "/bw off":
            self.monitors[ctx]["enabled"] = False
            return True, (True, "已关闭票务监控。", "bw_off")
        if msg.startswith("/bw add"):
            parts = msg.split()
            if len(parts) == 2 and parts[1].isdigit():
                pid = parts[1]
                self.monitors[ctx]["projects"].add(pid)
                return True, (True, f"项目 {pid} 已添加到监控列表。", "bw_add")
            else:
                return True, (False, "用法：/bw add <项目ID>", "bw_add")
        if msg.startswith("/bw rm"):
            parts = msg.split()
            if len(parts) == 2 and parts[1].isdigit():
                pid = parts[1]
                self.monitors[ctx]["projects"].discard(pid)
                return True, (True, f"项目 {pid} 已从监控列表移除。", "bw_rm")
            else:
                return True, (False, "用法：/bw rm <项目ID>", "bw_rm")
        if msg == "/bw list":
            projects = self.monitors[ctx]["projects"]
            if projects:
                return True, (True, "当前监控项目ID：" + " ".join(projects), "bw_list")
            else:
                return True, (True, "当前未监控任何项目。", "bw_list")

        # 非本插件命令，返回 False
        return False, None

    def info(self):
        return {
            "name": "bwticket",
            "desc": "Bilibili 漫展票务状态监控",
            "help": (
                "/bw on - 开启监控\n"
                "/bw off - 关闭监控\n"
                "/bw add <项目ID> - 添加监控项目\n"
                "/bw rm <项目ID> - 移除监控项目\n"
                "/bw list - 查看监控项目列表\n"
                "当监控开启后，票务状态变化会自动通知。"
            ),
            "version": "v1.0",
            "author": "satori"
        }

    def _monitor_loop(self):
        """后台轮询所有监控项目并比对状态，遇变动即通知用户/群。"""
        while True:
            # 遍历所有启用监控的上下文，收集项目ID
            projects_to_check = set()
            for ctx, conf in self.monitors.items():
                if conf.get("enabled"):
                    projects_to_check.update(conf["projects"])
            # 对每个项目ID轮询
            for pid in projects_to_check:
                try:
                    url = f"https://show.bilibili.com/api/ticket/project/getV2?version=134&id={pid}"
                    res = requests.get(url, timeout=10)
                    data = res.json().get("data", {})
                except Exception:
                    continue
                # 提取票种状态：假设 data["sessionList"] 包含场次信息
                new_status = {}
                for session in data.get("sessionList", []):
                    sid = session.get("sessionId")
                    for ticket in session.get("ticketInfo", []):
                        tid = str(ticket.get("ticketId"))
                        name = ticket.get("ticketName", "")
                        status = ticket.get("statusDesc", "")
                        # 记录票种状态，键可以组合项目+场次+票ID等
                        new_status[(pid, sid, tid)] = (name, status)
                        # 比对旧状态
                        old = self.last_status.get(pid, {}).get((sid, tid))
                        if old is not None and old != status:
                            # 状态发生变化，通知所有开启监控且监控此项目的上下文
                            for ctx, conf in self.monitors.items():
                                if conf.get("enabled") and pid in conf["projects"]:
                                    date_str = datetime.now().strftime("%Y-%m-%d")
                                    weekday = ["日","一","二","三","四","五","六"][datetime.now().weekday()]
                                    atxt = f"[{date_str} 星期{weekday}({sid})] [{name}]({tid}) 状态变化：[{old}] -> [{status}]"
                                    # 使用平台发送消息
                                    ame = ame = AstrMessageEvent(
                                        message_str="", message_obj=None,
                                        gocq_platform=ame.gocq_platform,  # 使用当前平台实例
                                        platform=ame.platform, role=ame.role,
                                        global_object=ame.global_object
                                    )
                                    ame.gocq_platform.send(ctx[1], [Plain(atxt)])
                        # 更新单条票种状态
                        self.last_status.setdefault(pid, {})[(sid, tid)] = status
            time.sleep(30)  # 设置轮询间隔，避免频率过快引发风控

