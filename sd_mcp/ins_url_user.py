# 职责：从数据库 ins_Comment 表读取帖子 ID，采集每个帖子下的评论用户并回写
import logging
from DrissionPage import ChromiumPage
import sqlite3

import os as _os
DB_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "runtime_store.db")



def user_js(page, media_id, user_max, user_time):
    target_count = user_max
    days_limit = user_time
    js_code = f"""
    // 改进点：去掉最外层的 return await，直接 return 异步自执行函数
    return (async function fetchInstagramComments(mediaId, targetCount, daysLimit) {{
        let allComments = [];
        let afterCursor = null;
        let hasMore = true;
        const now = Date.now() / 1000;
        const secondsLimit = daysLimit * 24 * 60 * 60;

        try {{
            while (hasMore && allComments.length < targetCount) {{
                // 1. 动态提取安全参数 (DTSG, LSD 等)
                let dtsg = "", lsd = "", rev = "", hsi = "", hs = "", spin_r = "", spin_b = "", spin_t = "", dyn = "";
                try {{
                    dtsg = require("DTSGInitialData").token;
                    lsd = require("LSD").token;
                    const sd = require("SiteData");
                    rev = sd.client_revision;
                    hsi = sd.hsi;
                    hs = sd.haste_session;
                    spin_r = sd.__spin_r;
                    spin_b = sd.__spin_b;
                    spin_t = sd.__spin_t || Date.now().toString().slice(0, 10);
                    dyn = sd.__dyn || "";
                }} catch (e) {{
                    console.warn("部分安全参数通过 require 提取失败，尝试使用默认值");
                }}

                const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || "";
                const jazoest = document.querySelector('input[name="jazoest"]')?.value || "26274";

                const variables = {{
                    after: afterCursor,
                    before: null,
                    first: 20,
                    last: null,
                    media_id: mediaId,
                    sort_order: "popular",
                    __relay_internal__pv__PolarisIsLoggedInrelayprovider: true
                }};

                // 构建包含全量指纹参数的 body
                const bodyParams = {{
                    av: "17841466433134302",
                    __d: "www",
                    __user: "0",
                    __a: "1",
                    __req: (Math.floor(Math.random() * 10) + 10).toString(),
                    __hs: hs,
                    dpr: "1",
                    __ccg: "EXCELLENT",
                    __rev: rev,
                    __s: "000000:000000:000000",
                    __hsi: hsi,
                    __dyn: dyn,
                    __csr: "",
                    __hsdp: "",
                    __hblp: "",
                    __sjsp: "",
                    __comet_req: "7",
                    fb_dtsg: dtsg,
                    jazoest: jazoest,
                    lsd: lsd,
                    __spin_r: spin_r,
                    __spin_b: spin_b,
                    __spin_t: spin_t,
                    fb_api_caller_class: "RelayModern",
                    fb_api_req_friendly_name: "PolarisPostCommentsPaginationQuery",
                    variables: JSON.stringify(variables),
                    doc_id: "26864966453197043"
                }};

                const body = new URLSearchParams(bodyParams);

                const response = await fetch("https://www.instagram.com/api/graphql", {{
                    method: "POST",
                    headers: {{
                        "content-type": "application/x-www-form-urlencoded",
                        "x-csrftoken": csrf,
                        "x-fb-lsd": lsd,
                        "x-ig-app-id": "936619743392459",
                        "x-fb-friendly-name": "PolarisPostCommentsPaginationQuery"
                    }},
                    body: body,
                    credentials: "include"
                }});

                const data = await response.json();
                const commentConnection = data?.data?.xdt_api__v1__media__media_id__comments__connection;

                if (!commentConnection || !commentConnection.edges || commentConnection.edges.length === 0) break;

                const pageComments = [];
                for (const edge of commentConnection.edges) {{
                    const node = edge.node;
                    if (now - node.created_at > secondsLimit) {{
                        hasMore = false;
                        break;
                    }}
                    pageComments.push({{
                        "username": node.user?.username || "未知用户",
                        "usertime": node.created_at ? new Date(node.created_at * 1000).toISOString().slice(0, 19).replace('T', ' ') : null,
                        "text": node.text || "",
                        "user_taken": node.created_at
                    }});
                }}

                allComments = allComments.concat(pageComments);
                afterCursor = commentConnection.page_info?.end_cursor;
                hasMore = hasMore && commentConnection.page_info?.has_next_page === true;

                console.log(`已采集 ${{allComments.length}} 条评论...`);

                if (hasMore && allComments.length < targetCount) {{
                    await new Promise(r => setTimeout(r, 2000 + Math.random() * 1000));
                }}
            }}
            console.log("%c=== 采集完成！数据如下 ===", "color: #00ff00; font-size: 16px; font-weight: bold;");
            console.table(allComments.slice(0, targetCount));
            return {{ success: true, comments: allComments.slice(0, targetCount) }};
        }} catch (e) {{
            console.error("采集出错:", e);
            return {{ success: false, error: e.message }};
        }}
    }})("{media_id}", {target_count}, {days_limit});
    """
    return page.run_js(js_code)


