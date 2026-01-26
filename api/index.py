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

# --- SESSION GLOBAL ---
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# --- SECURITY ---
@app.before_request
def check_origin():
    if request.method == "OPTIONS": return _build_cors_preflight_response()
    origin = request.headers.get("Origin")
    if origin and origin not in ALLOWED_ORIGINS: return jsonify({"error": "Forbidden"}), 403

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
    return response

# --- HELPERS ---
_last_warm = 0
def warmup():
    global _last_warm
    now = time.time()
    if now - _last_warm < 60: return
    try:
        SESSION.get(f"{BASE_URL}/in/", timeout=15)
        _last_warm = now
    except: pass

def get_soup(url, params=None):
    try:
        warmup()
        resp = SESSION.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"Error: {e}")
        return None

def extract_next_data(soup):
    script = soup.find("script", id="__NEXT_DATA__")
    if not script: return None
    try: return json.loads(script.get_text(strip=True))
    except: return None

def slugify(s):
    s = re.sub(r"[^a-zA-Z0-9\-]+", "-", (s or "").strip())
    return s.strip("-") or "drama"

def normalize_img_url(u):
    if not u or not isinstance(u, str): return None
    u = u.strip()
    if "url=" in u and "/_next/image" in u:
        m = re.search(r"url=([^&]+)", u)
        if m: u = unquote(m.group(1))
    if u.startswith("//"): return "https:" + u
    if u.startswith("/"): return BASE_URL + u
    return u

def extract_dramas_html(soup):
    dramas = []
    seen = set()
    for link in soup.find_all("a", href=re.compile(r"/in/drama/")):
        href = link.get("href")
        full = BASE_URL + href if href.startswith("/") else href
        if full in seen: continue
        seen.add(full)
        
        t = link.find(class_=re.compile(r"title|name|text", re.I))
        title = t.get_text(strip=True) if t else "No Title"
        img = link.find("img")
        thumb = normalize_img_url(img.get("src")) if img else None
        
        if title != "No Title" or thumb:
            dramas.append({"title": title, "url": full, "thumbnail": thumb})
    return dramas

# --- ROUTES ---

@app.route("/")
def index():
    return jsonify({"status": "Active", "msg": "Fixed Episode Logic"})

@app.route("/api/home")
def home():
    soup = get_soup(f"{BASE_URL}/in/browse/0/1")
    if not soup: return jsonify({"error": "Failed"}), 500
    dramas = extract_dramas_html(soup)
    return jsonify({"count": len(dramas), "data": dramas})

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q: return jsonify({"error": "No query"}), 400
    
    soup = get_soup(f"{BASE_URL}/in/search", params={"searchValue": q})
    if not soup: return jsonify({"error": "Failed"}), 500

    results = []
    data = extract_next_data(soup)
    if data:
        # Search recursive for list
        def find_list(obj):
            if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "bookId" in obj[0]: return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    found = find_list(v)
                    if found: return found
            return None
        
        items = find_list(data)
        if items:
            for i in items:
                title = i.get("bookName") or i.get("title")
                bid = i.get("bookId")
                if title and bid:
                    img = i.get("cover") or i.get("coverUrl")
                    results.append({
                        "title": title,
                        "thumbnail": normalize_img_url(img),
                        "url": f"{BASE_URL}/in/drama/{bid}/{slugify(title)}"
                    })
    
    if not results: results = extract_dramas_html(soup)
    return jsonify({"query": q, "count": len(results), "data": results})

@app.route("/api/browse")
def browse():
    g = request.args.get("genre_id", "0")
    p = request.args.get("page", "1")
    soup = get_soup(f"{BASE_URL}/in/browse/{g}/{p}")
    if not soup: return jsonify({"error": "Failed"}), 500
    
    dramas = extract_dramas_html(soup)
    genres = [{"id": "0", "name": "All"}]
    for a in soup.find_all("a", href=re.compile(r"/browse/\d+")):
        gid = re.search(r"/browse/(\d+)", a["href"]).group(1)
        genres.append({"id": gid, "name": a.get_text(strip=True)})
    
    # Unique genres
    u_genres = list({x['id']:x for x in genres}.values())
    return jsonify({"page": int(p), "genres": u_genres, "data": dramas})

