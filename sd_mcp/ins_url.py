# 职责：采集 Instagram 博主的帖子列表并写入数据库 (ins_Comment 表)
import threading, logging
from DrissionPage import ChromiumPage
import queue
import sqlite3

import os as _os
DB_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "runtime_store.db")
file_lock = threading.Lock()



def url_js(page, target_username, url_max, c_days, min_l, min_c):
    js_code = f"""
    return (async () => {{
        // 使用你提供的 JS 核心逻辑，仅对包裹方式做了异步兼容处理
        const targetUsername = "{target_username}";
        const targetCount = {url_max};
        const days = {c_days};
        const minLike = {min_l};
        const minComment = {min_c};
        console.log(`%c博主帖子抓取 v5 | 目标: "${{targetUsername}}" | 最近 ${{days}} 天 | 最多 ${{targetCount}} 条`, "color: #0095f6; font-weight: bold;");
        let allPosts = [];
        let afterCursor = null;
        let page = 1;
        let hasMore = true;
        const cutoffTimestamp = Math.floor(Date.now() / 1000) - (days * 24 * 60 * 60);

        try {{
            while (hasMore && allPosts.length < targetCount) {{
                let dtsg, lsd, rev, hsi, hs, spin_r, spin_b, spin_t;
                try {{
                    dtsg = require("DTSGInitialData").token;
                    lsd = require("LSD").token;
                    const sd = require("SiteData");
                    rev = sd.client_revision;
                    hsi = sd.hsi;
                    hs = sd.haste_session;
                    spin_r = sd.__spin_r;
                    spin_b = sd.__spin_b;
                    spin_t = sd.__spin_t || Date.now().toString().slice(0,10);
                }} catch (e) {{
                    console.warn(" require 提取失败");
                }}

                const csrfMatch = document.cookie.match(/csrftoken=([^;]+)/);
                const csrf = csrfMatch ? csrfMatch[1] : "";
                const jazoest = document.querySelector('input[name="jazoest"]')?.value || "26303";

                const variables = {{
                    after: afterCursor,
                    before: null,
                    data: {{
                        count: 12,
                        include_reel_media_seen_timestamp: true,
                        include_relationship_info: true,
                        latest_besties_reel_media: true,
                        latest_reel_media: true
                    }},
                    first: 12,
                    last: null,
                    username: targetUsername,
                    __relay_internal__pv__PolarisImmersiveFeedChainingEnabledrelayprovider: true
                }};

                const body = new URLSearchParams({{
                    av: "17841466433134302",
                    __d: "www",
                    __user: "0",
                    __a: "1",
                    dpr: "1",
                    __ccg: "UNKNOWN",
                    __rev: rev || "1037607845",
                    __hsi: hsi || "",
                    __hs: hs || "",
                    __dyn: "7xeUjG1mxu1syUbFp41twpUnwgU7SbzEdF8aUco2qwJxS0k24o0B-q1ew6ywaq0yE462mcw5Mx62G5UswoEcE7O2l0Fwqo31w9a9wlo5qfK0EUjwGzEaE2iwNwmE2eUlwhEe87q0oa2-azo7u3vwDwHg2ZwrUdUbGwmk0zU8oC1Iwqo5p0OwUQp1yU426V89F8uwm8jxK1mwa6bBK4o16UsxWawOwi84q2i1cweW",
                    fb_dtsg: dtsg,
                    jazoest: jazoest,
                    lsd: lsd,
                    __spin_r: spin_r || "1037607845",
                    __spin_b: spin_b || "trunk",
                    __spin_t: spin_t,
                    __crn: "comet.igweb.PolarisProfilePostsTabRoute",
                    fb_api_caller_class: "RelayModern",
                    fb_api_req_friendly_name: "PolarisProfilePostsTabContentQuery_connection",
                    server_timestamps: "true",
                    variables: JSON.stringify(variables),
                    doc_id: "25456462984030189"
                }});

                const response = await fetch("https://www.instagram.com/graphql/query", {{
                    method: "POST",
                    headers: {{
                        "accept": "*/*",
                        "content-type": "application/x-www-form-urlencoded",
                        "x-csrftoken": csrf,
                        "x-ig-app-id": "936619743392459"
                    }},
                    referrer: `https://www.instagram.com/${{targetUsername}}/`,
                    body: body,
                    credentials: "include"
                }});

                if (!response.ok) throw new Error(`HTTP ${{response.status}}`);

                const data = await response.json();
                const timeline = data?.data?.xdt_api__v1__feed__user_timeline_graphql_connection;

                if (!timeline || !timeline.edges || timeline.edges.length === 0) {{
                    hasMore = false;
                    break;
                }}

                const pagePosts = timeline.edges.map(edge => {{
                    const node = edge.node;
                    const shortcode = node.code || node.shortcode || null;
                    return {{
                        id: node.id,
                        url: shortcode ? `https://www.instagram.com/p/${{shortcode}}/` : null,
                        like_count: node.like_count || 0,
                        comment_count: node.comment_count || 0,
                        taken_at: node.taken_at,
                        time: node.taken_at ? new Date(node.taken_at * 1000).toISOString().slice(0, 19).replace('T', ' ') : null,
                        caption: node.caption?.text || ""
                    }};
                }}).filter(post => {{
                    if (post.taken_at && post.taken_at < cutoffTimestamp) return false;
                    return post.like_count >= minLike || post.comment_count >= minComment;
                }});

                allPosts = allPosts.concat(pagePosts);
                afterCursor = timeline.page_info?.end_cursor;
                hasMore = timeline.page_info?.has_next_page === true;

                if (timeline.edges.length > 0) {{
                    const lastTakenAt = timeline.edges[timeline.edges.length - 1].node.taken_at;
                    if (lastTakenAt && lastTakenAt < cutoffTimestamp) hasMore = false;
                }}

                page++;
                if (hasMore && allPosts.length < targetCount) {{
                    await new Promise(r => setTimeout(r, 1500 + Math.random() * 1500));
                }}
            }}
            return {{ success: true, posts: allPosts.slice(0, targetCount) }};
        }} catch (e) {{
            return {{ success: false, error: e.message }};
        }}
    }})();
    """
    return page.run_js(js_code)


