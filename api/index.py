from flask import Flask, request, jsonify, make_response
import requests
from bs4 import BeautifulSoup
import re
import json

app = Flask(__name__)

# --- CONFIG ---
ALLOWED_ORIGINS = [
    "https://frontend-kamu.vercel.app", 
    "http://localhost:3000",
    "http://127.0.0.1:5500"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

BASE_URL = "https://www.dramabox.com"

# --- SECURITY ---
@app.before_request
def check_origin():
    if request.method == "OPTIONS": return _build_cors_preflight_response()
    origin = request.headers.get('Origin')
    if origin and origin not in ALLOWED_ORIGINS:
        return jsonify({"error": "Forbidden"}), 403

def _build_cors_preflight_response():
    resp = make_response()
    resp.headers.add("Access-Control-Allow-Origin", request.headers.get('Origin', '*'))
    resp.headers.add("Access-Control-Allow-Headers", "*")
    resp.headers.add("Access-Control-Allow-Methods", "*")
    return resp

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers.add("Access-Control-Allow-Origin", origin)
    return response

# --- HELPER SAKTI (Hybrid BeautifulSoup + Regex) ---
def get_soup(url, params=None):
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=15)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser'), response.text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None, None

def extract_dramas_html(soup):
    """Cara lama: ambil dari HTML element (untuk Browse/Home)"""
    dramas = []
    links = soup.find_all('a', href=re.compile(r'/in/drama/'))
    seen = set()
    
    for link in links:
        href = link.get('href')
        full_url = BASE_URL + href if href.startswith('/') else href
        if full_url in seen: continue
        seen.add(full_url)

        title_tag = link.find(['h3', 'p', 'div', 'span'], class_=re.compile(r'title|name|text|ell', re.I))
        title = title_tag.get_text(strip=True) if title_tag else "No Title"
        
        img_tag = link.find('img')
        thumbnail = img_tag.get('src') if img_tag else None
        
        if title == "No Title" and img_tag and img_tag.get('alt'):
            title = img_tag.get('alt')

        if title != "No Title" or thumbnail:
            dramas.append({"title": title, "url": full_url, "thumbnail": thumbnail})
    return dramas

def extract_from_json_script(html_text, key_pattern):
    """
    Cara baru: Cari data tersembunyi di script NEXT_DATA
    Tanpa bongkar semua, cuma cari pola text aja biar gak error.
    """
    try:
        # Cari script JSON Next.js
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_text)
        if match:
            data = json.loads(match.group(1))
            # Fungsi recursive cari key "chapterList" atau "searchData"
            return find_key_recursive(data, key_pattern)
    except:
        pass
    return None

def find_key_recursive(obj, key_pattern):
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
    return jsonify({"status": "Active", "endpoints": ["/api/home", "/api/search?q=", "/api/drama?url="]})

@app.route('/api/home')
def home():
    # TRICK: Ambil dari /browse/0/1 karena isinya LEBIH BANYAK dari home biasa
    target_url = f"{BASE_URL}/in/browse/0/1"
    soup, html = get_soup(target_url)
    
    if not soup: return jsonify({"error": "Failed"}), 500
    
    dramas = extract_dramas_html(soup)
    return jsonify({"count": len(dramas), "data": dramas})

@app.route('/api/search')
def search():
    query = request.args.get('q')
    if not query: return jsonify({"error": "No query"}), 400

    # PENTING: Search pake HTML biasa bakal KOSONG. Harus ambil dari JSON hidden.
    target_url = f"{BASE_URL}/in/search"
    soup, html = get_soup(target_url, params={"searchValue": query})
    
    if not html: return jsonify({"error": "Failed"}), 500

    results = []
    
    # 1. Coba cara "Parsing Script" (Paling ampuh buat Search)
    search_data = extract_from_json_script(html, r'searchData|resultList')
    
    if search_data and isinstance(search_data, dict):
        # Biasanya ada di dalam list
        items = search_data.get('list', []) or search_data.get('results', [])
        for item in items:
            title = item.get('bookName') or item.get('title')
            bid = item.get('bookId') or item.get('id')
            cover = item.get('cover') or item.get('coverUrl')
            
            if title and bid:
                results.append({
                    "title": title,
                    "thumbnail": cover,
                    "url": f"{BASE_URL}/in/drama/{bid}/{title.replace(' ', '-')}"
                })
    
    # 2. Fallback: Kalau script gagal, coba HTML biasa (walau jarang berhasil)
    if not results and soup:
        results = extract_dramas_html(soup)

    return jsonify({"query": query, "count": len(results), "data": results})

