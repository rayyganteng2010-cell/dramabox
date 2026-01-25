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
    "http://localhost:2435",
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

        # kalau 403, coba ulang dengan referer yang lebih nyambung
        if resp.status_code == 403:
            headers["Referer"] = f"{BASE_URL}/in/"
            resp = SESSION.get(url, headers=headers, params=params, timeout=15, allow_redirects=True)

        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[get_soup] Error fetching {url}: {e}")
        return None


# --- URL/IMAGE NORMALIZER ---
IMG_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", re.I)

def normalize_url(u: str):
    if not u or not isinstance(u, str):
        return None
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return BASE_URL + u
    return u

def normalize_img_url(u: str):
    if not u or not isinstance(u, str):
        return None
    u = u.strip()

    # next/image proxy: /_next/image?url=ENCODED...
    if "/_next/image" in u and "url=" in u:
        m = re.search(r"[?&]url=([^&]+)", u)
        if m:
            u = unquote(m.group(1))

    return normalize_url(u)


# --- HTML DRAMA LIST FALLBACK ---
def extract_dramas_html(soup):
    dramas = []
    links = soup.find_all("a", href=re.compile(r"/in/drama/"))
    seen = set()

    for link in links:
        href = link.get("href")
        if not href:
            continue

        full_url = BASE_URL + href if href.startswith("/") else href
        if full_url in seen:
            continue
        seen.add(full_url)

        title_tag = link.find(
            ["h3", "p", "div", "span"],
            class_=re.compile(r"title|name|text|ell|item", re.I)
        )
        title = title_tag.get_text(strip=True) if title_tag else "No Title"

        img_tag = link.find("img")
        thumbnail = img_tag.get("src") if img_tag else None

        if title == "No Title" and img_tag and img_tag.get("alt"):
            title = img_tag.get("alt")

        if thumbnail:
            thumbnail = normalize_img_url(thumbnail)

        if title != "No Title" or thumbnail:
            dramas.append({"title": title, "url": full_url, "thumbnail": thumbnail})

    return dramas


# --- NEXT DATA EXTRACTOR ---
def extract_next_data(soup):
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return None
    try:
        raw = script.get_text(strip=True) or script.string
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        print(f"[extract_next_data] parse error: {e}")
        return None


