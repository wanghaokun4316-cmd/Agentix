"""
数据库管理模块
支持：统计、清空、导出（JSON / TXT）
数据库：hermes-agent/sd_mcp/runtime_store.db
"""
import os
import json
import sqlite3
from datetime import datetime

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(_THIS_DIR, "runtime_store.db")

# ── 表配置：每张表的平台名、主要字段、TXT 导出列 ──────────────────────────────
_TABLE_META = {
    "ins_followers": {
        "platform": "instagram_fans",
        "fields":   ["blogger", "user", "user_id", "country"],
        "txt_col":  "user",          # TXT 导出用这一列（每行一个用户名）
        "desc":     "Instagram 粉丝",
    },
    "ins_Comment": {
        "platform": "instagram_comments",
        "fields":   ["id", "url", "like_count", "comment_count", "taken_at",
                     "time", "caption", "name", "username", "usertime", "text", "user_taken"],
        "txt_col":  "username",      # TXT 导出：评论用户名（去重）
        "desc":     "Instagram 帖子/评论",
    },
    "th_followers": {
        "platform": "threads_fans",
        "fields":   ["blogger", "user", "user_id", "country"],
        "txt_col":  "user",
        "desc":     "Threads 粉丝",
    },
    "x_followers": {
        "platform": "twitter_fans",
        "fields":   ["blogger", "user", "user_id", "country"],
        "txt_col":  "user",
        "desc":     "X/Twitter 粉丝",
    },
}

_DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


def _connect():
    return sqlite3.connect(DB_FILE)


def _today():
    return datetime.now().strftime("%Y%m%d")


# ── 统计 ───────────────────────────────────────────────────────────────────────

def stats() -> dict:
    if not os.path.exists(DB_FILE):
        return {"error": f"数据库不存在: {DB_FILE}"}
    conn = _connect()
    c = conn.cursor()
    result = {}
    for table, meta in _TABLE_META.items():
        try:
            c.execute(f"SELECT COUNT(*) FROM [{table}]")
            total = c.fetchone()[0]
            c.execute(f"SELECT COUNT(*) FROM [{table}] WHERE [{meta['txt_col']}] IS NOT NULL AND [{meta['txt_col']}] != ''")
            valid = c.fetchone()[0]
            # 有 country 字段的表统计地区覆盖率
            if "country" in meta["fields"]:
                c.execute(f"SELECT COUNT(*) FROM [{table}] WHERE country IS NOT NULL AND country != ''")
                with_country = c.fetchone()[0]
            else:
                with_country = None
            result[table] = {
                "desc": meta["desc"],
                "total_rows": total,
                "valid_rows": valid,
                "with_country": with_country,
            }
        except Exception as e:
            result[table] = {"error": str(e)}
    conn.close()
    return result


# ── 清空 ───────────────────────────────────────────────────────────────────────

def clear(table: str) -> dict:
    if not os.path.exists(DB_FILE):
        return {"error": f"数据库不存在: {DB_FILE}"}
    if table == "all":
        tables = list(_TABLE_META.keys())
    elif table in _TABLE_META:
        tables = [table]
    else:
        return {"error": f"未知表: {table}，可选: {list(_TABLE_META.keys())} 或 all"}

    conn = _connect()
    c = conn.cursor()
    results = {}
    for t in tables:
        try:
            c.execute(f"SELECT COUNT(*) FROM [{t}]")
            before = c.fetchone()[0]
            c.execute(f"DELETE FROM [{t}]")
            results[t] = {"deleted": before, "status": "ok"}
        except Exception as e:
            results[t] = {"error": str(e)}
    conn.commit()
    conn.close()
    return results


# ── 导出 ───────────────────────────────────────────────────────────────────────

def export(table: str, fmt: str = "json", country_filter: str = "") -> dict:
    if not os.path.exists(DB_FILE):
        return {"error": f"数据库不存在: {DB_FILE}"}
    if table not in _TABLE_META:
        return {"error": f"未知表: {table}，可选: {list(_TABLE_META.keys())}"}

    meta = _TABLE_META[table]
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 构建查询（支持按国家过滤）
    if country_filter and "country" in meta["fields"]:
        c.execute(f"SELECT * FROM [{table}] WHERE country LIKE ?", (f"%{country_filter}%",))
    else:
        c.execute(f"SELECT * FROM [{table}]")

    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    if not rows:
        return {"error": "无数据可导出", "table": table, "rows": 0}

    os.makedirs(_DESKTOP, exist_ok=True)
    filename = f"{meta['platform']}_{_today()}.{fmt}"
    filepath = os.path.join(_DESKTOP, filename)

    if fmt == "json":
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)

    elif fmt == "txt":
        txt_col = meta["txt_col"]
        # 取指定列，去重，过滤空值
        values = list(dict.fromkeys(
            str(r[txt_col]).strip()
            for r in rows
            if r.get(txt_col) and str(r[txt_col]).strip()
        ))
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(values))
        rows = values  # 用于返回计数

    else:
        return {"error": f"不支持的格式: {fmt}，可选: json / txt"}

    return {
        "status": "ok",
        "table": table,
        "desc": meta["desc"],
        "rows": len(rows),
        "format": fmt,
        "path": filepath,
        "country_filter": country_filter or None,
    }


# ── 统一入口 ───────────────────────────────────────────────────────────────────

def main_entry(params: dict) -> dict:
    action = params.get("action", "stats")

    if action == "stats":
        return stats()

    elif action == "clear":
        table = params.get("table", "")
        if not table:
            return {"error": "clear 操作需要指定 table"}
        return clear(table)

    elif action == "export":
        table = params.get("table", "")
        if not table:
            return {"error": "export 操作需要指定 table"}
        fmt = params.get("format", "json").lower()
        country_filter = params.get("country_filter", "")
        return export(table, fmt, country_filter)

    else:
        return {"error": f"未知操作: {action}，可选: stats / clear / export"}
