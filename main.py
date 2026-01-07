import os, re, time
from typing import Optional
import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="video-scout-gpt", version="0.2.0")

# ========= ENV =========
API_KEY = (os.getenv("ACTION_API_KEY") or "").strip()
BILI_COOKIE = (os.getenv("BILI_COOKIE") or "").strip()

if not API_KEY:
    raise RuntimeError("Missing ACTION_API_KEY")

# ========= HTTP =========
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bilibili.com/"
})

# ========= Utils =========
def require_key(x_api_key: Optional[str]):
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

def strip_html(s: str) -> str:
    return re.sub("<.*?>", "", s or "")

def get_json(url: str, params=None, headers=None, timeout=15):
    r = SESSION.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()

# ========= Model =========
class ScoutRequest(BaseModel):
    keyword: str
    user_need: Optional[str] = None
    bili_top: int = 3
    yt_top: int = 3
    max_lines_per_sub: int = 200
    sleep_sec: float = 0.4

# ========= BILI =========
def fetch_bili_subtitle(bvid: str, max_lines: int, sleep_sec: float) -> str:
    try:
        view = get_json(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid}
        )
        cid = (view.get("data") or {}).get("cid")
        if not cid:
            return "（无法获取 CID）"

        headers = {"Cookie": BILI_COOKIE} if BILI_COOKIE else {}
        player = get_json(
            "https://api.bilibili.com/x/player/v2",
            params={"bvid": bvid, "cid": cid},
            headers=headers
        )

        subs = (((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
        if not subs:
            return "（无官方字幕，或需要登录 Cookie）"

        sub_url = subs[0].get("subtitle_url") or ""
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url

        js = get_json(sub_url, timeout=20)
        body = js.get("body") or []

        lines, prev = [], None
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
        return f"（字幕获取失败，已降级：{e}）"

# ========= API =========
@app.post("/scout")
def scout(req: ScoutRequest, x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    require_key(x_api_key)

    kw = req.keyword.strip()
    if not kw:
        raise HTTPException(400, "keyword required")

    search = get_json(
        "https://api.bilibili.com/x/web-interface/search/type",
        params={
            "search_type": "video",
            "keyword": kw,
            "page": 1,
            "page_size": max(req.bili_top, 5)
        }
    )

    items = (search.get("data") or {}).get("result") or []
    items = items[:req.bili_top]

    report = f"# 关于 {kw} 的全网测评包\n\n"
    if req.user_need:
        report += f"## 使用需求\n{req.user_need}\n\n"

    report += "## B站字幕摘要\n\n"
    for it in items:
        title = strip_html(it.get("title"))
        bvid = it.get("bvid") or ""
        url = it.get("arcurl") or f"https://www.bilibili.com/video/{bvid}"
        sub = fetch_bili_subtitle(bvid, req.max_lines_per_sub, req.sleep_sec)
        report += f"### {title}\n- 链接：{url}\n\n{sub}\n\n---\n\n"

    report += "## YouTube 候选视频\n\n"
    try:
        with YoutubeDL({
            "quiet": True,
            "extract_flat": True,
            "skip_download": True
        }) as ydl:
            info = ydl.extract_info(f"ytsearch{req.yt_top}:{kw}", download=False)

        for e in (info.get("entries") or []):
            title = e.get("title")
            vid = e.get("id")
            if title and vid:
                report += f"- {title}: https://www.youtube.com/watch?v={vid}\n"
    except Exception as e:
        report += f"（YouTube 搜索失败：{e}）\n"

    return {"bundle": report}

@app.get("/health")
def health():
    return {"status": "alive"}
