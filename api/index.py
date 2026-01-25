from flask import Flask, request, jsonify, make_response
import requests
from bs4 import BeautifulSoup
import re
import json
import time
from urllib.parse import unquote

app = Flask(__name__)

# --- CONFIG ---
ALLOWED_ORIGINS = [
    "https://frontend-kamu.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:5500"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.dramabox.com/",
    "Connection": "keep-alive",
}

BASE_URL = "https://www.dramabox.com"

# --- SESSION GLOBAL (cookies kebawa) ---
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# --- SECURITY (CORS allowlist) ---
@app.before_request
def check_origin():
    if request.method == "OPTIONS":
        return _build_cors_preflight_response()

    origin = request.headers.get("Origin")
    if origin and origin not in ALLOWED_ORIGINS:
        return jsonify({"error": "Forbidden"}), 403

def _build_cors_preflight_response():
    resp = make_response()
    resp.headers.add("Access-Control-Allow-Origin", request.headers.get("Origin", "*"))
    resp.headers.add("Access-Control-Allow-Headers", "*")
    resp.headers.add("Access-Control-Allow-Methods", "*")
    return resp

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers.add("Access-Control-Allow-Origin", origin)
        response.headers.add("Vary", "Origin")
    return response


# =========================
# HELPERS
# =========================

_last_warm = 0

def warmup():
    """Hit homepage dulu supaya cookie kebentuk."""
    global _last_warm
    now = time.time()
    if now - _last_warm < 60:
        return
    try:
        SESSION.get(f"{BASE_URL}/in/", timeout=15, allow_redirects=True)
        _last_warm = now
    except Exception:
        pass

def get_soup(url, params=None):
    try:
        warmup()

        headers = {
            **HEADERS,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }

        resp = SESSION.get(url, headers=headers, params=params, timeout=15, allow_redirects=True)

        if resp.status_code == 403:
            headers["Referer"] = f"{BASE_URL}/in/"
            resp = SESSION.get(url, headers=headers, params=params, timeout=15, allow_redirects=True)

        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[get_soup] Error fetching {url}: {e}")
        return None

def extract_dramas_html(soup):
    dramas = []
    links = soup.find_all("a", href=re.compile(r"/in/drama/"))
    seen = set()

    for link in links:
        href = link.get("href")
        if not href: continue

        full_url = BASE_URL + href if href.startswith("/") else href
        if full_url in seen: continue
        seen.add(full_url)

        title_tag = link.find(["h3", "p", "div", "span"], class_=re.compile(r"title|name|text|ell|item", re.I))
        title = title_tag.get_text(strip=True) if title_tag else "No Title"

        img_tag = link.find("img")
        thumbnail = img_tag.get("src") if img_tag else None

        if title == "No Title" and img_tag and img_tag.get("alt"):
            title = img_tag.get("alt")

        if thumbnail: thumbnail = normalize_img_url(thumbnail)

        if title != "No Title" or thumbnail:
            dramas.append({"title": title, "url": full_url, "thumbnail": thumbnail})

    return dramas

def extract_next_data(soup):
    script = soup.find("script", id="__NEXT_DATA__")
    if not script: return None
    try:
        raw = script.get_text(strip=True) or script.string
        if not raw: return None
        return json.loads(raw)
    except Exception as e:
        print(f"[extract_next_data] parse error: {e}")
        return None

