import requests
import hashlib
import os
import subprocess
import json
import random  # 新增：用于处理随机逻辑

# BASE_DIR 指向 mcp/ 目录（browser/ 就在 mcp/ 下面）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 1. 内核路径：指向项目下的 browser/Application 目录
CHROME_PATH = os.path.join(BASE_DIR, "browser", "Application", "chrome.exe")

def get_deterministic_value(window_id, options):
    seed = int(hashlib.md5(str(window_id).encode()).hexdigest(), 16)
    random.seed(seed)
    return random.choice(options)

def generate_static_fingerprint(window_id):
    """根据窗口 ID 生成唯一的、固定的指纹种子"""
    return hashlib.md5(str(window_id).encode()).hexdigest()[:10]

def get_geo_info(proxy_str):
    """基于代理 IP 自动获取时区和国家代码"""
    if not proxy_str or ":" not in proxy_str:
        return {"timezone": "America/New_York", "lang": "en-US", "accept": "en-US,en"}
    try:
        parts = proxy_str.split(':')
        if len(parts) != 4: return {"timezone": "America/New_York", "lang": "en-US", "accept": "en-US,en"}
        p_ip, p_port, p_user, p_pass = parts
        proxies = {"http": f"http://{p_user}:{p_pass}@{p_ip}:{p_port}",
                   "https": f"http://{p_user}:{p_pass}@{p_ip}:{p_port}"}
        geo_data = {"timezone": "America/New_York", "lang": "en-US", "accept": "en-US,en"}
        lang_map = {
            "US": {"lang": "en-US", "accept": "en-US,en"},
            "CN": {"lang": "zh-CN", "accept": "zh-CN,zh;q=0.9"},
            "HK": {"lang": "zh-HK", "accept": "zh-HK,zh;q=0.9,en;q=0.8"},
            "JP": {"lang": "ja-JP", "accept": "ja-JP,ja;q=0.9,en;q=0.8"},
        }
        response = requests.get("http://ip-api.com/json/", proxies=proxies, timeout=8)
        data = response.json()
        if data.get('status') == 'success':
            country_code = data['countryCode']
            geo_data["timezone"] = data['timezone']
            if country_code in lang_map:
                geo_data["lang"] = lang_map[country_code]["lang"]
                geo_data["accept"] = lang_map[country_code]["accept"]
        return geo_data
    except:
        return {"timezone": "America/New_York", "lang": "en-US", "accept": "en-US,en"}

def create_proxy_auth_extension(proxy_host, proxy_port, proxy_user, proxy_pass, plugin_path):
    """生成带账密验证的代理插件"""
    if not os.path.exists(plugin_path): os.makedirs(plugin_path, exist_ok=True)
    manifest = '{"version":"1.0.0","manifest_version":2,"name":"Auth","permissions":["proxy","tabs","webRequest","webRequestBlocking","<all_urls>"],"background":{"scripts":["background.js"]}}'
    background = """
    var config = { mode: "fixed_servers", rules: { singleProxy: { scheme: "http", host: "%s", port: parseInt(%s) } } };
    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
    chrome.webRequest.onAuthRequired.addListener(function(details) {
        return { authCredentials: { username: "%s", password: "%s" } };
    }, {urls: ["<all_urls>"]}, ['blocking']);
    """ % (proxy_host, proxy_port, proxy_user, proxy_pass)
    with open(os.path.join(plugin_path, "manifest.json"), 'w') as f: f.write(manifest)
    with open(os.path.join(plugin_path, "background.js"), 'w') as f: f.write(background)
    return os.path.abspath(plugin_path)

