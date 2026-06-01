#!/usr/bin/env python3
"""
社交媒体采集工具集 - 内置集成版
业务代码物理存放于 hermes-agent/sd_mcp/，无需任何外部依赖。
"""
import json
import os
import sys
import threading
import uuid
from datetime import datetime
from typing import Dict

# ── 内部路径（相对于 hermes-agent/ 根目录） ──────────────────────────────────
# tools/sd_mcp_tools.py → 上两级 = hermes-agent/
_HERMES_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SD_MCP_INTERNAL   = os.path.join(_HERMES_AGENT_ROOT, "sd_mcp")

# 将 sd_mcp/ 加入模块搜索路径，让 import ins_plcj 等直接生效
if _SD_MCP_INTERNAL not in sys.path:
    sys.path.insert(0, _SD_MCP_INTERNAL)

from tools.registry import registry, tool_error, tool_result

# ── 配置文件路径（与 sd_mcp 后端模块的路径计算逻辑完全一致） ─────────────────
# 后端模块：root_dir = dirname(dirname(__file__)) → hermes-agent/
# 因此 config/ 和 runtime_store.db 都在 hermes-agent/ 下
_WINDOWS_CONFIG_PATH = os.path.join(_SD_MCP_INTERNAL, "browser", "config", "windows_config.json")
_RUNNING_STATES_PATH = os.path.join(_HERMES_AGENT_ROOT, "config", "running_states.json")

# ── 任务状态跟踪 ─────────────────────────────────────────────────────────────
_tasks: Dict[str, dict] = {}
_tasks_lock  = threading.Lock()
_config_lock = threading.Lock()


# ── 配置文件辅助函数 ──────────────────────────────────────────────────────────

