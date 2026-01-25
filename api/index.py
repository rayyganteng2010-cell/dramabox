from flask import Flask, request, jsonify, make_response
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__)

# --- KONFIGURASI SECURITY ---
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

# --- MIDDLEWARE SECURITY ---
@app.before_request
def check_origin():
    if request.method == "OPTIONS":
        return _build_cors_preflight_response()
    
    origin = request.headers.get('Origin')
    if origin and origin not in ALLOWED_ORIGINS:
        return jsonify({"error": "Forbidden", "message": "Origin not allowed"}), 403

def _build_cors_preflight_response():
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", request.headers.get('Origin', '*'))
    response.headers.add("Access-Control-Allow-Headers", "*")
    response.headers.add("Access-Control-Allow-Methods", "*")
    return response

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers.add("Access-Control-Allow-Origin", origin)
    return response

# --- HELPER FUNCTIONS ---
def get_soup(url, params=None):
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def extract_dramas_from_soup(soup):
    dramas = []
    # Mencari link drama (support struktur home, browse, dan search)
    links = soup.find_all('a', href=re.compile(r'/in/drama/'))
    seen_urls = set()
    
    for link in links:
        href = link.get('href')
        full_url = BASE_URL + href if href.startswith('/') else href
        
        if full_url in seen_urls: continue
        seen_urls.add(full_url)

        # Container pencarian elemen
        container = link
        
        # Cari Title
        title_tag = container.find(['h3', 'p', 'div', 'span'], class_=re.compile(r'title|name|text|ell', re.I))
        title = title_tag.get_text(strip=True) if title_tag else "No Title"
        
        # Cari Image
        img_tag = container.find('img')
        thumbnail = img_tag.get('src') if img_tag else None
        
        if title == "No Title" and img_tag and img_tag.get('alt'):
            title = img_tag.get('alt')

        # Filter hasil kosong
        if title == "No Title" and not thumbnail:
            continue

        dramas.append({
            "title": title,
            "url": full_url,
            "thumbnail": thumbnail
        })
    return dramas

# --- ROUTES ---

@app.route('/')
def index():
    return jsonify({
        "status": "Running",
        "endpoints": {
            "home": "/api/home",
            "search": "/api/search?q=keywords",
            "browse": "/api/browse?genre_id=0&page=1",
            "detail": "/api/drama?url=...",
            "episode": "/api/episode?url=..."
        }
    })

@app.route('/api/home')
def home():
    soup = get_soup(f"{BASE_URL}/in")
    if not soup: return jsonify({"error": "Failed"}), 500
    dramas = extract_dramas_from_soup(soup)
    return jsonify({"count": len(dramas), "data": dramas})

@app.route('/api/search')
def search():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Parameter 'q' wajib diisi"}), 400

    # Target: https://www.dramabox.com/in/search?searchValue=...
    # Pake params dict biar requests yg urus encoding spasi dll
    soup = get_soup(f"{BASE_URL}/in/search", params={"searchValue": query})
    
    if not soup: 
        return jsonify({"error": "Failed to fetch search results"}), 500

    results = extract_dramas_from_soup(soup)
    
    return jsonify({
        "query": query,
        "count": len(results),
        "data": results
    })

@app.route('/api/browse')
def browse():
    genre_id = request.args.get('genre_id', '0')
    page = request.args.get('page', '1')
    
    target_url = f"{BASE_URL}/in/browse/{genre_id}/{page}"
    soup = get_soup(target_url)
    if not soup: return jsonify({"error": "Failed"}), 500

    dramas = extract_dramas_from_soup(soup)
    
    # Extract Genres (buat menu filter)
    genres = []
    genre_links = soup.find_all('a', href=re.compile(r'/in/browse/\d+'))
    seen_ids = set()
    for link in genre_links:
        href = link.get('href')
        match = re.search(r'/browse/(\d+)', href)
        if match:
            gid = match.group(1)
            if gid in seen_ids: continue
            seen_ids.add(gid)
            gname = link.get_text(strip=True) or f"Genre {gid}"
            genres.append({"id": gid, "name": gname, "url": BASE_URL + href})
            
    if not genres: genres.append({"id": "0", "name": "All"})

    return jsonify({
        "page": int(page),
        "genre_id": genre_id,
        "genres": genres,
        "data": dramas
    })

@app.route('/api/drama')
def drama_detail():
    url = request.args.get('url')
    if not url: return jsonify({"error": "No URL"}), 400
    
    soup = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    title = soup.find("meta", property="og:title")
    desc = soup.find("meta", property="og:description")
    img = soup.find("meta", property="og:image")
    
    synopsis_div = soup.find('div', class_=re.compile(r'desc|intro', re.I))
    synopsis = synopsis_div.get_text(strip=True) if synopsis_div else (desc['content'] if desc else "-")

    episodes = []
    ep_links = soup.find_all('a', href=re.compile(r'/in/video/'))
    seen_eps = set()
    for link in ep_links:
        href = link.get('href')
        full_url = BASE_URL + href if href.startswith('/') else href
        if full_url in seen_eps: continue
        seen_eps.add(full_url)
        episodes.append({"name": link.get_text(strip=True) or f"Episode {len(episodes)+1}", "url": full_url})

    return jsonify({
        "title": title['content'] if title else "Unknown",
        "synopsis": synopsis,
        "poster": img['content'] if img else None,
        "episodes": episodes
    })

@app.route('/api/episode')
def episode_detail():
    url = request.args.get('url')
    if not url: return jsonify({"error": "No URL"}), 400

    soup = get_soup(url)
    if not soup: return jsonify({"error": "Failed"}), 500

    title = soup.find("title")
    desc = soup.find("meta", property="og:description")

    return jsonify({
        "title": title.get_text(strip=True) if title else "Unknown",
        "synopsis": desc['content'] if desc else "-",
        "page_url": url
    })

if __name__ == '__main__':
    app.run(debug=True, port=3000)