def slugify(s):
    s = (s or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-zA-Z0-9\-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "drama"

IMG_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", re.I)

def normalize_img_url(u: str):
    if not u or not isinstance(u, str): return None
    u = u.strip()
    if "/_next/image" in u and "url=" in u:
        m = re.search(r"[?&]url=([^&]+)", u)
        if m: u = unquote(m.group(1))
    if u.startswith("//"): return "https:" + u
    if u.startswith("/"): return BASE_URL + u
    return u

def flatten_strings(obj, out):
    if isinstance(obj, dict):
        for v in obj.values(): flatten_strings(v, out)
    elif isinstance(obj, list):
        for i in obj: flatten_strings(i, out)
    elif isinstance(obj, str):
        out.append(obj)

def pick_thumbnail(item: dict):
    if not isinstance(item, dict): return None
    candidate_keys = ["cover", "coverUrl", "bookCover", "poster", "posterUrl", "image", "img", "thumbnail", "picUrl"]
    for k in candidate_keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip(): return normalize_img_url(v)
    
    strings = []
    flatten_strings(item, strings)
    for s in strings:
        ss = s.lower()
        if ("cover" in ss or "poster" in ss or "thumb" in ss) and (("http" in ss) or ss.startswith("/")):
            if IMG_EXT_RE.search(ss): return normalize_img_url(s)
    return None

def find_list_items_by_fields(obj):
    found_lists = []
    def walk(x):
        if isinstance(x, dict):
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            if x and all(isinstance(i, dict) for i in x):
                keys = set()
                for i in x[:10]: keys |= set(i.keys())
                if ("bookId" in keys or "id" in keys) and ("bookName" in keys or "title" in keys):
                    found_lists.append(x)
            for i in x: walk(i)
    walk(obj)
    return found_lists

def map_items_to_results(items):
    results = []
    for item in items:
        title = item.get("bookName") or item.get("title")
        bid = item.get("bookId") or item.get("id")
        if not title or not bid: continue
        cover = pick_thumbnail(item)
        results.append({
            "title": title,
            "thumbnail": cover,
            "url": f"{BASE_URL}/in/drama/{bid}/{slugify(title)}"
        })
    return results

# --- HELPER KHUSUS VIDEO STREAM ---
def find_video_stream(obj):
    """Cari URL video (m3u8/mp4) di dalam JSON episode"""
    candidates = []
    
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                # Cek key yang biasanya nyimpen link video
                if k in ["videoUrl", "url", "playUrl", "source", "src", "originalUrl", "m3u8", "m3u8Url"]:
                    if isinstance(v, str) and v.startswith("http"):
                        candidates.append(v)
                walk(v)
        elif isinstance(x, list):
            for i in x: walk(i)
    
    walk(obj)
    
    # Prioritas: m3u8 > mp4 > link lainnya
    for u in candidates:
        if ".m3u8" in u: return u
    for u in candidates:
        if ".mp4" in u: return u
    
    return candidates[0] if candidates else None

# =========================
# ROUTES
# =========================

@app.route("/")
def index():
    return jsonify({"status": "Active", "msg": "Dramabox API with Video Extraction"})

@app.route("/api/home")
def home():
    soup = get_soup(f"{BASE_URL}/in/browse/0/1")
    if not soup: return jsonify({"error": "Failed"}), 500
    dramas = extract_dramas_html(soup)
    return jsonify({"count": len(dramas), "data": dramas})

@app.route("/api/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query: return jsonify({"error": "No query"}), 400

    soup = get_soup(f"{BASE_URL}/in/search", params={"searchValue": query})
    if not soup: return jsonify({"error": "Failed"}), 500

    results = []
    data = extract_next_data(soup)
    if data:
        candidate_lists = find_list_items_by_fields(data)
        if candidate_lists:
            best = max(candidate_lists, key=len)
            results = map_items_to_results(best)

    if not results:
        results = extract_dramas_html(soup)

    return jsonify({"query": query, "count": len(results), "data": results})

@app.route("/api/browse")
def browse():
    genre = request.args.get("genre_id", "0")
    page = request.args.get("page", "1")
    soup = get_soup(f"{BASE_URL}/in/browse/{genre}/{page}")
    if not soup: return jsonify({"error": "Failed"}), 500
    dramas = extract_dramas_html(soup)
    
    genres = [{"id": "0", "name": "All"}]
    for link in soup.find_all("a", href=re.compile(r"/in/browse/\d+")):
        match = re.search(r"/browse/(\d+)", link.get("href", ""))
        if match:
            gname = link.get_text(strip=True)
            if gname: genres.append({"id": match.group(1), "name": gname})
    
    unique_genres = list({g["id"]: g for g in genres}.values())
    return jsonify({"page": int(page), "genres": unique_genres, "data": dramas})

@app.route("/api/drama")
def drama_detail():
    url = request.args.get("url")
    if not url: return jsonify({"error": "No URL"}), 400
    soup = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    t_meta = soup.find("meta", property="og:title")
    d_meta = soup.find("meta", property="og:description")
    i_meta = soup.find("meta", property="og:image")

    title = t_meta["content"] if t_meta else "Unknown"
    synopsis = d_meta["content"] if d_meta else "-"
    poster = normalize_img_url(i_meta["content"]) if i_meta else None

    episodes = []
    data = extract_next_data(soup)
    raw_eps = None

    if data:
        # Logic complex buat nyari episode list
        def find_eps(obj):
            lists = []
            def walk(x):
                if isinstance(x, dict):
                    for v in x.values(): walk(v)
                elif isinstance(x, list):
                    if x and all(isinstance(i, dict) for i in x):
                        keys = set()
                        for i in x[:5]: keys |= set(i.keys())
                        if ("chapterId" in keys or "id" in keys) and ("chapterName" in keys or "name" in keys):
                            lists.append(x)
                    for i in x: walk(i)
            walk(obj)
            return lists
        
        found = find_eps(data)
        if found: raw_eps = max(found, key=len)

    if raw_eps:
        m = re.search(r"/drama/(\d+)", url)
        bid_url = m.group(1) if m else None
        for ep in raw_eps:
            eid = ep.get("chapterId") or ep.get("id")
            if not eid: continue
            ename = ep.get("chapterName") or ep.get("name") or "Episode"
            bid = ep.get("bookId") or bid_url or "0"
            episodes.append({
                "name": ename,
                "url": f"{BASE_URL}/in/video/{bid}_{slugify(title)}/{eid}_{slugify(ename)}"
            })
    
    if not episodes:
        for link in soup.find_all("a", href=re.compile(r"/in/video/")):
            episodes.append({
                "name": link.get_text(strip=True),
                "url": BASE_URL + link["href"] if link["href"].startswith("/") else link["href"]
            })

    return jsonify({"title": title, "synopsis": synopsis, "poster": poster, "total_episodes": len(episodes), "episodes": episodes})

@app.route("/api/episode")
def episode_detail():
    url = request.args.get("url")
    if not url: return jsonify({"error": "No URL"}), 400

    soup = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    t = soup.find("title")
    title = t.get_text(strip=True) if t else "Episode"
    
    # --- LOGIC EXTRAK VIDEO ---
    stream_url = None
    note = "DRM protected content"
    
    # 1. Ambil data JSON
    data = extract_next_data(soup)
    
    if data:
        # 2. Cari link m3u8 atau mp4 di dalam data json
        stream_url = find_video_stream(data)
        if stream_url:
            note = "Stream found. Usually expires quickly (Signed URL)."
    
    return jsonify({
        "title": title,
        "page_url": url,
        "stream_url": stream_url,
        "note": note
    })

if __name__ == "__main__":
    app.run(debug=True, port=3000)
