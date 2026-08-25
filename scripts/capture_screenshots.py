r"""Capture the projector overlay screenshots used in the README.

Renders the real overlay against a running VerseSync server using
Chromium via Playwright -- the same engine an OBS Browser Source uses --
so what lands in `docs/images/` is genuinely what OBS would composite,
not a mock-up.

Prerequisites:

    pip install playwright
    python -m playwright install chromium

Then, with a server running and Bibles ingested:

    python scripts/capture_screenshots.py

Options:

    --base-url   VerseSync server (default http://127.0.0.1:8000)
    --token      Operator device token, if auth is enabled
    --out        Output directory (default docs/images)
    --width/--height  Canvas size (default 1920x1080)

Each shot is composited over a still frame that stands in for a camera,
because a transparent overlay screenshotted on its own just looks like
floating text and tells a reader nothing about how it will sit over their
service. The stand-in is drawn here in code -- no stock photography, no
implied endorsement, nothing to license.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

# Adds backend/ to sys.path and forces UTF-8 console output so
# Yoruba scripture can be printed on a default Windows console.
from _bootstrap import ROOT

# Each entry: (filename, query string, verse to display, caption)
SHOTS = [
    (
        "overlay-lowerthird.png",
        "theme=lowerthird&hold=0",
        {"book": "ROM", "chapter": 8, "verse_start": 28, "verse_end": 30,
         "translation": "KJV"},
        "lowerthird over a camera",
    ),
    (
        "overlay-caption.png",
        "theme=caption&hold=0",
        {"book": "PSA", "chapter": 23, "verse_start": 1, "translation": "KJV"},
        "caption strip",
    ),
    (
        "overlay-fullscreen.png",
        "theme=fullscreen&bg=dark&hold=0",
        {"book": "JHN", "chapter": 3, "verse_start": 16, "translation": "KJV"},
        "fullscreen, no camera behind it",
    ),
    (
        "overlay-yoruba.png",
        "theme=lowerthird&hold=0",
        {"book": "JHN", "chapter": 3, "verse_start": 16, "translation": "YOR"},
        "Yoruba, diacritics intact",
    ),
]

# A neutral stage-lit gradient with a soft key light, standing in for a
# camera feed. Deliberately abstract: the point is to show how the
# overlay reads over moving footage, not to fake a real service.
BACKDROP_JS = r"""
([canvasW, canvasH]) => {
  const c = document.createElement('canvas');
  c.width = canvasW; c.height = canvasH;
  const x = c.getContext('2d');

  // Warm stage wash. Kept mid-tone rather than near-black on purpose:
  // against a very dark backdrop the overlay panel's own dark fill has
  // no visible edge, and the screenshot reads as though the card is
  // clipped at the frame bottom when it is not.
  const bg = x.createLinearGradient(0, 0, 0, canvasH);
  bg.addColorStop(0,    '#243447');
  bg.addColorStop(0.45, '#33465c');
  bg.addColorStop(1,    '#4a5f78');
  x.fillStyle = bg; x.fillRect(0, 0, canvasW, canvasH);

  const key = x.createRadialGradient(
    canvasW * 0.60, canvasH * 0.30, 0,
    canvasW * 0.60, canvasH * 0.30, canvasW * 0.55);
  key.addColorStop(0,   'rgba(255, 216, 168, 0.42)');
  key.addColorStop(0.5, 'rgba(255, 196, 140, 0.16)');
  key.addColorStop(1,   'rgba(0, 0, 0, 0)');
  x.fillStyle = key; x.fillRect(0, 0, canvasW, canvasH);

  // Suggestion of a figure at a lectern, low contrast so it never
  // competes with the scripture.
  x.fillStyle = 'rgba(20, 28, 40, 0.42)';
  x.beginPath();
  x.ellipse(canvasW * 0.60, canvasH * 0.34, canvasW * 0.045,
            canvasH * 0.105, 0, 0, Math.PI * 2);
  x.fill();
  x.beginPath();
  x.moveTo(canvasW * 0.60, canvasH * 0.44);
  x.bezierCurveTo(canvasW * 0.51, canvasH * 0.52,
                  canvasW * 0.50, canvasH * 0.74,
                  canvasW * 0.505, canvasH * 0.86);
  x.lineTo(canvasW * 0.695, canvasH * 0.86);
  x.bezierCurveTo(canvasW * 0.70, canvasH * 0.74,
                  canvasW * 0.69, canvasH * 0.52,
                  canvasW * 0.60, canvasH * 0.44);
  x.fill();

  // Soft vignette, so the eye settles where the overlay sits.
  const vig = x.createRadialGradient(
    canvasW * 0.5, canvasH * 0.45, canvasH * 0.25,
    canvasW * 0.5, canvasH * 0.45, canvasW * 0.75);
  vig.addColorStop(0, 'rgba(0,0,0,0)');
  vig.addColorStop(1, 'rgba(0,0,0,0.45)');
  x.fillStyle = vig; x.fillRect(0, 0, canvasW, canvasH);

  return c.toDataURL('image/png');
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="",
                        help="Operator device token, if auth is enabled")
    parser.add_argument("--out", default=str(ROOT / "docs" / "images"))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERR] playwright is not installed.\n"
              "      pip install playwright\n"
              "      python -m playwright install chromium",
              file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    api = httpx.Client(base_url=args.base_url, timeout=30.0)

    try:
        api.get("/healthz").raise_for_status()
    except Exception as exc:
        print(f"[ERR] No VerseSync server at {args.base_url}: {exc}\n"
              f"      Start one with: uvicorn app.main:app --port 8000",
              file=sys.stderr)
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=1,
        )
        backdrop = page.evaluate(BACKDROP_JS, [args.width, args.height])

        for filename, query, verse, caption in SHOTS:
            response = api.post("/projector/show", headers=headers, json=verse)
            if response.status_code == 401:
                print("[ERR] Server requires authentication. Pass "
                      "--token <operator device token>.", file=sys.stderr)
                browser.close()
                return 1
            if response.status_code != 200:
                print(f"[ERR] {filename}: /projector/show returned "
                      f"{response.status_code}: {response.text[:200]}",
                      file=sys.stderr)
                continue

            page.goto(f"{args.base_url}/projector?{query}",
                      wait_until="networkidle")
            # The page fetches its verse over the WebSocket on connect, so
            # wait for the text to actually be in the DOM rather than
            # sleeping and hoping.
            page.wait_for_function(
                "document.querySelectorAll('#verses li').length > 0",
                timeout=10_000)
            page.wait_for_function(
                "getComputedStyle(document.getElementById('card')).opacity==='1'",
                timeout=10_000)

            # Composite over the stand-in camera frame unless the theme
            # already paints its own opaque background.
            if "bg=dark" not in query:
                page.evaluate(
                    """(src) => {
                        document.body.style.backgroundImage = `url(${src})`;
                        document.body.style.backgroundSize = 'cover';
                    }""", backdrop)

            target = out_dir / filename
            page.screenshot(path=str(target))
            size_kb = target.stat().st_size // 1024
            print(f"[OK] {filename:28s} {size_kb:4d} KB  {caption}")

        # A transparency proof: the same overlay with no backdrop at all,
        # so the checkerboard shows the alpha channel is real.
        page.goto(f"{args.base_url}/projector?theme=lowerthird&hold=0",
                  wait_until="networkidle")
        page.wait_for_function(
            "document.querySelectorAll('#verses li').length > 0", timeout=10_000)
        target = out_dir / "overlay-transparent.png"
        page.screenshot(path=str(target), omit_background=True)
        print(f"[OK] {'overlay-transparent.png':28s} "
              f"{target.stat().st_size // 1024:4d} KB  true alpha, no backdrop")

        browser.close()

    api.post("/projector/clear", headers=headers)
    print(f"\nWrote {len(SHOTS) + 1} images to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
