# 职责：任务调度模块，统一负责读取配置、分配端口，按需组合调用功能模块
import json, os

import ins_fans
import ins_fans_location
import ins_url
import ins_url_user
import ins_dm


def _get_ports(params: dict) -> list:
    """统一端口解析，只在这里读取一次配置"""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(root_dir, "config", "running_states.json")
    if not os.path.exists(config_path):
        print("❌ [配置读取失败]: 找不到配置文件")
        return []
    with open(config_path, 'r', encoding='utf-8') as f:
        running_states = json.load(f)
    accounts = params.get('selected_accounts', [])
    return [running_states[acc]['port'] for acc in accounts if acc in running_states]


# ── 粉丝相关 ──────────────────────────────────────────────────────

def run_fans(params: dict):
    """只采集粉丝，写入数据库"""
    port_list = _get_ports(params)
    if not port_list:
        print("❌ 无可用端口，任务终止"); return
    bloggers     = params.get('bloggers', [])
    single_count = params.get('single_count', 0)
    interval     = params.get('interval', 0)
    ins_fans.run(port_list, bloggers, single_count, interval)


def run_fans_location(params: dict):
    """只查归属地（读 DB → 回写 country）"""
    port_list = _get_ports(params)
    if not port_list:
        print("❌ 无可用端口，任务终止"); return
    ins_fans_location.run(port_list)


def run_fans_full(params: dict):
    """采集粉丝 + 查归属地"""
    port_list = _get_ports(params)
    if not port_list:
        print("❌ 无可用端口，任务终止"); return
    bloggers     = params.get('bloggers', [])
    single_count = params.get('single_count', 0)
    interval     = params.get('interval', 0)
    ins_fans.run(port_list, bloggers, single_count, interval)
    ins_fans_location.run(port_list)


# ── 帖子相关 ──────────────────────────────────────────────────────

def run_posts(params: dict):
    """只采集博主帖子，写入数据库"""
    port_list = _get_ports(params)
    if not port_list:
        print("❌ 无可用端口，任务终止"); return
    bloggers = params.get('bloggers', [])
    url_max  = int(params.get('url_max', 50))
    c_days   = int(params.get('c_days', 0))
    min_l    = int(params.get('min_l', 0))
    min_c    = int(params.get('min_c', 0))
    ins_url.run(port_list, bloggers, url_max, c_days, min_l, min_c)


def run_posts_users(params: dict):
    """只采集帖子评论用户（读 DB → 写评论）"""
    port_list = _get_ports(params)
    if not port_list:
        print("❌ 无可用端口，任务终止"); return
    user_max  = int(params.get('c_max', 0))
    user_time = 90
    ins_url_user.run(port_list, user_max, user_time)


def run_posts_full(params: dict):
    """采集帖子 + 评论用户"""
    port_list = _get_ports(params)
    if not port_list:
        print("❌ 无可用端口，任务终止"); return
    bloggers  = params.get('bloggers', [])
    url_max   = int(params.get('url_max', 50))
    c_days    = int(params.get('c_days', 0))
    min_l     = int(params.get('min_l', 0))
    min_c     = int(params.get('min_c', 0))
    user_max  = int(params.get('c_max', 0))
    user_time = 90
    ins_url.run(port_list, bloggers, url_max, c_days, min_l, min_c)
    ins_url_user.run(port_list, user_max, user_time)


# ── DM 私信相关 ───────────────────────────────────────────────────

def run_ins_dm(params: dict):
    """Instagram 批量私信"""
    port_list = _get_ports(params)
    if not port_list:
        print("❌ 无可用端口，任务终止"); return

    def safe_int(key, default):
        try: return int(params.get(key, default))
        except: return default

    min_interval   = safe_int("min_interval", 30)
    max_interval   = safe_int("max_interval", 60)
    user_data_path = params.get("user_data_path") or ""
    script_path    = params.get("script_path") or ""

    print("\n📥📥📥 [IG_DM 后端就绪] 📥📥📥")
    print(f" ├─ 🔌 端口: {port_list}")
    print(f" ├─ ⏱️ 间隔: {min_interval}-{max_interval}")
    print(f" ├─ 📁 data: {user_data_path}")
    print(f" └─ 📜 script: {script_path}")
    print("-" * 50)

    usernames = ins_dm.load_usernames(user_data_path)
    scripts   = ins_dm.load_scripts(script_path)
    ins_dm.run(
        ports=port_list,
        usernames=usernames,
        scripts=scripts,
        min_interval=min_interval,
        max_interval=max_interval
    )
