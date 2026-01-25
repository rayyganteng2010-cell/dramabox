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

# Ganti User-Agent biar servernya "takut" dan ngasih data lengkap (SSR)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.dramabox.com/",
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

# --- HELPER SAKTI (Fixed Version) ---
def get_soup(url, params=None):
    try:
        # Tambahin session biar cookies nyimpen (bantu bypass bot detect)
        session = requests.Session()
        response = session.get(url, headers=HEADERS, params=params, timeout=15)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def extract_dramas_html(soup):
    """Helper buat ambil list drama dari HTML biasa"""
    dramas = []
    # Cari container drama (biasanya grid)
    links = soup.find_all('a', href=re.compile(r'/in/drama/'))
    seen = set()
    
    for link in links:
        href = link.get('href')
        full_url = BASE_URL + href if href.startswith('/') else href
        if full_url in seen: continue
        seen.add(full_url)

        # Cari Title di dalem link
        # Pola class ditambah biar nangkep elemen search result
        title_tag = link.find(['h3', 'p', 'div', 'span'], class_=re.compile(r'title|name|text|ell|item', re.I))
        title = title_tag.get_text(strip=True) if title_tag else "No Title"
        
        # Cari Gambar
        img_tag = link.find('img')
        thumbnail = img_tag.get('src') if img_tag else None
        
        # Fallback Title dari Alt Gambar
        if title == "No Title" and img_tag and img_tag.get('alt'):
            title = img_tag.get('alt')

        # Filter hasil kosong/sampah
        if title != "No Title" or thumbnail:
            dramas.append({"title": title, "url": full_url, "thumbnail": thumbnail})
    return dramas

def extract_from_json_script(soup, key_pattern):
    """
    FIXED: Pake BeautifulSoup buat nyari script tag, bukan Regex.
    Ini jauh lebih stabil buat ambil data tersembunyi.
    """
    try:
        # 1. Cari script __NEXT_DATA__
        script = soup.find("script", id="__NEXT_DATA__")
        if script:
            data = json.loads(script.string)
            # 2. Cari key yang diminta (misal: searchData)
            return find_key_recursive(data, key_pattern)
    except Exception as e:
        print(f"JSON Extract Error: {e}")
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
    return jsonify({"status": "Active", "msg": "Search fixed with Soup extraction"})

@app.route('/api/home')
def home():
    # Tembak browse page 1 biar dapet list banyak
    soup = get_soup(f"{BASE_URL}/in/browse/0/1")
    if not soup: return jsonify({"error": "Failed"}), 500
    
    dramas = extract_dramas_html(soup)
    return jsonify({"count": len(dramas), "data": dramas})

@app.route('/api/search')
def search():
    query = request.args.get('q')
    if not query: return jsonify({"error": "No query"}), 400

    # 1. Request ke URL Search
    target_url = f"{BASE_URL}/in/search"
    soup = get_soup(target_url, params={"searchValue": query})
    
    if not soup: return jsonify({"error": "Failed"}), 500

    results = []
    
    # 2. CARA JITU: Ambil dari JSON tersembunyi (karena HTML search biasanya kosong)
    # Kita cari key 'searchData' atau 'list' di dalam script next data
    search_data = extract_from_json_script(soup, r'searchData|resultList')
    
    if search_data and isinstance(search_data, dict):
        # Struktur biasanya: { list: [...] } atau { results: [...] }
        items = search_data.get('list', []) or search_data.get('results', []) or search_data.get('data', [])
        
        for item in items:
            # Mapping data JSON ke format kita
            title = item.get('bookName') or item.get('title')
            bid = item.get('bookId') or item.get('id')
            cover = item.get('cover') or item.get('coverUrl')
            
            if title and bid:
                results.append({
                    "title": title,
                    "thumbnail": cover,
                    "url": f"{BASE_URL}/in/drama/{bid}/{title.replace(' ', '-')}"
                })
    
    # 3. Fallback: Kalau JSON gagal, coba parsing HTML manual (Jaga-jaga)
    if not results:
        results = extract_dramas_html(soup)

    return jsonify({
        "query": query,
        "count": len(results),
        "data": results
    })

@app.route('/api/browse')
def browse():
    genre = request.args.get('genre_id', '0')
    page = request.args.get('page', '1')
    
    soup = get_soup(f"{BASE_URL}/in/browse/{genre}/{page}")
    if not soup: return jsonify({"error": "Failed"}), 500

    dramas = extract_dramas_html(soup)
    
    # Extract Genres
    genres = [{"id": "0", "name": "All"}]
    for link in soup.find_all('a', href=re.compile(r'/in/browse/\d+')):
        match = re.search(r'/browse/(\d+)', link['href'])
        if match:
            gname = link.get_text(strip=True)
            if gname: genres.append({"id": match.group(1), "name": gname})
    
    # Unique genres
    unique_genres = list({v['id']:v for v in genres}.values())

    return jsonify({"page": int(page), "genres": unique_genres, "data": dramas})

@app.route('/api/drama')
def drama_detail():
    url = request.args.get('url')
    if not url: return jsonify({"error": "No URL"}), 400
    
    soup = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    # Info
    title = soup.find("meta", property="og:title")
    desc = soup.find("meta", property="og:description")
    img = soup.find("meta", property="og:image")
    
    # List Episode
    episodes = []
    
    # Coba ambil FULL episode dari JSON dulu
    raw_eps = extract_from_json_script(soup, r'chapterList|episodeList')
    
    if raw_eps and isinstance(raw_eps, list):
        for ep in raw_eps:
            ep_id = ep.get('chapterId') or ep.get('id')
            if ep_id:
                ep_name = ep.get('chapterName') or f"Episode"
                # Buat URL manual yang valid
                # Pattern: /in/video/BOOKID_SLUG/CHAPTERID_SLUG
                # Kita perlu extract bookID dari URL atau JSON
                book_id = ep.get('bookId')
                if not book_id:
                    # Ambil dari URL input user
                    match = re.search(r'/drama/(\d+)', url)
                    book_id = match.group(1) if match else "0"
                
                # Slugify
                t_slug = (title['content'] if title else "drama").replace(" ", "-")
                e_slug = ep_name.replace(" ", "-")
                
                episodes.append({
                    "name": ep_name,
                    "url": f"{BASE_URL}/in/video/{book_id}_{t_slug}/{ep_id}_{e_slug}"
                })
    
    # Kalau JSON gagal, pake HTML biasa
    if not episodes:
        for link in soup.find_all('a', href=re.compile(r'/in/video/')):
            episodes.append({
                "name": link.get_text(strip=True),
                "url": BASE_URL + link['href'] if link['href'].startswith('/') else link['href']
            })

    return jsonify({
        "title": title['content'] if title else "Unknown",
        "synopsis": desc['content'] if desc else "-",
        "poster": img['content'] if img else None,
        "total_episodes": len(episodes),
        "episodes": episodes
    })

@app.route('/api/episode')
def episode_detail():
    url = request.args.get('url')
    if not url: return jsonify({"error": "No URL"}), 400
    
    soup = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    t = soup.find("title")
    return jsonify({"title": t.get_text(strip=True) if t else "Episode", "page_url": url})

if __name__ == '__main__':
    app.run(debug=True, port=3000)