def create_unbreakable_ui_extension(plugin_path):
    """生成强制导航栏 UI 插件 - 优化版：支持失败自动跳转 Google"""
    if not os.path.exists(plugin_path):
        os.makedirs(plugin_path, exist_ok=True)

    manifest = {
        "manifest_version": 2,
        "name": "NavUI",
        "version": "9.0",
        "permissions": ["tabs", "<all_urls>", "webNavigation", "webRequest"],
        "background": {"scripts": ["background.js"]},
        "content_scripts": [
            {
                "matches": ["<all_urls>"],
                "js": ["content.js"],
                "run_at": "document_start",
                "all_frames": False
            }
        ]
    }

    # 背景脚本逻辑：监听错误并处理 2 秒重定向
    background_logic = """
    let failTimers = {};

    chrome.webNavigation.onErrorOccurred.addListener((details) => {
        // 仅处理主框架错误
        if (details.frameId === 0) {
            console.log('Detected load error, redirecting in 2s...');
            failTimers[details.tabId] = setTimeout(() => {
                chrome.tabs.update(details.tabId, { url: "https://www.google.com" });
            }, 2000);
        }
    });

    chrome.webNavigation.onCompleted.addListener((details) => {
        if (details.frameId === 0 && failTimers[details.tabId]) {
            clearTimeout(failTimers[details.tabId]);
            delete failTimers[details.tabId];
        }
    });
    """
    ui_logic = """
    function forceDrawUI() {
        let shell = document.getElementById('matrix-nav-shell');
        if (shell) {
            // 同步当前的 URL 到输入框 (非输入状态下)
            const input = document.getElementById('m-url');
            if (input && document.activeElement !== input) {
                input.value = window.location.href;
            }
            return;
        }

        shell = document.createElement('div');
        shell.id = 'matrix-nav-shell';
        // 使用 !important 防止被网页样式覆盖
        shell.style = `
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            width: 100% !important;
            height: 44px !important;
            background: #FFFFFF !important;
            z-index: 2147483647 !important;
            display: flex !important;
            align-items: center !important;
            padding: 0 15px !important;
            box-sizing: border-box !important;
            border-bottom: 1px solid #E5E5E5 !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05) !important;
        `;

        shell.innerHTML = `
            <span id="m-back" style="cursor:pointer;margin-right:15px;color:#007AFF;font-family:sans-serif;font-weight:bold;">◁ Back</span>
            <input id="m-url" type="text" style="flex:1;height:28px;background:#F2F2F7;border:none;border-radius:8px;padding:0 10px;outline:none;font-size:12px;" value="${window.location.href}">
            <button id="m-go" style="margin-left:10px;background:#007AFF;color:white;border:none;border-radius:5px;padding:4px 12px;cursor:pointer;font-size:12px;">Go</button>
        `;

        // 兼容 document_start 阶段 body 尚未生成的情况
        const target = document.body || document.documentElement;
        if (target) {
            target.appendChild(shell);
            document.documentElement.style.setProperty('margin-top', '44px', 'important');
        }

        shell.querySelector('#m-go').onclick = () => {
            let url = shell.querySelector('#m-url').value;
            window.location.href = url.startsWith('http') ? url : 'https://' + url;
        };
        shell.querySelector('#m-back').onclick = () => window.history.back();
    }
    """
    with open(os.path.join(plugin_path, "manifest.json"), 'w', encoding='utf-8') as f:
        json.dump(manifest, f)

    with open(os.path.join(plugin_path, "content.js"), 'w', encoding='utf-8') as f:
        f.write(ui_logic + "\nsetInterval(forceDrawUI, 1000); forceDrawUI();")

    with open(os.path.join(plugin_path, "background.js"), 'w', encoding='utf-8') as f:
        f.write(background_logic)
    return os.path.abspath(plugin_path)



def launch_perfect_matrix(window_id, proxy_str, port=8666):
    start_x, start_y = 20, 20  # 起始位置
    offset_x = 25  # 每个窗口向右偏移量
    offset_y = 35  # 每个窗口向下偏移量

    # 循环周期：每 15 个窗口一轮，防止跑出屏幕
    cycle = (int(window_id) - 1) % 15

    pos_x = start_x + (cycle * offset_x)
    pos_y = start_y + (cycle * offset_y)

    win_width = 1000
    win_height = 700

    # 1. 路径自动对齐
    base_path = os.path.join(BASE_DIR, "browser", "userbrowser", str(window_id))
    profile = os.path.join(base_path, "Profiles")
    os.makedirs(profile, exist_ok=True)
    # 2. 随机指纹注入逻辑 (保持与 ID 绑定，确保唯一性)
    seed = generate_static_fingerprint(window_id)
    # CPU 核心数随机池
    cpu_options = [4, 8, 12, 16, 24, 32]
    cpu_cores = get_deterministic_value(window_id, cpu_options)

    # UA 随机池
    ua_options = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ]
    user_agent = get_deterministic_value(window_id, ua_options)

    # WebGL 渲染随机化
    vendor_options = ["Google Inc. (NVIDIA)", "Google Inc. (Intel)", "Google Inc. (AMD)"]
    renderer_options = [
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, )",
        "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, )",
        "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, )"
    ]
    webgl_vendor = get_deterministic_value(window_id, vendor_options)
    webgl_renderer = get_deterministic_value(window_id, renderer_options)

    # 3. 代理与地理位置处理
    geo = get_geo_info(proxy_str)

    # 4. 构建启动命令
    cmd = [
        CHROME_PATH,
        f"--window-position={pos_x},{pos_y}",
        f"--window-size={win_width},{win_height}",
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        f"--user-agent={user_agent}",
        f"--fingerprint={seed}",
        f"--fingerprint-hardware-concurrency={cpu_cores}",
        f"--fingerprint-webgl-vendor={webgl_vendor}",
        f"--fingerprint-webgl-renderer={webgl_renderer}",
        f"--timezone={geo['timezone']}",
        f"--lang={geo['lang']}",
        f"--accept-lang={geo['accept']}",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    # 6. 执行启动，返回 pid 供调用方写配置
    print(f"🚀 环境启动中 | ID:{window_id} | 端口:{port}")

    try:
        proc = subprocess.Popen(cmd)
        print(f"✅ 窗口_{window_id} 已启动 (PID:{proc.pid}，端口:{port})")
        # 尝试同步给主控后端（可选，不影响主流程）
        try:
            payload = {"window_id": str(window_id), "pid": proc.pid, "port": int(port), "status": "running"}
            requests.post("http://127.0.0.1:6888/register", json=payload, timeout=0.5, headers={"Connection": "close"})
        except Exception:
            pass
        return proc.pid
    except Exception as e:
        print(f"❌ 启动过程出错: {e}")
        return None