def _load_windows_config() -> dict:
    if not os.path.exists(_WINDOWS_CONFIG_PATH):
        return {"max_id": 0, "data": []}
    with open(_WINDOWS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_windows_config(cfg: dict):
    os.makedirs(os.path.dirname(_WINDOWS_CONFIG_PATH), exist_ok=True)
    with open(_WINDOWS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


def _sync_running_states(cfg: dict):
    os.makedirs(os.path.dirname(_RUNNING_STATES_PATH), exist_ok=True)
    existing = {}
    if os.path.exists(_RUNNING_STATES_PATH):
        try:
            with open(_RUNNING_STATES_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    for entry in cfg.get("data", []):
        existing[str(entry["id"])] = {"port": entry["port"]}
    with open(_RUNNING_STATES_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=4)


def _register_window(window_id: int, port: int, proxy: str):
    with _config_lock:
        cfg = _load_windows_config()
        existing_ids = {e["id"] for e in cfg["data"]}
        if window_id in existing_ids:
            for e in cfg["data"]:
                if e["id"] == window_id:
                    e["port"] = port
                    e["proxy"] = proxy
                    e["status"] = "running"
        else:
            cfg["data"].append({"id": window_id, "port": port, "proxy": proxy, "status": "running"})
            cfg["max_id"] = max(cfg.get("max_id", 0), window_id)
        _save_windows_config(cfg)
        _sync_running_states(cfg)


def _get_running_states() -> dict:
    result = {}
    for entry in _load_windows_config().get("data", []):
        if entry.get("status") == "running":
            result[str(entry["id"])] = {"port": entry["port"]}
    if os.path.exists(_RUNNING_STATES_PATH):
        try:
            with open(_RUNNING_STATES_PATH, "r", encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    if k not in result:
                        result[k] = v
        except Exception:
            pass
    return result


def _start_task(display_name: str, module, params: dict) -> str:
    task_id = str(uuid.uuid4())[:8]

    def _run():
        try:
            result = module.main_entry(params)
            with _tasks_lock:
                _tasks[task_id]["status"] = "done"
                _tasks[task_id]["result"] = result
        except Exception as exc:
            with _tasks_lock:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["error"] = str(exc)

    with _tasks_lock:
        _tasks[task_id] = {
            "name": display_name,
            "status": "running",
            "start_time": datetime.now().strftime("%H:%M:%S"),
            "error": None,
        }
    threading.Thread(target=_run, daemon=True).start()
    return task_id


# ── 可用性检查（检测内置目录是否完整） ───────────────────────────────────────

def _check_sd_mcp() -> bool:
    return os.path.isdir(_SD_MCP_INTERNAL) and os.path.isfile(
        os.path.join(_SD_MCP_INTERNAL, "userwindow.py")
    )


# ════════════════════════════════════════════════════════════════════════════
# Tool 1: sd_list_accounts
# ════════════════════════════════════════════════════════════════════════════

_LIST_ACCOUNTS_SCHEMA = {
    "name": "sd_list_accounts",
    "description": (
        "列出所有浏览器窗口的真实在线状态（实际连接 CDP 端口检测，非仅读配置文件）。\n"
        "同时更新 windows_config.json 中的 status 字段。\n"
        "调用任何采集/登录工具前，请先用此工具确认哪些窗口实际可用。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def _ping_cdp(port: int, timeout: float = 2.0) -> bool:
    """尝试连接 CDP 端口，返回 True 表示窗口存活"""
    try:
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout)
        return True
    except Exception:
        return False


def _handle_list_accounts(args, **kw) -> str:
    try:
        states = _get_running_states()
        if not states:
            return tool_result({
                "status": "no_accounts",
                "message": "配置文件中无任何窗口记录，请先用 sd_browser_launch_windows 启动窗口。",
                "accounts": [],
            })

        alive, dead = [], []
        for acc, info in states.items():
            port = info.get("port")
            online = _ping_cdp(port) if port else False
            entry = {"name": acc, "port": port, "alive": online}
            (alive if online else dead).append(entry)

        # 同步更新 windows_config.json 中的 status 字段
        with _config_lock:
            cfg = _load_windows_config()
            alive_ports = {a["port"] for a in alive}
            for entry in cfg.get("data", []):
                entry["status"] = "running" if entry.get("port") in alive_ports else "stopped"
            _save_windows_config(cfg)

        return tool_result({
            "status": "ok",
            "alive_count": len(alive),
            "dead_count": len(dead),
            "alive": alive,
            "dead": dead,
            "tip": "只有 alive=true 的窗口才能接收任务。",
        })
    except Exception as e:
        return tool_error(f"检测账号状态失败: {e}")


registry.register(
    name="sd_list_accounts",
    toolset="sd_mcp",
    schema=_LIST_ACCOUNTS_SCHEMA,
    handler=_handle_list_accounts,
    check_fn=_check_sd_mcp,
    emoji="📋",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 2: sd_check_task
# ════════════════════════════════════════════════════════════════════════════

_CHECK_TASK_SCHEMA = {
    "name": "sd_check_task",
    "description": (
        "查询后台任务的执行状态（running / done / error）。\n"
        "采集任务通常耗时较长，可反复调用此工具跟踪进度。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID，由启动任务的工具返回"}
        },
        "required": ["task_id"],
    },
}


def _handle_check_task(args, **kw) -> str:
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return tool_error("请提供 task_id")
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return tool_error(f"未找到任务 ID：{task_id}")
    return tool_result({
        "task_id": task_id,
        "name": task["name"],
        "status": task["status"],
        "start_time": task["start_time"],
        "error": task.get("error"),
        "result": task.get("result"),
    })


registry.register(
    name="sd_check_task",
    toolset="sd_mcp",
    schema=_CHECK_TASK_SCHEMA,
    handler=_handle_check_task,
    check_fn=_check_sd_mcp,
    emoji="🔍",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 3: sd_browser_launch_windows
# ════════════════════════════════════════════════════════════════════════════

_BROWSER_LAUNCH_SCHEMA = {
    "name": "sd_browser_launch_windows",
    "description": (
        "启动一个或多个防检测浏览器窗口。\n"
        "端口由系统自动分配（9000 + 窗口ID），无需用户指定。\n"
        "每个窗口独立指纹（CPU核心数、UA、WebGL），支持代理（自动识别时区和语言），\n"
        "启动后自动注册到云控，之后即可用窗口ID作为账号名调用采集工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "window_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "要启动的窗口ID列表，如 [1, 2, 3]。端口自动为 9001、9002、9003。",
            },
            "proxy_list": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": (
                    "可选，每个窗口对应的代理，顺序与 window_ids 一致。\n"
                    "格式：ip:port:user:pass。不需要代理可不填，"
                    "或填空字符串占位，如 ['1.2.3.4:8080:u:p', '', '5.6.7.8:8080:u:p']"
                ),
            },
        },
        "required": ["window_ids"],
    },
}


def _handle_browser_launch_windows(args, **kw) -> str:
    try:
        import userwindow
    except ImportError as e:
        return tool_error(f"无法导入 userwindow 模块: {e}")
    # 强制覆盖路径，确保无论从哪里 import 都指向正确的 browser/
    userwindow.BASE_DIR    = _SD_MCP_INTERNAL
    userwindow.CHROME_PATH = os.path.join(_SD_MCP_INTERNAL, "browser", "Application", "chrome.exe")

    window_ids = args.get("window_ids", [])
    proxy_list = args.get("proxy_list", [])
    if not window_ids:
        return tool_error("请提供至少一个窗口ID（window_ids 不能为空）")

    results = []
    for i, wid in enumerate(window_ids):
        wid = int(wid)
        port = 9000 + wid
        proxy = str(proxy_list[i] if i < len(proxy_list) else "") or ""
        try:
            pid = userwindow.launch_perfect_matrix(str(wid), proxy, port)
            if pid:
                _register_window(wid, port, proxy)
                proxy_info = f"代理: {proxy.split(':')[0]}" if proxy else "无代理"
                results.append({"window_id": wid, "port": port, "proxy_info": proxy_info, "pid": pid, "status": "ok"})
            else:
                results.append({"window_id": wid, "port": port, "status": "failed", "reason": "Chrome 进程未创建"})
        except Exception as exc:
            results.append({"window_id": wid, "port": port, "status": "error", "reason": str(exc)})

    return tool_result({
        "total": len(window_ids),
        "results": results,
        "tip": "稍等几秒让浏览器完成加载，再用 sd_list_accounts 确认账号上线。",
    })


registry.register(
    name="sd_browser_launch_windows",
    toolset="sd_mcp",
    schema=_BROWSER_LAUNCH_SCHEMA,
    handler=_handle_browser_launch_windows,
    check_fn=_check_sd_mcp,
    emoji="🌐",
)



# ════════════════════════════════════════════════════════════════════════════
# Tool 4: sd_open_url
# ════════════════════════════════════════════════════════════════════════════

_OPEN_URL_SCHEMA = {
    "name": "sd_open_url",
    "description": (
        "在指定浏览器窗口中打开目标网址。\n"
        "支持三种模式：\n"
        "  - 单窗口：指定 account_name，只打开该窗口\n"
        "  - 多窗口：指定 account_names 列表，并发打开\n"
        "  - 全部窗口：两者都不填，所有在线窗口同时导航\n"
        "操作同步返回每个窗口的成功/失败状态。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "目标网址（可省略 https:// 前缀，系统自动补全）",
            },
            "account_name": {
                "type": "string",
                "description": "（可选）单个窗口的账号名。填写后仅操作该窗口。",
            },
            "account_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "（可选）多个窗口的账号名列表。并发在这些窗口中打开网址。",
            },
        },
        "required": ["url"],
    },
}


