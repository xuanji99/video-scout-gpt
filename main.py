import os, re, time
from typing import Optional, List, Dict, Any
import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="video-pro-gpt", version="0.1.0")

API_KEY = (os.getenv("ACTION_API_KEY") or "").strip()
BILI_COOKIE = (os.getenv("BILI_COOKIE") or "").strip()

if not API_KEY:
    raise RuntimeError("Missing ACTION_API_KEY")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bilibili.com/"
})

def require_key(x_api_key: Optional[str]):
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

def strip_html(s: str) -> str:
    return re.sub("<.*?>", "", s or "")

def get_json(url: str, params: dict = None, headers: dict = None, timeout: int = 15) -> dict:
    r = SESSION.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()

class ScoutRequest(BaseModel):
    keyword: str
    user_need: Optional[str] = None
    bili_top: int = 3          # 只抓前3个字幕，稳 + 不超时
    yt_top: int = 3
    max_lines_per_sub: int = 220  # 每个字幕最多行数（控制体积）
    sleep_sec: float = 0.4        # 轻微节流，减少风控

def smart_fetch_bili_subtitles(bvid: str, max_lines: int, sleep_sec: float) -> str:
    try:
        # 1) cid
        view = get_json("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid})
        cid = (view.get("data") or {}).get("cid")
        if not cid:
            return "（CID获取失败）"

        # 2) subtitle url
        headers = {"Cookie": BILI_COOKIE} if BILI_COOKIE else {}
        player = get_json("https://api.bilibili.com/x/player/v2", params={"bvid": bvid, "cid": cid}, headers=headers)
        subs = (((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
        if not subs:
            return "（无外挂字幕/或需Cookie）"

        sub_url = subs[0].get("subtitle_url") or ""
        if not sub_url:
            return "（字幕URL为空）"
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        elif sub_url.startswith("http"):
            pass
        else:
            # 极少数情况：相对路径
            sub_url = "https://" + sub_url.lstrip("/")

        # 3) download + clean
        js = get_json(sub_url, timeout=20)
        body = js.get("body") or []
        lines = []
        prev = None
        for seg in body:
            c = (seg.get("content") or "").strip()
            if c and c != prev:
                lines.append(c)
                prev = c
            if len(lines) >= max_lines:
                break

        time.sleep(sleep_sec)
        return "\n".join(lines) if lines else "（字幕为空）"
    except Exception as e:
        return f"（字幕提取失败：{e}）"

@app.post("/scout")
def scout(req: ScoutRequest, x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    require_key(x_api_key)
    kw = (req.keyword or "").strip()
    if not kw:
        raise HTTPException(400, "keyword required")

    # 1) B站搜索
    search = get_json(
        "https://api.bilibili.com/x/web-interface/search/type",
        params={"search_type": "video", "keyword": kw, "page": 1, "page_size": max(req.bili_top, 5)}
    )
    items = (search.get("data") or {}).get("result") or []
    items = items[:req.bili_top]

    report = f"# 关于 {kw} 的全网测评包\n\n"
    if req.user_need:
        report += f"## 需求补充\n{req.user_need}\n\n"

    report += "## B站字幕包\n\n"
    for it in items:
        title = strip_html(it.get("title"))
        bvid = it.get("bvid") or ""
        url = it.get("arcurl") or (f"https://www.bilibili.com/video/{bvid}" if bvid else "")
        sub_text = smart_fetch_bili_subtitles(bvid, req.max_lines_per_sub, req.sleep_sec) if bvid else "（缺少bvid）"
        report += f"### {title}\n- 链接：{url}\n\n{sub_text}\n\n---\n\n"

    # 2) YouTube 搜索（最省事：yt-dlp）
    report += "## YouTube 候选清单\n\n"
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(f"ytsearch{req.yt_top}:{kw}", download=False)
        for e in (info.get("entries") or []):
            title = e.get("title") or ""
            vid = e.get("id") or e.get("url") or ""
            link = vid if str(vid).startswith("http") else (f"https://www.youtube.com/watch?v={vid}" if vid else "")
            if title and link:
                report += f"- {title}: {link}\n"
    except Exception as e:
        report += f"（YouTube 搜索失败：{e}）\n"

    return {"bundle": report}

@app.get("/health")
def health():
    return {"status": "alive"}
