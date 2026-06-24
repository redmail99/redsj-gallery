#!/usr/bin/env python3
"""
RedSJ Gallery — fetches album/cover art from Spotify and serves a gallery.
"""
import base64
import json
import os
import re
import sys
import socket
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from html import escape

import requests

ARTIST_ID = "6hwevbERwOIUzk90IRHUnE"
ARTIST_NAME = "RedSJ"
BASE = Path(__file__).parent
PORT = int(os.getenv("PORT", "8081"))

SPOTIFY_URL = f"https://open.spotify.com/artist/{ARTIST_ID}"

# tracks individuales para incluir como "Single" si no aparecen en la discografía
EXTRA_TRACKS = [
    "3BN0C4XGC2Kq745p6f6Nxu",  # Echoes of infinity
]

CACHE_FILE = BASE / ".cover_cache.json"
LINKS_FILE = BASE / "streaming-links.json"


def _load_links() -> dict:
    """Load custom streaming links from streaming-links.json."""
    if LINKS_FILE.exists():
        return json.loads(LINKS_FILE.read_text())
    return {}


def _track_search_url(platform: str, track_name: str) -> str:
    """Generate a search-based fallback URL for a platform."""
    q = track_name.replace(" ", "+")
    q_raw = track_name.replace(" ", "%20")
    urls = {
        "youtube": f"https://music.youtube.com/search?q=RedSJ+{q}",
        "applemusic": f"https://music.apple.com/us/search?term=RedSJ+{q}",
        "deezer": f"https://www.deezer.com/search/RedSJ%20{q_raw}",
        "tidal": f"https://tidal.com/search?q=RedSJ+{q}",
    }
    return urls.get(platform, "")


def merge_links(cover: dict, links_map: dict) -> dict:
    """Merge streaming links into a cover dict. Spotify auto-generated, rest from map or search."""
    cover["links"] = {
        "spotify": cover.get("spotify_url", ""),
        "youtube": "",
        "applemusic": "",
        "deezer": "",
        "tidal": "",
    }
    name = cover["name"]
    if name in links_map:
        for k in ("youtube", "applemusic", "deezer", "tidal"):
            if links_map[name].get(k):
                cover["links"][k] = links_map[name][k]
    # Fallback: search URL for any missing
    for plat in ("youtube", "applemusic", "deezer", "tidal"):
        if not cover["links"][plat]:
            cover["links"][plat] = _track_search_url(plat, name)
    return cover