def _handle_open_url(args, **kw) -> str:
    try:
        import open_url
    except ImportError as e:
        return tool_error(f"无法导入 open_url: {e}")
    try:
        result = open_url.main_entry(args)
        return tool_result(result)
    except Exception as e:
        return tool_error(f"打开网址失败: {e}")


registry.register(
    name="sd_open_url",
    toolset="sd_mcp",
    schema=_OPEN_URL_SCHEMA,
    handler=_handle_open_url,
    check_fn=_check_sd_mcp,
    emoji="🔗",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 5: sd_cdp_login
# ════════════════════════════════════════════════════════════════════════════

_CDP_LOGIN_SCHEMA = {
    "name": "sd_cdp_login",
    "description": (
        "通过 CDP 协议接口（GraphQL + 密码加密）登录 Instagram 或 Facebook 账号。\n"
        "支持 TOTP 自动 2FA（需提供 Base32 密钥，不是六位数字验证码）。\n\n"
        "【分配规则 - 严格执行】\n"
        "一个账号只能登录一个窗口，一个窗口只能登录一个账号，严格一对一。\n"
        "解析账号文件后按顺序分配：第1个账号→窗口1，第2个账号→窗口2，以此类推。\n"
        "账号数量超过在线窗口数时，多余的账号不分配，不报错，只登可用窗口数量。\n"
        "绝对禁止：同一账号出现在多个窗口，或同一窗口出现多个账号。\n\n"
        "── 账号文件格式解析规则 ──\n"
        "【重要】无论何种分隔符，字段顺序固定：第1段=账号，第2段=密码，第3段=2FA密钥(可选)\n"
        "绝对不能把密码当账号、把账号当密码，顺序不可颠倒。\n\n"
        "支持的分隔符（自动识别，每行只有一种）：\n"
        "  :   →  账号:密码  或  账号:密码:2FA密钥\n"
        "  |   →  账号|密码  或  账号|密码|2FA密钥\n"
        "  ----→  账号----密码  或  账号----密码----2FA密钥\n"
        "  空格→  账号 密码\n\n"
        "解析示例：\n"
        "  alice@mail.com:P@ssw0rd              → username=alice@mail.com, password=P@ssw0rd\n"
        "  bob|secret123|JBSWY3DPEHPK3PXP       → username=bob, password=secret123, fa2_secret=JBSWY3DPEHPK3PXP\n"
        "  61590476394334----hcnvyfchjvf         → username=61590476394334, password=hcnvyfchjvf\n\n"
        "── 调用方式 ──\n"
        "批量：传 assignments 列表，每项指定 window + platform + username + password [+ fa2_secret]\n"
        "单次：直接传 window_id + platform + username + password [+ fa2_secret]\n\n"
        "── 注意 ──\n"
        "使用前请先用 sd_list_accounts 确认目标窗口在线；\n"
        "登录耗时约 15~60 秒（2FA 页面加载需要时间），用 sd_check_task 跟踪任务。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "description": (
                    "批量登录列表，解析账号文件后将每行映射成一项，示例：\n"
                    '[{"window":"1","platform":"instagram","username":"u1","password":"p1"},'
                    '{"window":"2","platform":"facebook","username":"u2","password":"p2","fa2_secret":"SECRET"}]'
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "window":     {"type": "string", "description": "窗口 ID（账号名）"},
                        "platform":   {"type": "string", "enum": ["instagram", "facebook"],
                                       "description": "目标平台"},
                        "username":   {"type": "string", "description": "登录用户名或邮箱"},
                        "password":   {"type": "string", "description": "登录密码"},
                        "fa2_secret": {"type": "string", "description": "TOTP Base32 密钥（可选）"},
                    },
                    "required": ["window", "platform", "username", "password"],
                },
            },
            "window_id": {
                "type": "string",
                "description": "单次模式：目标窗口 ID",
            },
            "platform": {
                "type": "string",
                "enum": ["instagram", "facebook"],
                "description": "单次模式：目标平台",
            },
            "username": {
                "type": "string",
                "description": "单次模式：登录用户名或邮箱",
            },
            "password": {
                "type": "string",
                "description": "单次模式：登录密码",
            },
            "fa2_secret": {
                "type": "string",
                "description": "单次模式：TOTP Base32 密钥（账号无 2FA 可留空）",
            },
        },
        "required": [],
    },
}