def get_url_id_list():
    """只返回还没有采集过评论的帖子 ID（username 为空的母记录）"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # 只取尚未采集评论的帖子（username 为空 = 只有母记录，没有评论行）
        cursor.execute(
            "SELECT DISTINCT id FROM ins_Comment WHERE (username IS NULL OR username = '') AND url IS NOT NULL"
        )
        rows = cursor.fetchall()
        url_id = list(dict.fromkeys(  # 保序去重
            str(row[0]).split('_')[0] for row in rows if row[0]
        ))
        conn.close()
        return url_id
    except Exception as e:
        print(f"❌ 读取数据库失败: {e}")
        return []


def update_post_with_comments(post_meta, new_comments):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        with conn:
            # 删除该帖子的所有旧记录（含已有评论），防止重复写入
            cursor.execute("DELETE FROM ins_Comment WHERE id LIKE ?",
                           (f"{str(post_meta.get('id')).split('_')[0]}%",))
            for comment in new_comments:
                cursor.execute('''
                    INSERT INTO ins_Comment (
                        id, url, like_count, comment_count, taken_at, time,
                        caption, name, username, usertime, text, user_taken
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    post_meta.get('id'),
                    post_meta.get('url'),
                    int(post_meta.get('like_count') or 0),
                    int(post_meta.get('comment_count') or 0),
                    int(post_meta.get('taken_at') or 0),
                    post_meta.get('time'),
                    post_meta.get('caption'),
                    post_meta.get('name'),
                    comment.get('username'),
                    comment.get('usertime'),
                    comment.get('text'),
                    comment.get('user_taken')
                ))
        return True
    except Exception as e:
        print(f"⚠️ 数据库写入异常: {e}")
        return False
    finally:
        conn.close()


def user_worker(page, url_id_list, user_max, user_time):
    unique_url_ids = list(set(url_id_list))
    while unique_url_ids:
        media_id = str(unique_url_ids.pop(0)).split('_')[0]
        print(f"\n🚀 正在处理 MediaID (基础ID): {media_id}")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, url, like_count, comment_count, taken_at, time, caption, name FROM ins_Comment WHERE id LIKE ? AND url IS NOT NULL LIMIT 1",
            (f"{media_id}%",)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            print(f"⚠️ {media_id} 在数据库中未找到匹配的母数据，跳过...")
            continue
        full_id = row[0]
        post_meta = {
            "id": full_id,
            "url": row[1],
            "like_count": row[2],
            "comment_count": row[3],
            "taken_at": row[4],
            "time": row[5],
            "caption": row[6],
            "name": row[7]
        }
        result = user_js(page, media_id, user_max, user_time)
        if result and result.get('success'):
            comments = result.get('comments', [])
            if comments:
                success = update_post_with_comments(post_meta, comments)
                if success:
                    print(f"✅ {full_id} 评论已成功合并，共 {len(comments)} 条")
                else:
                    print(f"❌ {full_id} 数据库写入失败")
            else:
                print(f"⚠️ {media_id} 无新评论")
        else:
            err_msg = result.get('error', '未知原因')
            print(f"❌ {media_id} 采集失败: {err_msg}")


def port_page(port):
    try:
        page = ChromiumPage(port)
        logging.info(f"成功连接端口 {port} 的浏览器窗口")
        return page
    except Exception as e:
        logging.info(f" 连接端口 {port} 失败: {e}")
        return None


def run(port, user_max, user_time):
    import threading, queue as _queue
    media_ids = get_url_id_list()
    if not media_ids:
        print("⚠️ 数据库中未找到未采集评论的帖子 ID。")
        return

    print(f"📋 共读取到 {len(media_ids)} 个帖子待处理，使用 {len(port)} 个窗口")

    # 多窗口并行：用任务队列分发
    task_q = _queue.Queue()
    for mid in media_ids:
        task_q.put(mid)

    def _worker(p):
        page = port_page(p)
        if not page:
            print(f"❌ 无法连接端口 {p}")
            return
        while True:
            try:
                media_id = task_q.get_nowait()
            except _queue.Empty:
                break
            # 复用 user_worker 单条处理逻辑
            user_worker(page, [media_id], user_max, user_time)
            task_q.task_done()

    threads = [threading.Thread(target=_worker, args=(p,), daemon=True) for p in port]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print("\n✅ 所有评论采集任务已处理完毕。")

    print("\n✅ 所有评论采集任务已处理完毕。")
