"""Render a Folium HTML map to a PNG screenshot, for committing as
documentation evidence (not part of the pipeline or its tests).

Why this exists: in this project's development/CI sandbox, headless
Chromium's own networking cannot reach external CDNs directly (the same
class of issue documented in ADR-0002 for Overpass, but this time it's the
browser's network stack, not Python's — even pointing Chromium explicitly
at the working HTTPS_PROXY does not fix it). `curl`, run from Python, *can*
reach those hosts reliably. This script routes every non-file:// request
Chromium makes through an on-demand `curl` fetch (cached to disk), so the
page's CDN-hosted JS/CSS/font/tile dependencies resolve without Chromium
needing direct network access at all.

On a normal internet-connected machine this workaround is unnecessary —
Folium HTML files are self-contained web pages and will render in any
browser as-is. This script is only needed for reproducing this project's
own documentation screenshots inside this specific sandboxed environment.

Usage
-----
    python experiments/render_map_screenshot.py <input.html> <output.png>
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

CHROMIUM_PATH = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
CACHE_DIR = Path("/tmp/dlm_map_screenshot_cache")
USER_AGENT = "dlm-dublin-routing/0.1 (UCD ACM40960 coursework)"

EXT_CONTENT_TYPE = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".eot": "application/vnd.ms-fontobject",
    ".svg": "image/svg+xml",
}


def render(html_path: Path, png_path: Path, width: int = 1200, height: int = 800) -> None:
    from playwright.sync_api import sync_playwright

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def handle_route(route, request):  # noqa: ANN001, ANN202 - playwright callback signature
        url = request.url
        if url.startswith("file://"):
            route.continue_()
            return
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        ext = "".join(Path(url.split("?")[0]).suffixes) or ".png"
        cache_path = CACHE_DIR / f"{key}{ext}"
        if not cache_path.exists():
            result = subprocess.run(  # noqa: S603 - fixed args, trusted local tool
                ["curl", "-sSL", "--max-time", "6", "-A", USER_AGENT, "-o", str(cache_path), url],
                capture_output=True,
                timeout=8,
                check=False,
            )
            if result.returncode != 0 or not cache_path.exists() or cache_path.stat().st_size == 0:
                route.abort()
                return
        route.fulfill(
            status=200,
            content_type=EXT_CONTENT_TYPE.get(cache_path.suffix, "image/png"),
            body=cache_path.read_bytes(),
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM_PATH, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": height})
        page.route("**/*", handle_route)
        page.goto(html_path.resolve().as_uri(), timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        page.screenshot(path=str(png_path))
        browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    render(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"Saved {sys.argv[2]}")
