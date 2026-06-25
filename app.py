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
PORT = int(os.getenv("PORT", "8080"))

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

  .footer {
    max-width: 1200px; margin: 0 auto; padding: 2rem 1rem 3rem;
    text-align: center;
  }
  .footer .socials {
    display: flex; justify-content: center; gap: 1rem; flex-wrap: wrap;
    margin-bottom: 1.2rem;
  }
  .footer .socials a {
    display: inline-flex; align-items: center; gap: .4rem;
    padding: .5rem .9rem; border-radius: 30px;
    background: rgba(255,255,255,.05);
    border: 1px solid rgba(255,255,255,.08);
    color: #bbb; font-size: .8rem; text-decoration: none;
    transition: all .2s;
  }
  .footer .socials a:hover {
    background: rgba(255,255,255,.1); color: #fff;
    transform: translateY(-2px);
  }
  .footer .socials a svg { width: 18px; height: 18px; fill: currentColor; }
  .footer .ko-fi {
    margin-top: 1rem;
  }
  .footer .ko-fi a {
    display: inline-block;
  }
  .footer .ko-fi img {
    height: 42px;
  }
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

<div class="footer">
  <div class="socials">
    <a href="https://x.com/redmail99" target="_blank" title="X / Twitter">
      <svg viewBox="0 0 24 24"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
      <span>X</span>
    </a>
    <a href="https://www.facebook.com/red.smith.739978" target="_blank" title="Facebook">
      <svg viewBox="0 0 24 24"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
      <span>Facebook</span>
    </a>
    <a href="https://bsky.app/profile/redmail99.bsky.social" target="_blank" title="Bluesky">
      <svg viewBox="0 0 24 24"><path d="M12 10.8c-1.087-2.114-4.046-6.053-6.798-7.995C2.566 1.88 0 2.46 0 5.414c0 .585.357 4.915.668 5.617.857 1.937 3.982 2.553 6.758 2.24-4.023.485-7.214 1.984-4.854 5.908 2.576 4.28 5.282 1.932 7.428-1.676 2.146 3.608 4.852 5.956 7.428 1.676 2.36-3.924-.83-5.423-4.854-5.908 2.776.313 5.901-.303 6.758-2.24.311-.702.668-5.032.668-5.617 0-2.954-2.566-3.534-5.202-1.618C16.044 4.747 13.087 8.686 12 10.8Z"/></svg>
      <span>Bluesky</span>
    </a>
    <a href="https://www.threads.com/@redmail99" target="_blank" title="Threads">
      <svg viewBox="0 0 24 24"><path d="M14.065 5.454c-.987-.837-2.262-1.254-3.725-1.254-1.543 0-2.838.464-3.776 1.35-.904.854-1.364 2.023-1.364 3.477 0 1.395.436 2.525 1.22 3.373.74.8 1.827 1.303 3.17 1.472.206.026.413.04.618.04 1.48 0 2.624-.443 3.577-1.136.082-.06.166-.117.25-.17-.057-.164-.113-.33-.168-.5-.588.443-1.25.744-2.046.796-1.222.08-2.205-.342-2.832-.896.37.044.738.067 1.097.067 1.087 0 2.12-.2 3.02-.582 2.372-1.005 3.76-2.924 3.76-5.197 0-1.263-.461-2.32-1.32-3.086-.808-.72-1.925-1.094-3.23-1.094Zm-.34 4.877c-.662.33-1.47.514-2.422.514-.292 0-.59-.02-.893-.062-1.155-.16-2.058-.713-2.625-1.572-.362-.548-.558-1.215-.558-1.921 0-.67.174-1.273.5-1.744.34-.492.83-.842 1.412-1.006.272-.078.56-.117.855-.117 1.024 0 1.88.32 2.515.95.612.606.939 1.46.939 2.482 0 1.043-.529 1.909-1.723 2.476ZM17.43 16.26c.436-.113.837-.285 1.194-.507l-.001.003c-.39-.532-.817-.837-1.278-.912-.462-.075-1.028.033-1.698.323-.67.29-1.274.648-1.81 1.072-.537.424-.93.847-1.178 1.27-.249.423-.374.804-.374 1.143 0 .338.092.65.276.934.184.283.445.518.784.705.339.187.74.322 1.203.406.463.084.97.111 1.523.082.553-.03 1.128-.14 1.725-.331.597-.192 1.14-.445 1.63-.76v-2.386c-.544.547-1.15.981-1.817 1.3-.667.32-1.273.48-1.817.48-.383 0-.66-.082-.83-.246-.17-.164-.255-.38-.255-.648 0-.282.117-.593.35-.934.233-.34.582-.686 1.047-1.036.465-.35 1.03-.68 1.694-.99.665-.31 1.358-.55 2.08-.72.722-.17 1.418-.256 2.088-.256 1.127 0 2.014.192 2.66.578.647.386 1.002.93 1.065 1.632.064.703-.086 1.5-.448 2.392-.363.892-.868 1.661-1.515 2.307-.647.646-1.39 1.147-2.227 1.504-.838.358-1.703.595-2.597.712-.894.117-1.757.107-2.59-.032-.832-.14-1.564-.403-2.195-.79-.63-.387-1.138-.88-1.524-1.48-.386-.6-.597-1.287-.633-2.064l.002-.002c-.016-.186-.024-.374-.024-.564 0-1.143.277-2.176.83-3.098.554-.922 1.31-1.692 2.27-2.31.96-.617 2.025-1.068 3.195-1.353 1.17-.285 2.33-.402 3.48-.35.657.03 1.272.128 1.844.294v-2.14c-.554-.2-1.16-.342-1.818-.424-.658-.082-1.343-.11-2.056-.084-.712.027-1.43.116-2.152.27-.722.153-1.408.37-2.058.652-.65.282-1.232.617-1.746 1.006-.514.39-.93.81-1.248 1.26-.318.45-.543.903-.676 1.357-.133.454-.2.88-.2 1.277 0 .32.04.615.123.886l-.002.001c.573-.585 1.249-1.06 2.027-1.425.778-.366 1.577-.608 2.398-.726.82-.118 1.597-.13 2.33-.035.733.095 1.348.305 1.846.63.455.297.82.658 1.093 1.083v.002c.215.337.374.706.478 1.108v.001c.103.402.155.82.155 1.254 0 .702-.126 1.358-.378 1.97-.252.61-.603 1.157-1.053 1.64-.45.484-.983.89-1.6 1.22-.617.33-1.278.556-1.984.678Z"/></svg>
      <span>Threads</span>
    </a>
    <a href="https://www.instagram.com/redmail99/" target="_blank" title="Instagram">
      <svg viewBox="0 0 24 24"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069ZM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0Zm0 5.838a6.162 6.162 0 1 0 0 12.324 6.162 6.162 0 0 0 0-12.324ZM12 16a4 4 0 1 1 0-8 4 4 0 0 1 0 8Zm6.406-11.845a1.44 1.44 0 1 0 0 2.881 1.44 1.44 0 0 0 0-2.881Z"/></svg>
      <span>Instagram</span>
    </a>
    <a href="https://www.tiktok.com/@redsj99" target="_blank" title="TikTok">
      <svg viewBox="0 0 24 24"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg>
      <span>TikTok</span>
    </a>
  </div>
  <div class="ko-fi">
    <a href="https://ko-fi.com/Y8Y7X0UZV" target="_blank">
      <img src="https://storage.ko-fi.com/cdn/kofi2.png" alt="Support on Ko-fi">
    </a>
  </div>
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
    '<div class="portrait-card" onclick="openLightbox(\'portrait-output/' + c.file + '?t=' + ts + '\', \'' + c.name + '\')">' +
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
    '<div class="promo-card" onclick="openLightbox(\'output/' + c.file + '?t=' + ts + '\', \'' + c.name + '\')">' +
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

/* ── Hide regen buttons on GitHub Pages ── */
if (location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
  document.getElementById('btn-regen').style.display = 'none';
  document.getElementById('btn-regen-portrait').style.display = 'none';
}

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