def _handle_cdp_login(args, **kw) -> str:
    try:
        import cdp_login
    except ImportError as e:
        return tool_error(f"无法导入 cdp_login: {e}")
    try:
        result = cdp_login.main_entry(args)
        return tool_result(result)
    except Exception as e:
        return tool_error(f"登录执行失败: {e}")


registry.register(
    name="sd_cdp_login",
    toolset="sd_mcp",
    schema=_CDP_LOGIN_SCHEMA,
    handler=_handle_cdp_login,
    check_fn=_check_sd_mcp,
    emoji="🔐",
)


# ── 公共辅助：用 browsers_work 的某个函数启动后台任务 ─────────────────────────

def _bw_task(display_name: str, fn_name: str, args: dict) -> str:
    """通过 browsers_work.<fn_name>(args) 启动后台任务，返回 task_id"""
    import types
    try:
        import browsers_work as _bw
    except ImportError as e:
        return tool_error(f"无法导入 browsers_work: {e}")
    fn = getattr(_bw, fn_name, None)
    if fn is None:
        return tool_error(f"browsers_work 中找不到函数 {fn_name}")
    m = types.SimpleNamespace(main_entry=fn)
    tid = _start_task(display_name, m, args)
    return tid


# ════════════════════════════════════════════════════════════════════════════
# Tool 6: sd_ins_collect_fans  粉丝采集
# ════════════════════════════════════════════════════════════════════════════