# --- GENERIC JSON WALKERS ---
def flatten_strings(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            flatten_strings(v, out)
    elif isinstance(obj, list):
        for i in obj:
            flatten_strings(i, out)
    elif isinstance(obj, str):
        out.append(obj)

def slugify(s):
    s = (s or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-zA-Z0-9\-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "drama"


# --- THUMBNAIL PICKER (ROBUST) ---
def pick_thumbnail(item: dict):
    if not isinstance(item, dict):
        return None

    candidate_keys = [
        "cover", "coverUrl", "coverURL",
        "bookCover", "bookCoverUrl", "bookCoverURL",
        "poster", "posterUrl", "posterURL",
        "image", "imageUrl", "img", "imgUrl",
        "thumbnail", "thumbnailUrl",
        "icon", "iconUrl",
        "verticalCover", "horizontalCover",
        "pic", "picUrl"
    ]

    for k in candidate_keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return normalize_img_url(v)
        if isinstance(v, dict):
            for subk in ("url", "src", "link", "path"):
                sv = v.get(subk)
                if isinstance(sv, str) and sv.strip():
                    return normalize_img_url(sv)

    strings = []
    flatten_strings(item, strings)

    for s in strings:
        ss = s.lower()
        if ("cover" in ss or "poster" in ss or "thumb" in ss) and (("http" in ss) or ss.startswith("//") or ss.startswith("/")):
            if IMG_EXT_RE.search(ss) or "image" in ss:
                return normalize_img_url(s)

    for s in strings:
        ss = s.lower()
        if (("http" in ss) or ss.startswith("//") or ss.startswith("/")) and (IMG_EXT_RE.search(ss) or "image" in ss):
            return normalize_img_url(s)

    return None


# --- FIND SEARCH LIST IN NEXT DATA ---
def find_list_items_by_fields(obj):
    found_lists = []

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            if x and all(isinstance(i, dict) for i in x):
                keys = set()
                for i in x[:10]:
                    keys |= set(i.keys())
                if ("bookId" in keys or "id" in keys) and ("bookName" in keys or "title" in keys or "name" in keys):
                    found_lists.append(x)
            for i in x:
                walk(i)

    walk(obj)
    return found_lists

def map_items_to_results(items):
    results = []
    for item in items:
        title = item.get("bookName") or item.get("title") or item.get("name")
        bid = item.get("bookId") or item.get("id")
        if not title or not bid:
            continue

        cover = pick_thumbnail(item)

        results.append({
            "title": title,
            "thumbnail": cover,
            "url": f"{BASE_URL}/in/drama/{bid}/{slugify(title)}"
        })
    return results


# =========================
# ROUTES
# =========================

@app.route("/")
def index():
    return jsonify({"status": "Active", "msg": "Dramabox scraper (search+thumbnail+embed)"})

@app.route("/api/home")
def home():
    soup = get_soup(f"{BASE_URL}/in/browse/0/1")
    if not soup:
        return jsonify({"error": "Failed"}), 500
    dramas = extract_dramas_html(soup)
    return jsonify({"count": len(dramas), "data": dramas})

@app.route("/api/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "No query"}), 400

    soup = get_soup(f"{BASE_URL}/in/search", params={"searchValue": query})
    if not soup:
        return jsonify({"error": "Failed"}), 500

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
    if not soup:
        return jsonify({"error": "Failed"}), 500

    dramas = extract_dramas_html(soup)

    genres = [{"id": "0", "name": "All"}]
    for link in soup.find_all("a", href=re.compile(r"/in/browse/\d+")):
        href = link.get("href", "")
        match = re.search(r"/browse/(\d+)", href)
        if match:
            gname = link.get_text(strip=True)
            if gname:
                genres.append({"id": match.group(1), "name": gname})

    unique_genres = list({g["id"]: g for g in genres}.values())

    return jsonify({"page": int(page), "genres": unique_genres, "data": dramas})

@app.route("/api/drama")
def drama_detail():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "No URL"}), 400

    soup = get_soup(url)
    if not soup:
        return jsonify({"error": "Failed"}), 500

    title_meta = soup.find("meta", property="og:title")
    desc_meta = soup.find("meta", property="og:description")
    img_meta = soup.find("meta", property="og:image")

    title = title_meta["content"] if title_meta and title_meta.get("content") else "Unknown"
    synopsis = desc_meta["content"] if desc_meta and desc_meta.get("content") else "-"
    poster = img_meta["content"] if img_meta and img_meta.get("content") else None
    poster = normalize_img_url(poster) if poster else None

    episodes = []

    data = extract_next_data(soup)
    raw_eps = None

    if data:
        def find_episode_lists(obj):
            lists = []
            def walk(x):
                if isinstance(x, dict):
                    for v in x.values():
                        walk(v)
                elif isinstance(x, list):
                    if x and all(isinstance(i, dict) for i in x):
                        keys = set()
                        for i in x[:10]:
                            keys |= set(i.keys())
                        if ("chapterId" in keys or "id" in keys) and ("chapterName" in keys or "name" in keys):
                            lists.append(x)
                    for i in x:
                        walk(i)
            walk(obj)
            return lists

        eps_lists = find_episode_lists(data)
        if eps_lists:
            raw_eps = max(eps_lists, key=len)

    if raw_eps and isinstance(raw_eps, list):
        m = re.search(r"/drama/(\d+)", url)
        book_id_from_url = m.group(1) if m else None

        for ep in raw_eps:
            ep_id = ep.get("chapterId") or ep.get("id")
            if not ep_id:
                continue
            ep_name = ep.get("chapterName") or ep.get("name") or "Episode"
            book_id = ep.get("bookId") or book_id_from_url or "0"

            episodes.append({
                "name": ep_name,
                "url": f"{BASE_URL}/in/video/{book_id}_{slugify(title)}/{ep_id}_{slugify(ep_name)}"
            })

    if not episodes:
        for link in soup.find_all("a", href=re.compile(r"/in/video/")):
            href = link.get("href")
            if not href:
                continue
            episodes.append({
                "name": link.get_text(strip=True) or "Episode",
                "url": normalize_url(href)
            })

    return jsonify({
        "title": title,
        "synopsis": synopsis,
        "poster": poster,
        "total_episodes": len(episodes),
        "episodes": episodes
    })

@app.route("/api/episode")
def episode_detail():
    """
    Ambil info episode + player/embed URL (bukan direct stream url).
    """
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "No URL"}), 400

    soup = get_soup(url)
    if not soup:
        return jsonify({"error": "Failed"}), 500

    t = soup.find("title")
    title = t.get_text(strip=True) if t else "Episode"

    og_video = soup.find("meta", property="og:video")
    og_image = soup.find("meta", property="og:image")
    og_video_url = normalize_url(og_video["content"]) if og_video and og_video.get("content") else None
    poster = normalize_img_url(og_image["content"]) if og_image and og_image.get("content") else None

    embed_url = None

    # 1) iframe src
    iframe = soup.find("iframe")
    if iframe and iframe.get("src"):
        embed_url = normalize_url(iframe["src"])

    # 2) cari di script inline: iframeUrl/embedUrl/playerUrl
    if not embed_url:
        for sc in soup.find_all("script"):
            txt = sc.get_text(" ", strip=True)
            if not txt:
                continue
            m = re.search(r'(iframeUrl|embedUrl|playerUrl)"\s*:\s*"(https?:\\/\\/[^"]+)"', txt, re.I)
            if m:
                embed_url = m.group(2).replace("\\/", "/")
                embed_url = normalize_url(embed_url)
                break

    # 3) dari __NEXT_DATA__: cari string yang mengandung embed/player/iframe (skip direct media)
    if not embed_url:
        data = extract_next_data(soup)
        if data:
            strings = []
            flatten_strings(data, strings)
            for s in strings:
                ss = s.lower()
                if "http" not in ss:
                    continue
                if any(x in ss for x in [".m3u8", ".mpd", ".mp4"]):
                    continue
                if ("iframe" in ss or "embed" in ss or "player" in ss):
                    embed_url = s.replace("\\/", "/")
                    embed_url = normalize_url(embed_url)
                    break

    return jsonify({
        "title": title,
        "page_url": url,
        "poster": poster,
        "og_video": og_video_url,
        "embed_url": embed_url
    })


if __name__ == "__main__":
    app.run(debug=True, port=3000)