@app.route("/api/drama")
def drama():
    url = request.args.get("url")
    if not url: return jsonify({"error": "No URL"}), 400
    soup = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    title = soup.find("meta", property="og:title")["content"]
    desc = soup.find("meta", property="og:description")["content"]
    img = normalize_img_url(soup.find("meta", property="og:image")["content"])

    episodes = []
    data = extract_next_data(soup)
    
    if data:
        # Cari list episode
        def find_chapters(obj):
            if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "chapterId" in obj[0]: return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    f = find_chapters(v)
                    if f: return f
            return None
            
        chapters = find_chapters(data)
        if chapters:
            m = re.search(r"/drama/(\d+)", url)
            bid_url = m.group(1) if m else "0"
            for c in chapters:
                eid = c.get("chapterId") or c.get("id")
                ename = c.get("chapterName") or c.get("name") or "Episode"
                bid = c.get("bookId") or bid_url
                episodes.append({
                    "name": ename,
                    "url": f"{BASE_URL}/in/video/{bid}_{slugify(title)}/{eid}_{slugify(ename)}",
                    "is_locked": c.get("isLocked", False) # Info tambahan
                })

    if not episodes:
        for a in soup.find_all("a", href=re.compile(r"/in/video/")):
            episodes.append({"name": a.get_text(strip=True), "url": BASE_URL + a["href"]})

    return jsonify({
        "title": title, 
        "synopsis": desc, 
        "poster": img, 
        "total_episodes": len(episodes), 
        "episodes": episodes
    })

@app.route("/api/episode")
def episode_detail():
    url = request.args.get("url")
    if not url: return jsonify({"error": "No URL"}), 400
    
    # Extract ID from URL for strict checking
    # URL format: .../chapterID_slug
    match_id = re.search(r'/(\d+)_', url.split('/')[-1])
    target_ep_id = match_id.group(1) if match_id else None

    soup = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    t = soup.find("title")
    title = t.get_text(strip=True) if t else "Episode"
    
    data = extract_next_data(soup)
    stream_url = None
    status = "Unknown"
    
    if data:
        # 1. Cari object episode yg SEDANG DIBUKA (Current Chapter)
        # Biasanya ada di keys: chapterInfo, videoInfo, currentChapter
        def find_current_chapter(obj):
            if isinstance(obj, dict):
                # Cek apakah object ini adalah chapter yang kita cari
                cid = str(obj.get("chapterId") or obj.get("id") or "")
                if cid == target_ep_id:
                    return obj
                
                for v in obj.values():
                    found = find_current_chapter(v)
                    if found: return found
            return None
        
        # Prioritas 1: Cari ID spesifik
        current_ep_data = None
        if target_ep_id:
            current_ep_data = find_current_chapter(data)
        
        # Prioritas 2: Cari generic "chapterInfo" kalau ID ga ketemu (fallback)
        if not current_ep_data:
             # Fungsi cari key recursive
            def find_key(obj, key):
                if isinstance(obj, dict):
                    if key in obj: return obj[key]
                    for v in obj.values():
                        f = find_key(v, key)
                        if f: return f
                return None
            current_ep_data = find_key(data, "chapterInfo") or find_key(data, "videoInfo")

        # 2. Extract Video URL dari object yang BENAR
        if current_ep_data:
            is_locked = current_ep_data.get("isLocked", False)
            
            if is_locked:
                status = "Locked (Premium)"
                stream_url = None # Pastikan NULL biar frontend tau ini kekunci
            else:
                # Cari link m3u8/mp4
                candidates = [
                    current_ep_data.get("m3u8"),
                    current_ep_data.get("m3u8Url"),
                    current_ep_data.get("url"),
                    current_ep_data.get("videoUrl"),
                    current_ep_data.get("src")
                ]
                
                for c in candidates:
                    if c and isinstance(c, str) and c.startswith("http"):
                        stream_url = c
                        status = "Available"
                        break
                
                if not stream_url:
                    status = "No stream URL found in data (Might be region locked)"
        else:
            status = "Episode data not found in JSON"

    return jsonify({
        "title": title,
        "page_url": url,
        "stream_url": stream_url,
        "status": status,
        "target_episode_id": target_ep_id
    })

if __name__ == "__main__":
    app.run(debug=True, port=3000)