_INS_FANS_SCHEMA = {
    "name": "sd_ins_collect_fans",
    "description": (
        "采集 Instagram 博主的粉丝列表，结果写入数据库 ins_followers 表。\n"
        "只采集粉丝用户名和 ID，不查询归属地。\n"
        "若需同时查归属地，请改用 sd_ins_fans_full。\n"
        "适合场景：只需要粉丝名单，不关心地区分布。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selected_accounts": {
                "type": "array", "items": {"type": "string"},
                "description": "使用的窗口账号列表，用 sd_list_accounts 查询",
            },
            "bloggers": {
                "type": "array", "items": {"type": "string"},
                "description": "目标博主用户名列表，如 ['nasa', 'natgeo']",
            },
            "single_count": {"type": "integer", "default": 100, "description": "每个博主最多采集粉丝数"},
            "interval":     {"type": "integer", "default": 3,   "description": "博主间采集间隔秒数"},
        },
        "required": ["selected_accounts", "bloggers"],
    },
}


def _handle_ins_collect_fans(args, **kw) -> str:
    tid = _bw_task("Instagram粉丝采集", "run_fans", args)
    if tid.startswith('{"error'):
        return tid
    return tool_result({"task_id": tid, "status": "started",
                        "message": f"粉丝采集已启动，用 sd_check_task('{tid}') 查看进度。",
                        "output": "结果写入 runtime_store.db → ins_followers 表"})


registry.register(
    name="sd_ins_collect_fans",
    toolset="sd_mcp",
    schema=_INS_FANS_SCHEMA,
    handler=_handle_ins_collect_fans,
    check_fn=_check_sd_mcp,
    emoji="👥",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 7: sd_ins_fans_location  归属地查询
# ════════════════════════════════════════════════════════════════════════════

_INS_FANS_LOC_SCHEMA = {
    "name": "sd_ins_fans_location",
    "description": (
        "查询数据库 ins_followers 表中 country 为空的粉丝的归属地，并回写 country 字段。\n"
        "前提：必须先运行过 sd_ins_collect_fans，数据库中有粉丝数据。\n"
        "适合场景：已有粉丝列表，补充查询地区信息。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selected_accounts": {
                "type": "array", "items": {"type": "string"},
                "description": "使用的窗口账号列表",
            },
        },
        "required": ["selected_accounts"],
    },
}


def _handle_ins_fans_location(args, **kw) -> str:
    tid = _bw_task("Instagram粉丝归属地查询", "run_fans_location", args)
    if tid.startswith('{"error'):
        return tid
    return tool_result({"task_id": tid, "status": "started",
                        "message": f"归属地查询已启动，用 sd_check_task('{tid}') 查看进度。"})