def fetch_covers() -> list[dict]:
    """Fetch album/single cover art from Spotify artist page."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.6422.165 Mobile Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = requests.get(SPOTIFY_URL, headers=headers, timeout=30)
    r.raise_for_status()

    # extract initialState JSON (base64 encoded in <script id="initialState">)
    m = re.search(
        r'<script id="initialState" type="text/plain">([^<]+)</script>',
        r.text,
    )
    if not m:
        raise RuntimeError("Could not find initialState in Spotify page")

    raw = m.group(1).strip()
    decoded = base64.b64decode(raw).decode()
    data = json.loads(decoded)

    items = data.get("entities", {}).get("items", {})
    artist_key = f"spotify:artist:{ARTIST_ID}"
    artist = items.get(artist_key, {})
    discography = artist.get("discography", {})

    covers: list[dict] = []

    # popular releases (albums)
    popular = discography.get("popularReleasesAlbums", {}).get("items", [])
    for item in popular:
        cover = item.get("coverArt", {})
        sources = cover.get("sources", [])
        url = _best_cover(sources)
        name = item.get("name", "Unknown")
        year = str(item.get("date", {}).get("year", ""))
        typ_raw = (item.get("type") or "").strip()
        typ = {"SINGLE": "Single", "ALBUM": "Album", "EP": "EP", "COMPILATION": "Compilation"}.get(typ_raw, typ_raw.capitalize() if typ_raw else "Album")
        spotify_url = f"https://open.spotify.com/album/{item['uri'].split(':')[-1]}" if item.get("uri") else ""
        if url:
            covers.append({"name": name, "url": url, "year": year, "type": typ, "spotify_url": spotify_url})

    # singles / eps
    singles_data = discography.get("singles", {}).get("items", [])
    if not singles_data:
        # sometimes the data is nested differently
        singles_data = discography.get("singles", {}).get("items", [])

    # from the page we saw, singles are in discography.singles.items[].releases.items[]
    for group in singles_data:
        releases = group.get("releases", {}).get("items", [])
        for item in releases:
            cover = item.get("coverArt", {})
            sources = cover.get("sources", [])
            url = _best_cover(sources)
            name = item.get("name", "Unknown")
            year = str(item.get("date", {}).get("year", ""))
            typ_raw = (item.get("type") or "").strip()
            typ = {"SINGLE": "Single", "ALBUM": "Album", "EP": "EP", "COMPILATION": "Compilation"}.get(typ_raw, typ_raw.capitalize() if typ_raw else "Single")
            spotify_url = f"https://open.spotify.com/album/{item['uri'].split(':')[-1]}" if item.get("uri") else ""
            if url and name not in {c["name"] for c in covers}:
                covers.append({"name": name, "url": url, "year": year, "type": typ, "spotify_url": spotify_url})

    if not covers:
        raise RuntimeError("No covers extracted from Spotify page")

    # extra tracks
    seen = {c["name"] for c in covers}
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/125.0 Mobile Safari/537.36",
    }
    for tid in EXTRA_TRACKS:
        try:
            tr = requests.get(f"https://open.spotify.com/track/{tid}", headers=headers, timeout=15)
            tr.raise_for_status()
            # extract initialState from track page
            m2 = re.search(r'<script id="initialState" type="text/plain">([^<]+)</script>', tr.text)
            if m2:
                raw2 = base64.b64decode(m2.group(1).strip()).decode()
                tdata = json.loads(raw2)
                entity_key = [k for k in tdata.get("entities", {}).get("items", {}) if k.startswith("spotify:track:")]
                if entity_key:
                    track_entity = tdata["entities"]["items"][entity_key[0]]
                    album = track_entity.get("albumOfTrack", {})
                    name = track_entity.get("name", "Unknown")
                    cover_sources = album.get("coverArt", {}).get("sources", [])
                    url = _best_cover(cover_sources)
                    year = str(album.get("date", {}).get("year", ""))
                    album_uri = album.get("uri", "")
                    spotify_url = f"https://open.spotify.com/album/{album_uri.split(':')[-1]}" if album_uri else ""
                    if url and name not in seen:
                        covers.append({"name": name, "url": url, "year": year, "type": "Track", "spotify_url": spotify_url})
                        seen.add(name)
        except Exception as e:
            print(f"  ⚠ track {tid}: {e}", file=sys.stderr)

    covers.sort(key=lambda c: (c.get("year", "0"), c.get("type", ""), c.get("name", "")))
    return covers


def _best_cover(sources: list[dict]) -> str | None:
    """Get the largest available cover image URL."""
    best = None
    best_w = 0
    for src in sources:
        w = src.get("width", 0)
        url = src.get("url", "")
        if w > best_w and url:
            best_w = w
            best = url
    return best


def _render_links(c: dict) -> str:
    links = c.get("links", {})
    platforms = [
        ("spotify", "Spotify", "#1DB954"),
        ("youtube", "YouTube", "#FF0000"),
        ("applemusic", "Apple", "#FA243A"),
        ("deezer", "Deezer", "#A238FF"),
        ("tidal", "Tidal", "#FFFFFF"),
    ]
    html = ""
    for key, label, color in platforms:
        url = links.get(key, "")
        if url:
            style = f"color:{color};border-color:{color}44;"
            html += f'<a href="{escape(url)}" target="_blank" class="stream-link" style="{style}" title="{label}">{label}</a>\n    '
    return html


def build_html(covers: list[dict]) -> str:
    """Generate a static gallery HTML page with integrated promo cards."""
    cards_html = ""
    for c in covers:
        links_html = _render_links(c)
        cards_html += f"""\
    <div class="card">
      <img class="card-img" src="{escape(c['url'])}" alt="{escape(c['name'])}" loading="lazy">
      <div class="card-body">
        <div class="card-info">
          <strong>{escape(c['name'])}</strong>
          <span class="meta">{escape(c['type'])} · {escape(c['year'])}</span>
        </div>
        <div class="card-links">
          {links_html}
        </div>
      </div>
    </div>