def url_worker(page, blogger, url_max, c_days, min_l, min_c):
    result = url_js(page, blogger, url_max, c_days, min_l, min_c)
    if result and result.get('success'):
        posts = result.get('posts', [])
        for post in posts:
            post['name'] = blogger
        return result
    return {"success": False, "posts": []}


def port_page(port):
    try:
        page = ChromiumPage(port)
        logging.info(f"成功连接端口 {port} 的浏览器窗口")
        return page
    except Exception as e:
        logging.info(f" 连接端口 {port} 失败: {e}")
        return None


def save_to_db(post_list):
    with file_lock:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        for post in post_list:
            params = (
                str(post.get('id', '')),
                str(post.get('url')) if post.get('url') else None,
                int(post.get('like_count', 0)),
                int(post.get('comment_count', 0)),
                int(post.get('taken_at', 0)),
                str(post.get('time')) if post.get('time') else None,
                str(post.get('caption')) if post.get('caption') else None,
                str(post.get('name')) if post.get('name') else None,
                None, None, None, None
            )
            cursor.execute('''
                INSERT INTO ins_Comment
                (id, url, like_count, comment_count, taken_at, time, caption, name,
                 username, usertime, text, user_taken)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', params)
        conn.commit()
        conn.close()


def run(port, bloggers, url_max, c_days, min_l, min_c):
    task_queue = queue.Queue()
    for b in bloggers:
        task_queue.put(b)

    def worker(p):
        page = port_page(p)
        if not page:
            return

        while not task_queue.empty():
            try:
                blogger = task_queue.get_nowait()
            except queue.Empty:
                break

            print(f"🚀 [线程-{p}] 开始采集帖子 -> {blogger}")
            result = url_worker(page, blogger, url_max, c_days, min_l, min_c)

            if result and result.get('success'):
                posts = result.get('posts', [])
                print(f"✅ [采集结果]: 博主 {blogger} 成功抓取 {len(posts)} 条数据")
                save_to_db(posts)
            else:
                err_msg = result.get('error', '未知原因')
                print(f"❌ [采集失败]: {blogger} 采集未成功，原因: {err_msg}")

            task_queue.task_done()

    threads = []
    for p in port:
        t = threading.Thread(target=worker, args=(p,), name=f"Thread-{p}")
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    print("\n✅ 所有帖子采集结束。")