registry.register(
    name="sd_ins_fans_location",
    toolset="sd_mcp",
    schema=_INS_FANS_LOC_SCHEMA,
    handler=_handle_ins_fans_location,
    check_fn=_check_sd_mcp,
    emoji="🌍",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 8: sd_ins_fans_full  粉丝采集 + 归属地（完整流程）
# ════════════════════════════════════════════════════════════════════════════

_INS_FANS_FULL_SCHEMA = {
    "name": "sd_ins_fans_full",
    "description": (
        "采集 Instagram 博主粉丝列表，并自动查询每位粉丝的归属地，一步完成。\n"
        "等同于先运行 sd_ins_collect_fans 再运行 sd_ins_fans_location。\n"
        "适合场景：需要带地区信息的粉丝名单，比如筛选特定国家的用户。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selected_accounts": {
                "type": "array", "items": {"type": "string"},
                "description": "使用的窗口账号列表",
            },
            "bloggers": {
                "type": "array", "items": {"type": "string"},
                "description": "目标博主用户名列表",
            },
            "single_count": {"type": "integer", "default": 100, "description": "每个博主最多采集粉丝数"},
            "interval":     {"type": "integer", "default": 3,   "description": "博主间采集间隔秒数"},
        },
        "required": ["selected_accounts", "bloggers"],
    },
}


def _handle_ins_fans_full(args, **kw) -> str:
    tid = _bw_task("Instagram粉丝采集+归属地", "run_fans_full", args)
    if tid.startswith('{"error'):
        return tid
    return tool_result({"task_id": tid, "status": "started",
                        "message": f"完整粉丝采集已启动，用 sd_check_task('{tid}') 查看进度。",
                        "output": "结果写入 runtime_store.db → ins_followers 表（含 country 字段）"})


registry.register(
    name="sd_ins_fans_full",
    toolset="sd_mcp",
    schema=_INS_FANS_FULL_SCHEMA,
    handler=_handle_ins_fans_full,
    check_fn=_check_sd_mcp,
    emoji="👥🌍",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 9: sd_ins_collect_posts  帖子采集
# ════════════════════════════════════════════════════════════════════════════

_INS_POSTS_SCHEMA = {
    "name": "sd_ins_collect_posts",
    "description": (
        "采集 Instagram 博主的帖子列表（URL、点赞数、评论数、发布时间），写入 ins_Comment 表。\n"
        "只采集帖子元数据，不采集评论用户。\n"
        "若需同时采集评论用户，请改用 sd_ins_posts_full。\n"
        "适合场景：筛选高互动帖子，后续批量采集评论用户作为私信目标。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selected_accounts": {
                "type": "array", "items": {"type": "string"},
                "description": "使用的窗口账号列表",
            },
            "bloggers": {
                "type": "array", "items": {"type": "string"},
                "description": "目标博主用户名列表",
            },
            "url_max":  {"type": "integer", "default": 50, "description": "每个博主最多采集帖子数"},
            "c_days":   {"type": "integer", "default": 7,  "description": "只采集最近 N 天内的帖子"},
            "min_l":    {"type": "integer", "default": 0,  "description": "帖子最低点赞数过滤"},
            "min_c":    {"type": "integer", "default": 0,  "description": "帖子最低评论数过滤"},
        },
        "required": ["selected_accounts", "bloggers"],
    },
}


def _handle_ins_collect_posts(args, **kw) -> str:
    tid = _bw_task("Instagram帖子采集", "run_posts", args)
    if tid.startswith('{"error'):
        return tid
    return tool_result({"task_id": tid, "status": "started",
                        "message": f"帖子采集已启动，用 sd_check_task('{tid}') 查看进度。",
                        "output": "结果写入 runtime_store.db → ins_Comment 表"})