@app.route('/api/browse')
def browse():
    genre = request.args.get('genre_id', '0')
    page = request.args.get('page', '1')
    target_url = f"{BASE_URL}/in/browse/{genre}/{page}"
    
    soup, html = get_soup(target_url)
    if not soup: return jsonify({"error": "Failed"}), 500

    dramas = extract_dramas_html(soup)
    
    # Simple Genre Extractor
    genres = [{"id": "0", "name": "All"}]
    for link in soup.find_all('a', href=re.compile(r'/in/browse/\d+')):
        match = re.search(r'/browse/(\d+)', link['href'])
        if match:
            gname = link.get_text(strip=True)
            if gname: 
                genres.append({"id": match.group(1), "name": gname})
    
    # Hapus duplikat genre
    unique_genres = list({v['id']:v for v in genres}.values())

    return jsonify({"page": int(page), "genres": unique_genres, "data": dramas})

@app.route('/api/drama')
def drama_detail():
    url = request.args.get('url')
    if not url: return jsonify({"error": "No URL"}), 400
    
    soup, html = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    # --- Info Drama (Metadata HTML - Aman) ---
    title = soup.find("meta", property="og:title")
    desc = soup.find("meta", property="og:description")
    img = soup.find("meta", property="og:image")
    
    title_txt = title['content'] if title else "Unknown"
    synopsis = desc['content'] if desc else "-"
    poster = img['content'] if img else None

    # --- FULL EPISODE FIX ---
    episodes = []
    
    # Method 1: Coba ambil FULL list dari script JSON (Biar dapet > 11 eps)
    # Kita cari key "chapterList" di dalam source code
    raw_chapters = extract_from_json_script(html, r'chapterList|episodeList')
    
    if raw_chapters and isinstance(raw_chapters, list):
        # BERHASIL DAPET FULL DATA
        for ep in raw_chapters:
            ep_name = ep.get('chapterName') or ep.get('name') or f"Episode"
            ep_id = ep.get('chapterId') or ep.get('id')
            book_id = ep.get('bookId') # Kadang perlu buat URL
            
            # Kita coba bangun URL manual yang valid
            # Url format: /in/video/BOOKID_TITLE/CHAPTERID_TITLE
            if ep_id:
                # Ambil Book ID dari URL asli jika di JSON ga ada
                current_book_id = re.search(r'/drama/(\d+)', url)
                bid = book_id or (current_book_id.group(1) if current_book_id else "0")
                
                slug_t = title_txt.replace(" ", "-")
                slug_e = ep_name.replace(" ", "-")
                
                episodes.append({
                    "name": ep_name,
                    "url": f"{BASE_URL}/in/video/{bid}_{slug_t}/{ep_id}_{slug_e}"
                })
    
    # Method 2: Fallback ke HTML biasa kalau JSON gagal (Dapetnya dikit gapapa drpd error)
    if not episodes:
        for link in soup.find_all('a', href=re.compile(r'/in/video/')):
            episodes.append({
                "name": link.get_text(strip=True),
                "url": BASE_URL + link['href'] if link['href'].startswith('/') else link['href']
            })

    return jsonify({
        "title": title_txt,
        "synopsis": synopsis,
        "poster": poster,
        "total_episodes": len(episodes),
        "episodes": episodes
    })

@app.route('/api/episode')
def episode_detail():
    url = request.args.get('url')
    if not url: return jsonify({"error": "No URL"}), 400
    
    soup, html = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    title = soup.find("title")
    return jsonify({
        "title": title.get_text(strip=True) if title else "Episode",
        "page_url": url,
        "stream_url": "Stream URL protected by DRM (Not extractable via simple request)"
    })

if __name__ == '__main__':
    app.run(debug=True, port=3000)
