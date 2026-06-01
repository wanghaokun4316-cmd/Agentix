"""
多窗口 URL 导航模块
从 windows_config.json / running_states.json 读取在线账号端口，
通过 DrissionPage 并发导航到目标网址。
所有自然语言解析（URL、目标账号范围）均由调用方（大模型）完成后以结构化参数传入。
"""
import os, json, threading
from DrissionPage import ChromiumPage

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_WINDOWS_CONFIG_PATH = os.path.join(_THIS_DIR, "browser", "config", "windows_config.json")
_RUNNING_STATES_PATH = os.path.join(os.path.dirname(_THIS_DIR), "config", "running_states.json")


def _load_online_accounts() -> dict:
    """返回 {account_name: port} — 合并 windows_config.json 和 running_states.json"""
    result: dict[str, int] = {}
    if os.path.exists(_WINDOWS_CONFIG_PATH):
        try:
            with open(_WINDOWS_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for entry in cfg.get("data", []):
                if entry.get("status") == "running":
                    result[str(entry["id"])] = int(entry["port"])
        except Exception:
            pass
    if os.path.exists(_RUNNING_STATES_PATH):
        try:
            with open(_RUNNING_STATES_PATH, "r", encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    if k not in result:
                        port = v.get("port") if isinstance(v, dict) else v
                        if port:
                            result[str(k)] = int(port)
        except Exception:
            pass
    return result


def _ensure_scheme(url: str) -> str:
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return "https://" + url


def _navigate_one(port: int, url: str) -> bool:
    try:
        page = ChromiumPage(port)
        page.get(url)
        return True
    except Exception as e:
        print(f"❌ 端口 {port} 导航失败: {e}")
        return False


def open_url_in_window(account_name: str, url: str) -> dict:
    """指定某个账号的窗口打开某个网址"""
    accounts = _load_online_accounts()
    key = str(account_name)
    if key not in accounts:
        return {"success": False, "error": f"账号 {account_name} 不在线或不存在", "online": list(accounts.keys())}
    url = _ensure_scheme(url)
    ok = _navigate_one(accounts[key], url)
    return {"success": ok, "account": account_name, "port": accounts[key], "url": url}


def open_url_in_all_windows(url: str) -> dict:
    """所有在线窗口同时打开某个网址（多线程并发）"""
    accounts = _load_online_accounts()
    if not accounts:
        return {"success": False, "error": "没有在线账号"}
    url = _ensure_scheme(url)
    results: dict = {}
    lock = threading.Lock()

    def _worker(acc: str, port: int):
        ok = _navigate_one(port, url)
        with lock:
            results[acc] = {"port": port, "success": ok}

    threads = [threading.Thread(target=_worker, args=(a, p)) for a, p in accounts.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = sum(1 for v in results.values() if v["success"])
    return {"success": ok_count > 0, "url": url, "total": len(accounts), "ok": ok_count, "detail": results}


def open_url_in_multiple_windows(account_names: list, url: str) -> dict:
    """指定多个账号的窗口打开某个网址（多线程并发）"""
    accounts = _load_online_accounts()
    url = _ensure_scheme(url)
    results: dict = {}
    lock = threading.Lock()

    def _worker(acc: str, port: int):
        ok = _navigate_one(port, url)
        with lock:
            results[acc] = {"port": port, "success": ok}

    threads = []
    for raw_acc in account_names:
        acc = str(raw_acc)
        if acc not in accounts:
            results[acc] = {"port": None, "success": False, "error": "不在线"}
            continue
        t = threading.Thread(target=_worker, args=(acc, accounts[acc]))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    ok_count = sum(1 for v in results.values() if v["success"])
    return {"success": ok_count > 0, "url": url, "ok": ok_count, "total": len(account_names), "detail": results}


def _print_result(result: dict):
    url = result.get("url", "")
    detail = result.get("detail", {})
    if detail:
        ok = result.get("ok", 0)
        total = result.get("total", 0)
        print(f"\n{'✅' if result.get('success') else '❌'} 共 {total} 个窗口，成功 {ok} 个 → {url}")
        for acc, info in detail.items():
            mark = "✅" if info.get("success") else "❌"
            err = f"  ({info.get('error', '')})" if not info.get("success") else ""
            print(f"  {mark} 账号 {acc}  端口 {info.get('port')}{err}")
    else:
        if result.get("success"):
            print(f"✅ 账号 {result.get('account')} (端口 {result.get('port')}) → {url}")
        else:
            print(f"❌ 失败: {result.get('error', '未知错误')}")
            if result.get("online"):
                print(f"   当前在线账号: {result['online']}")


def main_entry(params: dict):
    """
    MCP 工具入口。所有自然语言推理由大模型完成，此处只做路由：
      account_name  → open_url_in_window
      account_names → open_url_in_multiple_windows
      （两者均缺） → open_url_in_all_windows
    """
    url           = _ensure_scheme(params.get("url", "").strip())
    account_name  = params.get("account_name")
    account_names = params.get("account_names")

    if account_name is not None:
        result = open_url_in_window(str(account_name), url)
    elif account_names:
        result = open_url_in_multiple_windows(account_names, url)
    else:
        result = open_url_in_all_windows(url)

    _print_result(result)
    return result
