from flask import Flask, request, jsonify, make_response
import requests
from bs4 import BeautifulSoup
import json
import re

app = Flask(__name__)

# --- CONFIG ---
ALLOWED_ORIGINS = [
    "https://frontend-kamu.vercel.app", 
    "http://localhost:3000",
    "http://127.0.0.1:5500"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://www.dramabox.com"

# --- SECURITY ---
@app.before_request
def check_origin():
    if request.method == "OPTIONS": return _cors_response()
    origin = request.headers.get('Origin')
    if origin and origin not in ALLOWED_ORIGINS:
        return jsonify({"error": "Forbidden"}), 403

def _cors_response():
    resp = make_response()
    resp.headers.add("Access-Control-Allow-Origin", request.headers.get('Origin', '*'))
    resp.headers.add("Access-Control-Allow-Headers", "*")
    resp.headers.add("Access-Control-Allow-Methods", "*")
    return resp

@app.after_request
def add_cors(resp):
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        resp.headers.add("Access-Control-Allow-Origin", origin)
    return resp

# --- CORE LOGIC (JSON EXTRACTION) ---
def get_next_data(url, params=None):
    """
    Fungsi sakti buat ngambil data JSON murni dari __NEXT_DATA__
    Ini ngelewatin limitasi HTML parsing biasa.
    """
    try:
        res = requests.get(url, headers=HEADERS, params=params, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Cari tag script __NEXT_DATA__
        script = soup.find("script", id="__NEXT_DATA__")
        if script:
            return json.loads(script.string)
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

def find_key_recursive(obj, key_pattern):
    """Bantu cari key tertentu di dalam JSON yang dalem banget"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if re.search(key_pattern, k, re.I):
                return v
            found = find_key_recursive(v, key_pattern)
            if found: return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_key_recursive(item, key_pattern)
            if found: return found
    return None

# --- ROUTES ---

@app.route('/')
def index():
    return jsonify({"status": "Dramabox API V2 (JSON Mode)", "msg": "Ready to scraping full episodes"})

@app.route('/api/home')
def home():
    # Pake endpoint browse genre=0 (All) buat home biar dapet banyak
    # Kalau home page biasa kadang isinya cuma banner
    # Kita ambil page 1
    data = get_next_data(f"{BASE_URL}/in/browse/0/1")
    
    dramas = []
    
    if data:
        # Coba cari list drama di dalam JSON browse
        # Struktur biasanya: props -> pageProps -> data -> list
        # Kita cari generic list yang isinya ada 'bookId' atau 'title'
        raw_list = find_key_recursive(data, r'^(list|items|contents)$')
        
        if raw_list and isinstance(raw_list, list):
            for item in raw_list:
                # Mapping data dari JSON asli Dramabox (fieldnya bisa beda2 dikit)
                d_id = item.get('bookId') or item.get('id')
                title = item.get('bookName') or item.get('title')
                cover = item.get('cover') or item.get('coverUrl')
                
                if d_id and title:
                    # Bersihin URL cover kalau relatif
                    if cover and not cover.startswith('http'):
                        cover = item.get('cover_url') # kadang ada field lain

                    dramas.append({
                        "title": title,
                        "thumbnail": cover,
                        # Bangun URL manual biar rapi
                        "url": f"{BASE_URL}/in/drama/{d_id}/{title.replace(' ', '-')}"
                    })
    
    return jsonify({"source": "json_mode", "count": len(dramas), "data": dramas})

@app.route('/api/search')
def search():
    query = request.args.get('q')
    if not query: return jsonify({"error": "No query"}), 400

    # Search biasanya butuh encode URL yang bener
    # Kita tembak URL search page-nya
    data = get_next_data(f"{BASE_URL}/in/search", params={"searchValue": query})
    
    results = []
    if data:
        # Cari data search result di JSON
        # Biasanya di props -> pageProps -> searchData -> list
        raw_results = find_key_recursive(data, r'search.*list|result')
        
        if raw_results and isinstance(raw_results, list):
            for item in raw_results:
                d_id = item.get('bookId')
                title = item.get('bookName')
                cover = item.get('cover')
                
                if d_id and title:
                    results.append({
                        "title": title,
                        "thumbnail": cover,
                        "url": f"{BASE_URL}/in/drama/{d_id}/{title.replace(' ', '-')}"
                    })

    return jsonify({"query": query, "count": len(results), "data": results})

@app.route('/api/drama')
def drama_detail():
    url = request.args.get('url')
    if not url: return jsonify({"error": "No URL"}), 400

    data = get_next_data(url)
    
    if not data:
        return jsonify({"error": "Failed to fetch JSON data"}), 500

    # 1. Ambil Info Drama
    # Biasanya di props -> pageProps -> detail -> bookInfo
    info = find_key_recursive(data, r'bookInfo|dramaInfo|detail') or {}
    
    title = info.get('bookName') or info.get('title') or "Unknown"
    synopsis = info.get('introduction') or info.get('desc') or info.get('summary') or "-"
    poster = info.get('cover') or info.get('coverUrl')

    # 2. Ambil FULL Episode List
    # Biasanya di props -> pageProps -> chapterList (Ini isinya semua chapter!)
    raw_episodes = find_key_recursive(data, r'chapterList|episodeList') or []
    
    episodes = []
    for ep in raw_episodes:
        ep_id = ep.get('chapterId') or ep.get('id')
        ep_name = ep.get('chapterName') or ep.get('name') or f"Episode {len(episodes)+1}"
        
        # Kadang ID drama juga butuh buat URL video
        book_id = info.get('bookId') or ep.get('bookId')
        
        if ep_id and book_id:
            # Format URL video dramabox: /in/video/BOOKID_SLUG/CHAPTERID_SLUG
            # Kita construct manual biar valid
            slug_title = title.replace(" ", "-")
            slug_ep = ep_name.replace(" ", "-")
            full_video_url = f"{BASE_URL}/in/video/{book_id}_{slug_title}/{ep_id}_{slug_ep}"
            
            episodes.append({
                "name": ep_name,
                "url": full_video_url,
                "is_locked": ep.get('isLocked', False) # Bonus: Info kalau kekunci
            })

    # Sort episode biar urut (jaga-jaga)
    # episodes.sort(key=lambda x: x['name']) 

    return jsonify({
        "title": title,
        "synopsis": synopsis,
        "poster": poster,
        "total_episodes_found": len(episodes),
        "episodes": episodes
    })

@app.route('/api/episode')
def episode_detail():
    url = request.args.get('url')
    if not url: return jsonify({"error": "No URL"}), 400
    
    data = get_next_data(url)
    # Parsing basic info episode dari JSON page episode
    # Logicnya mirip detail
    
    info = find_key_recursive(data, r'chapterInfo|videoInfo') or {}
    title = info.get('chapterName') or "Unknown"
    
    return jsonify({
        "episode_title": title,
        "page_url": url,
        "note": "Video URL requires DRM token extraction which is complex."
    })

if __name__ == '__main__':
    app.run(debug=True, port=3000)
