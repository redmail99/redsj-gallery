# RedSJ — Cover Gallery

Album & single cover art gallery for **RedSJ**, with streaming platform links (Spotify, YouTube, Apple Music, Deezer, Tidal) and dynamically generated promo cards.

## Features

- Cover art gallery sourced from Spotify
- Streaming links for each release
- Promo cards with cosmic/geometric generated artwork (1200×630 landscape + 540×960 portrait)
- Lightbox viewer with visual effects
- Regenerate promo card styles on demand

## Usage

Serve locally:

```bash
PORT=8080 python3 server.py
```

The gallery auto-fetches covers from Spotify on startup. To update promos, hit `/regenerate` or click **New Style**.

## Tech

- Python `http.server` for serving
- Pillow for card generation
- Spotify artist page scraping for cover art

## Support

If you enjoy the music, consider supporting me on Ko-fi!

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/Y8Y7X0UZV)