registry.register(
    name="sd_ins_collect_posts",
    toolset="sd_mcp",
    schema=_INS_POSTS_SCHEMA,
    handler=_handle_ins_collect_posts,
    check_fn=_check_sd_mcp,
    emoji="📸",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 10: sd_ins_collect_comments  评论用户采集
# ════════════════════════════════════════════════════════════════════════════

_INS_COMMENTS_SCHEMA = {
    "name": "sd_ins_collect_comments",
    "description": (
        "从数据库 ins_Comment 表读取已采集的帖子，采集每条帖子下的评论用户，并回写数据库。\n"
        "前提：必须先运行过 sd_ins_collect_posts，数据库中有帖子数据。\n"
        "适合场景：获取高互动帖子的评论用户名单，用于精准私信营销。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selected_accounts": {
                "type": "array", "items": {"type": "string"},
                "description": "使用的窗口账号列表",
            },
            "c_max":    {"type": "integer", "default": 100, "description": "每条帖子最多采集评论数"},
        },
        "required": ["selected_accounts"],
    },
}


def _handle_ins_collect_comments(args, **kw) -> str:
    tid = _bw_task("Instagram评论用户采集", "run_posts_users", args)
    if tid.startswith('{"error'):
        return tid
    return tool_result({"task_id": tid, "status": "started",
                        "message": f"评论采集已启动，用 sd_check_task('{tid}') 查看进度。"})


registry.register(
    name="sd_ins_collect_comments",
    toolset="sd_mcp",
    schema=_INS_COMMENTS_SCHEMA,
    handler=_handle_ins_collect_comments,
    check_fn=_check_sd_mcp,
    emoji="💬",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 11: sd_ins_posts_full  帖子 + 评论用户（完整流程）
# ════════════════════════════════════════════════════════════════════════════

_INS_POSTS_FULL_SCHEMA = {
    "name": "sd_ins_posts_full",
    "description": (
        "采集 Instagram 博主帖子，并自动采集每条帖子下的评论用户，一步完成。\n"
        "等同于先运行 sd_ins_collect_posts 再运行 sd_ins_collect_comments。\n"
        "适合场景：完整的帖子评论用户挖掘流程，获取潜在私信目标。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selected_accounts": {
                "type": "array", "items": {"type": "string"},
                "description": "使用的窗口账号列表",
            },
            "bloggers": {
                "type": "array", "items": {"type": "string"},
                "description": "目标博主用户名列表",
            },
            "url_max":  {"type": "integer", "default": 50,  "description": "每个博主最多采集帖子数"},
            "c_days":   {"type": "integer", "default": 7,   "description": "只采集最近 N 天内的帖子"},
            "min_l":    {"type": "integer", "default": 0,   "description": "帖子最低点赞数过滤"},
            "min_c":    {"type": "integer", "default": 0,   "description": "帖子最低评论数过滤"},
            "c_max":    {"type": "integer", "default": 100, "description": "每条帖子最多采集评论数"},
        },
        "required": ["selected_accounts", "bloggers"],
    },
}


def _handle_ins_posts_full(args, **kw) -> str:
    tid = _bw_task("Instagram帖子+评论完整采集", "run_posts_full", args)
    if tid.startswith('{"error'):
        return tid
    return tool_result({"task_id": tid, "status": "started",
                        "message": f"完整帖子采集已启动，用 sd_check_task('{tid}') 查看进度。",
                        "output": "结果写入 runtime_store.db → ins_Comment 表"})


registry.register(
    name="sd_ins_posts_full",
    toolset="sd_mcp",
    schema=_INS_POSTS_FULL_SCHEMA,
    handler=_handle_ins_posts_full,
    check_fn=_check_sd_mcp,
    emoji="📸💬",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 12: sd_ins_send_dm  Instagram 批量私信
# ════════════════════════════════════════════════════════════════════════════

_INS_DM_SCHEMA = {
    "name": "sd_ins_send_dm",
    "description": (
        "向 Instagram 用户列表批量发送私信。\n"
        "目标用户从文件读取（每行一个用户名），话术从文件读取（每行一条，轮换发送）。\n"
        "多窗口并行，每个窗口分配独立用户子集，支持设置发送间隔防风控。\n"
        "适合场景：对采集到的粉丝或评论用户进行批量私信营销。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selected_accounts": {
                "type": "array", "items": {"type": "string"},
                "description": "使用的窗口账号列表",
            },
            "user_data_path": {
                "type": "string",
                "description": "目标用户名列表文件路径（每行一个 Instagram 用户名）",
            },
            "script_path": {
                "type": "string",
                "description": "私信话术文件路径（每行一条话术，多条时轮换发送）",
            },
            "min_interval": {"type": "integer", "default": 30, "description": "两次发送最小间隔秒数"},
            "max_interval": {"type": "integer", "default": 60, "description": "两次发送最大间隔秒数"},
        },
        "required": ["selected_accounts", "user_data_path", "script_path"],
    },
}


