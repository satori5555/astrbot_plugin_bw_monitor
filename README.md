# 🚀 astrbot_plugin_bw_monitor

> Bilibili Show 票务监控插件，实时推送项目余票变化。  
> 适配 [AstrBot](https://github.com/BetaCatX/astrbot)。

---

## ✨ 功能简介

✅ 实时监控 Bilibili Show 项目票务状态  
✅ 支持群聊、私聊、不同会话隔离  
✅ 自动轮询接口，每秒刷新  
✅ 检测到票务状态变化时自动推送  
✅ 命令查询项目当前票务信息  
✅ 自动保存各聊天会话配置到 `settings.json`  
✅ 支持：
- 场贩（linkgoods）
- 多日程项目（sales_dates）
- 普通单日项目
✅ 推送信息包含：
- 日期（若有）
- 场次（screen_name）
- 票种名称
- 票价
- 当前状态

---

## 📦 安装方法

1. 将本插件源码放入 AstrBot 的插件目录：

```
astrbot_plugins/astrbot_plugin_bw_monitor
```

2. 确保插件内有以下文件：

```
main.py
settings.json (插件会自动生成)
```

3. 启动 AstrBot。

---

## 🔧 JSON 配置

插件会自动生成一个 `settings.json`：

```json
{
  "aiocqhttp:GroupMessage:123456789": {
    "switch": true,
    "projects": ["102194", "266087"]
  }
}
```

- 每个 key 是 unified_msg_origin（即聊天会话唯一标识）  
- `switch`: 是否开启监控  
- `projects`: 当前会话监控的项目 ID 列表

---

## 💻 使用方法

### 开启监控

```
/bw on
```

✅ 启动本群或本私聊的轮询。

---

### 关闭监控

```
/bw off
```

---

### 添加项目

```
/bw add <项目ID>
```

例：

```
/bw add 102194
```

---

### 移除项目

```
/bw rm <项目ID>
```

例：

```
/bw rm 102194
```

---

### 查看已添加项目

```
/bw list
```

---

### 查询项目当前票务

```
/bw now <项目ID>
```

例：

```
/bw now 102194
```

机器人将推送完整票务信息。

---

## 📈 推送示例

**当状态发生变化时，或使用 /bw now，会收到消息：**

```
🎫 项目名称：上海·BILIBILI MACRO LINK 2025
2025-07-11 主舞台 VIP票 ¥1880 - 预售中
2025-07-11 主舞台 A级票 ¥1280 - 已售罄
```

或者没有日期时：

```
🎫 项目名称：上海·BilibiliWorld 2025
主舞台 VIP票 ¥1880 - 已售罄
```

---

## 💡 注意事项

- 每个群聊或私聊独立配置，互不影响  
- JSON 文件 `settings.json` 会在插件目录下自动生成  
- 接口可能受 Bilibili 风控限制  
- 本插件仅用于学习与交流，禁止用于商业或非法用途

---

## 📝 作者

- **satori5555**  
- License: MIT

---

需要更多帮助？欢迎提 Issue！ 🚀