"""
    template = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__ARTIST_NAME__ — Cover Gallery</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #121212; color: #e8e6e3; min-height: 100vh;
  }
  header {
    text-align: center; padding: 3rem 1rem 2rem;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    border-bottom: 1px solid rgba(255,255,255,.06);
  }
  header h1 { font-size: 1.8rem; font-weight: 600; }
  header h1 span { color: #1DB954; }
  header p { margin-top: .5rem; color: #999; font-size: .95rem; }
  .badge {
    display: inline-block; margin-top: .8rem; padding: .3rem 1rem;
    border-radius: 20px; background: rgba(255,255,255,.06);
    font-size: .8rem; color: #aaa;
  }
  .section {
    max-width: 1200px; margin: 0 auto; padding: 2rem 1rem;
  }
  .section-title {
    font-size: 1.3rem; font-weight: 600; margin-bottom: 1.5rem;
    display: flex; align-items: center; gap: .6rem;
  }
  .section-title .bar {
    flex: 1; height: 1px; background: rgba(255,255,255,.08);
  }
  .grid {
    display: flex; flex-direction: column; gap: .6rem;
  }
  .card {
    display: flex; gap: 1rem;
    background: #18181d; border-radius: 12px;
    overflow: hidden; border: 1px solid rgba(255,255,255,.06);
    transition: transform .2s, box-shadow .2s;
    padding: .75rem;
  }
  .card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,.5);
  }
  .card-img {
    width: 80px; height: 80px; min-width: 80px;
    border-radius: 8px; object-fit: cover; background: #222;
    cursor: pointer;
  }
  .card-body {
    display: flex; align-items: center; justify-content: space-between;
    flex: 1; min-width: 0;
  }
  .card-info { min-width: 0; }
  .card-info strong {
    color: #e8e6e3; display: block; font-size: .95rem;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .card-info .meta { font-size: .75rem; color: #888; }
  .card-links {
    display: flex; gap: .35rem; flex-shrink: 0; align-items: center;
  }
  .stream-link {
    display: inline-block; padding: .2rem .55rem;
    border-radius: 14px; border: 1px solid;
    font-size: .65rem; font-weight: 600; text-decoration: none;
    transition: all .2s; white-space: nowrap;
    letter-spacing: .3px;
  }
  .stream-link:hover {
    transform: scale(1.05);
    filter: brightness(1.3);
  }

  .grid-promo {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 1.2rem;
  }
  .promo-card {
    background: #18181d; border-radius: 12px; overflow: hidden;
    border: 1px solid rgba(255,255,255,.06);
    transition: transform .2s, box-shadow .2s;
  }
  .promo-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 32px rgba(0,0,0,.5);
  }
  .promo-img {
    width: 100%; display: block; aspect-ratio: 1200 / 630;
    object-fit: cover; background: #222;
  }
  .promo-body { padding: .8rem 1rem; display: flex; justify-content: space-between; align-items: center; }
  .promo-body strong { color: #e8e6e3; font-size: .95rem; display: block; }
  .promo-body .meta { font-size: .8rem; }

  .grid-portrait {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 1.2rem;
  }
  .portrait-card {
    background: #18181d; border-radius: 12px; overflow: hidden;
    border: 1px solid rgba(255,255,255,.06);
    transition: transform .2s, box-shadow .2s;
  }
  .portrait-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 32px rgba(0,0,0,.5);
  }
  .portrait-img {
    width: 100%; display: block; aspect-ratio: 540 / 960;
    object-fit: cover; background: #222;
  }
  .portrait-body { padding: .8rem 1rem; display: flex; justify-content: space-between; align-items: center; }
  .portrait-body strong { color: #e8e6e3; font-size: .9rem; display: block; }
  .portrait-body .meta { font-size: .75rem; }

  .btn-regen {
    padding: .6rem 1.5rem; border: none;
    border-radius: 30px;
    background: linear-gradient(135deg, #7c3aed, #c084fc);
    color: #fff; font-size: .85rem; font-weight: 600; cursor: pointer;
    transition: opacity .2s, transform .1s;
  }
  .btn-regen:hover { opacity: .85; }
  .btn-regen:active { transform: scale(.96); }
  .btn-regen:disabled { opacity: .4; cursor: not-allowed; transform: none; }
  .btn-regen .spinner {
    display: none; width: 14px; height: 14px;
    border: 2px solid rgba(255,255,255,.3); border-top-color: #fff;
    border-radius: 50%; animation: spin .6s linear infinite;
    vertical-align: middle; margin-right: .4rem;
  }
  .btn-regen.loading .spinner { display: inline-block; }
  .btn-regen.loading .label-text { display: none; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #promo-msg { margin-top: .6rem; font-size: .8rem; color: #888; min-height: 1.2em; }

  @media (max-width: 720px) {
    header h1 { font-size: 1.3rem; }
    .card-links { flex-wrap: wrap; gap: .25rem; }
    .stream-link { font-size: .6rem; padding: .15rem .45rem; }
    .grid-promo { grid-template-columns: 1fr; }
    .grid-portrait { grid-template-columns: repeat(2, 1fr); gap: .8rem; }
  }
  @media (max-width: 480px) {
    .card-img { width: 60px; height: 60px; min-width: 60px; }
    .card-body { flex-direction: column; align-items: flex-start; gap: .4rem; }
    .card-links { flex-wrap: wrap; }
  }

  /* ── Shared Keyframes (used by lightbox effects) ── */
  @keyframes glareSweep {
    0% { transform: translateX(-100%) skewX(-18deg); }
    100% { transform: translateX(300%) skewX(-18deg); }
  }
  @keyframes lightBreathe {
    0%, 100% { opacity: .2; transform: scale(1); }
    50% { opacity: 1; transform: scale(2); }
  }
  @keyframes hueSpin {
    0% { filter: hue-rotate(0deg); }
    100% { filter: hue-rotate(360deg); }
  }
  @keyframes grainShift {
    0% { transform: translate(0, 0); }
    33% { transform: translate(-6px, 4px); }
    66% { transform: translate(4px, -6px); }
  }

  /* ── Lightbox ── */
  .lightbox {
    display: none; position: fixed; inset: 0; z-index: 10000;
    justify-content: center; align-items: center;
    cursor: zoom-out;
  }
  .lightbox.open { display: flex; }
  .lb-bg {
    position: absolute; inset: -60px;
    background-size: cover; background-position: center;
    filter: blur(50px) brightness(.5);
    transition: filter .5s; z-index: 0;
  }
  .lb-overlay {
    position: absolute; inset: 0; z-index: 1;
    background: rgba(0,0,0,.55);
  }
  .lb-overlay::after {
    content: ''; position: absolute; inset: 0; z-index: 1;
    pointer-events: none; opacity: 0;
  }
  .lb-img {
    max-width: 90vw; max-height: 90vh;
    border-radius: 16px; box-shadow: 0 20px 60px rgba(0,0,0,.7);
    object-fit: contain; cursor: default; z-index: 5;
    position: relative;
  }
  .lb-close {
    position: absolute; top: 20px; right: 24px; z-index: 10;
    color: rgba(255,255,255,.6); font-size: 2rem; cursor: pointer;
    transition: color .2s; line-height: 1;
  }
  .lb-close:hover { color: #fff; }

  /* ── Lightbox Effects (scoped to .lightbox) ── */
  .lightbox .effect-bar {
    position: absolute; bottom: 24px; left: 50%; transform: translateX(-50%);
    z-index: 10;
    display: flex; align-items: center; gap: 6px;
    background: rgba(0,0,0,.7); backdrop-filter: blur(14px);
    padding: 8px 18px; border-radius: 30px;
    border: 1px solid rgba(255,255,255,.08);
    font-size: .75rem; color: rgba(255,255,255,.5);
    user-select: none;
    transition: opacity .4s; opacity: .7;
  }
  .lightbox .effect-bar:hover { opacity: 1; }
  .lightbox .effect-bar .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: rgba(255,255,255,.12);
    transition: all .4s ease; cursor: pointer;
  }
  .lightbox .effect-bar .dot:hover { background: rgba(255,255,255,.3); }
  .lightbox .effect-bar .dot.active {
    background: #c084fc; box-shadow: 0 0 12px rgba(192,132,252,.6);
    transform: scale(1.35);
  }
  /* Glare */
  .lightbox.ef-glare::after {
    content: ''; position: absolute; inset: -100px; z-index: 6;
    pointer-events: none;
    background: linear-gradient(110deg,
      transparent 25%, rgba(255,255,255,.08) 38%,
      rgba(255,255,255,.18) 50%, rgba(255,255,255,.08) 62%, transparent 75%);
    animation: glareSweep 2.5s ease-in-out infinite;
    will-change: transform;
  }

  /* Blur progresivo */
  .lightbox.ef-blur .lb-bg {
    animation: blurCycle 2.5s ease-in-out infinite;
    will-change: filter;
  }
  .lightbox.ef-blur .lb-img {
    animation: blurImgCycle 2.5s ease-in-out infinite;
    will-change: filter;
  }
  @keyframes blurCycle {
    0%, 100% { filter: blur(50px) brightness(.5); }
    30% { filter: blur(30px) brightness(.6); }
    60% { filter: blur(15px) brightness(.7); }
  }
  @keyframes blurImgCycle {
    0%, 100% { filter: blur(0px); }
    30% { filter: blur(2px); }
    60% { filter: blur(5px); }
  }

  /* Light */
  .lightbox.ef-light::before {
    content: ''; position: absolute; inset: 0; z-index: 2;
    pointer-events: none;
    background: radial-gradient(circle at 50% 40%, rgba(255,200,80,.2) 0%, transparent 60%);
    animation: lightBreathe 2.5s ease-in-out infinite;
    will-change: transform, opacity;
  }

  /* Scale */
  .lightbox.ef-scale .lb-img {
    animation: scaleBreathe 2.5s ease-in-out infinite;
    will-change: transform;
  }
  @keyframes scaleBreathe {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.06); }
  }

  /* Hue */
  .lightbox.ef-hue .lb-img,
  .lightbox.ef-hue .lb-bg {
    animation: hueSpin 2.5s linear infinite;
    will-change: filter;
  }

  /* Float */
  .lightbox.ef-float .lb-img {
    animation: floatDrift 2.5s ease-in-out infinite;
    will-change: transform;
  }
  @keyframes floatDrift {
    0%, 100% { transform: translateY(0px); }
    50% { transform: translateY(-20px); }
  }

  /* Grain */
  .lightbox.ef-grain .lb-overlay::after {
    opacity: .08;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.7' numOctaves='5' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
    background-repeat: repeat; background-size: 256px 256px;
    animation: grainShift .4s steps(3) infinite;
  }

  /* Border */
  .lightbox.ef-border .lb-img {
    border-radius: 16px;
    animation: borderGlow 2.5s linear infinite;
    will-change: box-shadow;
  }
  @keyframes borderGlow {
    0%   { box-shadow: 0 0 30px rgba(255,0,100,.6), 0 20px 60px rgba(0,0,0,.7); }
    20%  { box-shadow: 0 0 30px rgba(255,80,0,.6), 0 20px 60px rgba(0,0,0,.7); }
    40%  { box-shadow: 0 0 30px rgba(200,255,0,.6), 0 20px 60px rgba(0,0,0,.7); }
    60%  { box-shadow: 0 0 30px rgba(0,255,150,.6), 0 20px 60px rgba(0,0,0,.7); }
    80%  { box-shadow: 0 0 30px rgba(50,100,255,.6), 0 20px 60px rgba(0,0,0,.7); }
    100% { box-shadow: 0 0 30px rgba(255,0,100,.6), 0 20px 60px rgba(0,0,0,.7); }
  }

  .promo-card, .portrait-card { cursor: pointer; }
  .card-img { cursor: pointer; }
</style>
</head>
<body>
<header>
  <h1>🎵 <span>__ARTIST_NAME__</span> — Cover Gallery</h1>
  <p>All album &amp; single cover art from Spotify</p>
  <div class="badge"><strong>__COVER_COUNT__ covers</strong> · <a href="https://open.spotify.com/artist/__ARTIST_ID__" target="_blank" style="color:#1DB954;text-decoration:none;">Open in Spotify →</a></div>
</header>

<div class="section">
  <div class="grid">
__CARDS__
  </div>
</div>

<div class="section">
  <div class="section-title">
    <span>✨ Promo Cards</span>
    <span class="bar"></span>
    <button class="btn-regen" id="btn-regen" onclick="regenerate()">
      <span class="spinner"></span>
      <span class="label-text">New Style</span>
    </button>
  </div>
  <div class="grid-promo" id="promo-grid"></div>
  <div id="promo-msg"></div>
</div>

<div class="section">
  <div class="section-title">
    <span>📱 Portrait Cards</span>
    <span class="bar"></span>
    <button class="btn-regen" id="btn-regen-portrait" onclick="regeneratePortrait()">
      <span class="spinner"></span>
      <span class="label-text">New Style</span>
    </button>
  </div>
  <div class="grid-portrait" id="portrait-grid"></div>
  <div id="portrait-msg"></div>
</div>

<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <div class="lb-bg" id="lb-bg"></div>
  <div class="lb-overlay"></div>
  <span class="lb-close" onclick="event.stopPropagation(); closeLightbox()">&times;</span>
  <img class="lb-img" id="lb-img" src="" alt="">

</div>

<script>
async function loadPortraitCards() {
  const msg = document.getElementById('portrait-msg');
  try {
    const res = await fetch('portrait-cards.json');
    const data = await res.json();
    renderPortrait(data.cards || []);
  } catch (e) {
    msg.textContent = 'Could not load portrait cards';
    document.getElementById('btn-regen-portrait').disabled = true;
  }
}

function renderPortrait(cards) {
  const grid = document.getElementById('portrait-grid');
  const ts = Date.now();
  grid.innerHTML = cards.map(c =>
    '<div class="portrait-card" onclick="openLightbox('portrait-output/' + c.file + '?t=' + ts + '\', \'' + c.name + '\')">' +
    '  <img class="portrait-img" src="portrait-output/' + c.file + '?t=' + ts + '" alt="' + c.name + '" loading="lazy">' +
    '  <div class="portrait-body">' +
    '    <div><strong>' + c.name + '</strong><span class="meta">' + (c.date || '') + '</span></div>' +
    '  </div>' +
    '</div>'
  ).join('');
}

async function regeneratePortrait() {
  const btn = document.getElementById('btn-regen-portrait');
  const msg = document.getElementById('portrait-msg');
  btn.disabled = true;
  btn.classList.add('loading');
  msg.textContent = 'Generating new portrait styles\u2026';

  try {
    const res = await fetch('regenerate-portraits', { method: 'POST' });
    const data = await res.json();
    if (data.success && data.count > 0) {
      renderPortrait(data.cards);
      msg.textContent = '\u2728 ' + data.count + ' portrait cards regenerated!';
    } else {
      msg.textContent = '\u274c Failed. Check logs.';
    }
  } catch (err) {
    msg.textContent = '\u274c Error: ' + err.message;
  }

  btn.disabled = false;
  btn.classList.remove('loading');
}

loadPortraitCards();

async function loadCards() {
  const msg = document.getElementById('promo-msg');
  try {
    const res = await fetch('cards.json');
    const data = await res.json();
    render(data.cards || []);
  } catch (e) {
    msg.textContent = 'Could not load promo cards';
    document.getElementById('btn-regen').disabled = true;
  }
}

function render(cards) {
  const grid = document.getElementById('promo-grid');
  const ts = Date.now();
  grid.innerHTML = cards.map(c =>
    '<div class="promo-card" onclick="openLightbox('output/' + c.file + '?t=' + ts + '\', \'' + c.name + '\')">' +
    '  <img class="promo-img" src="output/' + c.file + '?t=' + ts + '" alt="' + c.name + '" loading="lazy">' +
    '  <div class="promo-body">' +
    '    <div><strong>' + c.name + '</strong><span class="meta">' + (c.date || '') + '</span></div>' +
    '  </div>' +
    '</div>'
  ).join('');
}

async function regenerate() {
  const btn = document.getElementById('btn-regen');
  const msg = document.getElementById('promo-msg');
  btn.disabled = true;
  btn.classList.add('loading');
  msg.textContent = 'Generating new cosmic styles\u2026';

  try {
    const res = await fetch('regenerate', { method: 'POST' });
    const data = await res.json();
    if (data.success && data.count > 0) {
      render(data.cards);
      msg.textContent = '\u2728 ' + data.count + ' cards regenerated!';
    } else {
      msg.textContent = '\u274c Failed. Check logs.';
    }
  } catch (err) {
    msg.textContent = '\u274c Error: ' + err.message;
  }

  btn.disabled = false;
  btn.classList.remove('loading');
}

loadCards();

/* ── Lightbox with Effects ── */
let lbInterval = null;
let lbEffectIdx = 0;
const LB_EFFECTS = [
  { id: 'ef-glare', label: '✨ Glare' },
  { id: 'ef-blur', label: '🌀 Blur' },
  { id: 'ef-light', label: '💡 Light' },
  { id: 'ef-scale', label: '📐 Scale' },
  { id: 'ef-float', label: '🎈 Float' },
  { id: 'ef-border', label: '💎 Border' },
];

function openLightbox(src) {
  const lb = document.getElementById('lightbox');
  document.getElementById('lb-img').src = src;
  document.getElementById('lb-bg').style.backgroundImage = 'url(' + src + ')';
  lb.classList.add('open');
  document.body.style.overflow = 'hidden';
  startLbEffects(lb);
}

function closeLightbox() {
  if (lbInterval) { clearInterval(lbInterval); lbInterval = null; }
  const bar = document.querySelector('.lightbox .effect-bar');
  if (bar) bar.remove();
  const lb = document.getElementById('lightbox');
  lb.className = 'lightbox';
  document.body.style.overflow = '';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeLightbox();
});

function startLbEffects(lb) {
  lbEffectIdx = 0;
  const bar = document.createElement('div');
  bar.className = 'effect-bar';
  LB_EFFECTS.forEach((ef, i) => {
    const dot = document.createElement('span');
    dot.className = 'dot' + (i === 0 ? ' active' : '');
    dot.dataset.idx = i;
    dot.title = ef.label;
    dot.addEventListener('click', () => { lbEffectIdx = i; applyLb(); });
    bar.appendChild(dot);
  });
  lb.appendChild(bar);

  function applyLb() {
    lb.className = lb.className.split(' ').filter(c => !c.startsWith('ef-')).join(' ');
    lb.classList.add(LB_EFFECTS[lbEffectIdx].id);
    bar.querySelectorAll('.dot').forEach((d, i) => d.classList.toggle('active', i === lbEffectIdx));
  }

  applyLb();
  lbInterval = setInterval(() => {
    lbEffectIdx = (lbEffectIdx + 1) % LB_EFFECTS.length;
    applyLb();
  }, 2500);
}

/* ── Cover card clicks (delegation) ── */
document.querySelector('.grid').addEventListener('click', e => {
  const img = e.target.closest('.card-img');
  if (!img) return;
  const nameEl = img.closest('.card').querySelector('strong');
  openLightbox(img.src, nameEl ? nameEl.textContent : '');
});
</script>
</body>
</html>"""
    return (template
        .replace("__ARTIST_NAME__", ARTIST_NAME)
        .replace("__ARTIST_ID__", ARTIST_ID)
        .replace("__COVER_COUNT__", str(len(covers)))
        .replace("__CARDS__", cards_html)
    )


def main():
    print(f"🎵 Fetching covers for {ARTIST_NAME}...")
    covers = fetch_covers()
    print(f"  → {len(covers)} covers found")
    links_map = _load_links()
    covers = [merge_links(c, links_map) for c in covers]
    html = build_html(covers)
    (BASE / "index.html").write_text(html)
    print(f"  → index.html written")
    return covers


if __name__ == "__main__":
    main()
