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
    "Accept": "*/*",
    "x-nextjs-data": "1"  # Header Wajib buat nipu Next.js
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

# --- HELPER SAKTI: Next.js Unlocker ---
def get_build_id():
    """
    Kita butuh 'Build ID' buat akses API internal Next.js.
    ID ini berubah tiap kali Dramabox update webnya.
    """
    try:
        # Hit home bentar buat nyolong Build ID dari script
        r = requests.get(f"{BASE_URL}/in", headers={"User-Agent": HEADERS["User-Agent"]})
        match = re.search(r'"buildId":"(.*?)"', r.text)
        if match:
            return match.group(1)
    except:
        pass
    return None

def extract_dramas_html(soup):
    """Helper lama buat Home/Browse (tetep dipake)"""
    dramas = []
    links = soup.find_all('a', href=re.compile(r'/in/drama/'))
    seen = set()
    for link in links:
        href = link.get('href')
        full_url = BASE_URL + href if href.startswith('/') else href
        if full_url in seen: continue
        seen.add(full_url)
        
        t_tag = link.find(['h3', 'div'], class_=re.compile(r'title|name|text', re.I))
        title = t_tag.get_text(strip=True) if t_tag else "No Title"
        
        img = link.find('img')
        thumb = img.get('src') if img else None
        
        if title != "No Title" or thumb:
            dramas.append({"title": title, "url": full_url, "thumbnail": thumb})
    return dramas

# --- ROUTES ---

@app.route('/')
def index():
    return jsonify({"status": "Ready", "msg": "Search fixed with Next.js Data Fetching"})

@app.route('/api/home')
def home():
    # Home tetep pake cara HTML biasa (karena udah jalan)
    r = requests.get(f"{BASE_URL}/in", headers=HEADERS)
    soup = BeautifulSoup(r.text, 'html.parser')
    dramas = extract_dramas_html(soup)
    return jsonify({"count": len(dramas), "data": dramas})

@app.route('/api/search')
def search():
    query = request.args.get('q')
    if not query: return jsonify({"error": "No query"}), 400

    # 1. Ambil Kunci (Build ID)
    build_id = get_build_id()
    if not build_id:
        return jsonify({"error": "Failed to bypass security (No Build ID)"}), 500

    # 2. Tembak URL API Rahasia Next.js
    # Format: /_next/data/{BUILD_ID}/in/search.json?searchValue=...
    target_api = f"{BASE_URL}/_next/data/{build_id}/in/search.json"
    
    try:
        # Request JSON langsung (bukan HTML)
        res = requests.get(target_api, headers=HEADERS, params={"searchValue": query})
        data = res.json()
        
        results = []
        # Lokasi data biasanya ada di sini:
        # pageProps -> searchData -> list
        search_data = data.get('pageProps', {}).get('searchData', {})
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
        
        return jsonify({
            "query": query,
            "method": "next_data_api",
            "count": len(results),
            "data": results
        })

    except Exception as e:
        return jsonify({"error": str(e), "hint": "API structure might have changed"}), 500

@app.route('/api/browse')
def browse():
    # Browse logic tetep sama kayak lu punya
    g_id = request.args.get('genre_id', '0')
    page = request.args.get('page', '1')
    r = requests.get(f"{BASE_URL}/in/browse/{g_id}/{page}", headers=HEADERS)
    soup = BeautifulSoup(r.text, 'html.parser')
    dramas = extract_dramas_html(soup)
    return jsonify({"page": page, "data": dramas})

@app.route('/api/drama')
def drama():
    # Drama logic tetep sama (Regex/BS4)
    url = request.args.get('url')
    r = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, 'html.parser')
    
    title = soup.find("meta", property="og:title")
    desc = soup.find("meta", property="og:description")
    img = soup.find("meta", property="og:image")
    
    episodes = []
    for link in soup.find_all('a', href=re.compile(r'/in/video/')):
        episodes.append({
            "name": link.get_text(strip=True),
            "url": BASE_URL + link['href'] if link['href'].startswith('/') else link['href']
        })

    return jsonify({
        "title": title['content'] if title else "Unknown",
        "synopsis": desc['content'] if desc else "-",
        "poster": img['content'] if img else None,
        "episodes": episodes
    })

@app.route('/api/episode')
def episode():
    url = request.args.get('url')
    r = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, 'html.parser')
    t = soup.find("title")
    return jsonify({"title": t.get_text(strip=True) if t else "Ep", "page_url": url})

if __name__ == '__main__':
    app.run(debug=True, port=3000)