def _handle_ins_send_dm(args, **kw) -> str:
    tid = _bw_task("Instagram批量私信", "run_ins_dm", args)
    if tid.startswith('{"error'):
        return tid
    return tool_result({"task_id": tid, "status": "started",
                        "message": f"私信任务已启动，用 sd_check_task('{tid}') 查看进度。"})


registry.register(
    name="sd_ins_send_dm",
    toolset="sd_mcp",
    schema=_INS_DM_SCHEMA,
    handler=_handle_ins_send_dm,
    check_fn=_check_sd_mcp,
    emoji="✉️",
)


# ════════════════════════════════════════════════════════════════════════════
# Tool 13: sd_db_manager  数据库管理
# ════════════════════════════════════════════════════════════════════════════

_DB_MANAGER_SCHEMA = {
    "name": "sd_db_manager",
    "description": (
        "数据库管理工具，支持统计、清空、导出三种操作。\n\n"
        "── 数据库表对照（自然语言 → table 参数）──\n"
        "  说「ins粉丝/Instagram粉丝/粉丝采集数据」  → table=ins_followers\n"
        "  说「ins评论/Instagram评论/帖子评论数据」   → table=ins_Comment\n"
        "  说「Threads粉丝/th粉丝数据」              → table=th_followers\n"
        "  说「推特粉丝/X粉丝/twitter粉丝数据」       → table=x_followers\n"
        "  说「全部/所有数据/清空所有」               → table=all\n\n"
        "── 操作说明 ──\n"
        "  stats  : 查看所有表的数据量，说「数据库有多少数据/看一下采集了多少」时使用\n"
        "  clear  : 清空指定表，说「清空/删除/重置 xx 数据」时使用\n"
        "  export : 导出到桌面，文件名自动命名为「平台_日期.格式」\n"
        "           · format=json → 完整数据（所有字段）\n"
        "           · format=txt  → 纯用户名列表每行一个（去重），说「导出用户名/私信名单」时用 txt\n"
        "           · country_filter 可选，说「只要美国的/筛选日本用户」时填写\n\n"
        "── 使用时机 ──\n"
        "  · 采集完问「采集了多少」→ stats\n"
        "  · 「把 ins 粉丝数据清空」→ clear + ins_followers\n"
        "  · 「导出私信名单到桌面」→ export + txt\n"
        "  · 「把采集的数据导出备份」→ export + json"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["stats", "clear", "export"],
                "description": "操作类型：stats=统计, clear=清空, export=导出",
            },
            "table": {
                "type": "string",
                "enum": ["ins_followers", "ins_Comment", "th_followers", "x_followers", "all"],
                "description": "目标表名（clear/export 必填；stats 可省略）",
            },
            "format": {
                "type": "string",
                "enum": ["json", "txt"],
                "default": "json",
                "description": "导出格式：json=完整数据，txt=纯用户名列表（export 时使用）",
            },
            "country_filter": {
                "type": "string",
                "description": "按国家过滤导出，如 '美国'、'日本'（可选，仅 export 时有效）",
            },
        },
        "required": ["action"],
    },
}


def _handle_db_manager(args, **kw) -> str:
    try:
        import db_manager
    except ImportError as e:
        return tool_error(f"无法导入 db_manager: {e}")
    try:
        result = db_manager.main_entry(args)
        return tool_result(result)
    except Exception as e:
        return tool_error(f"数据库操作失败: {e}")


registry.register(
    name="sd_db_manager",
    toolset="sd_mcp",
    schema=_DB_MANAGER_SCHEMA,
    handler=_handle_db_manager,
    check_fn=_check_sd_mcp,
    emoji="🗄️",
)
