"""Regenerates assets/brand/watermark.png -- the wordmark burned into free-tier
videos.

This is a DEV-TIME tool, not part of the request path. The PNG it produces is
committed, so the server never needs a font, a rasteriser, or Chrome to apply a
watermark -- it only needs ffmpeg's `overlay` filter, which every build has.
(This machine's ffmpeg is built without libfreetype, so `drawtext` is not
available; even where it is, depending on it would make the watermark differ
between dev and the container. A committed PNG is identical everywhere.)

Run it when the product name changes:

    python3 tools/make_watermark.py            # uses BRAND_NAME below
    python3 tools/make_watermark.py "NEWNAME"

Chrome is only used as a rasteriser. It's found in the hyperframes cache, or
pass one via CHROME env var. The font is vendored at assets/brand/bebas-neue.woff2
so regeneration doesn't depend on that cache still being populated.
"""
import base64
import glob
import os
import subprocess
import sys
import tempfile

BRAND_NAME = "ROTATION"

# Lives under server/static/ rather than assets/ -- assets/ is gitignored (it's
# populated at render time by visuals.install_vendor), and the wordmark has to
# ship with the repo or a deployed container has nothing to watermark with.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND_DIR = os.path.join(ROOT, "server", "static", "brand")
FONT_PATH = os.path.join(BRAND_DIR, "bebas-neue.woff2")
OUT_PATH = os.path.join(BRAND_DIR, "watermark.png")

# Sized for a 1080x1920 frame at 1:1 -- compose.watermark_video overlays it
# without scaling, so these are literal output pixels. The canvas is padded
# well beyond the glyphs to leave room for the shadow blur.
CANVAS_W, CANVAS_H = 560, 150
FONT_SIZE = 68


def _find_chrome() -> str:
    env = os.environ.get("CHROME")
    if env and os.path.exists(env):
        return env
    patterns = [
        os.path.expanduser("~/.cache/hyperframes/chrome/**/chrome-headless-shell"),
        os.path.expanduser("~/.cache/puppeteer/**/chrome-headless-shell"),
        os.path.expanduser("~/.cache/puppeteer/**/Google Chrome for Testing"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[-1]
    raise SystemExit(
        "No Chrome found to rasterise with. Set CHROME=/path/to/chrome, or run\n"
        "`npx hyperframes@0.7.70 browser install` to populate the cache."
    )


def _html(name: str) -> str:
    font_b64 = base64.b64encode(open(FONT_PATH, "rb").read()).decode()
    return f"""<!doctype html>
<meta charset="utf-8">
<style>
  @font-face {{
    font-family: "Brand";
    src: url(data:font/woff2;base64,{font_b64}) format("woff2");
  }}
  html, body {{ margin: 0; background: transparent; }}
  body {{
    width: {CANVAS_W}px; height: {CANVAS_H}px;
    display: flex; align-items: center; justify-content: center;
  }}
  .mark {{
    font-family: "Brand", sans-serif;
    font-size: {FONT_SIZE}px;
    letter-spacing: 0.22em;
    /* letter-spacing adds a trailing gap after the last glyph; pull it back
       so the wordmark is optically centred rather than sitting left. */
    text-indent: 0.22em;
    color: #fff;
    white-space: nowrap;
    /* Two shadows: a tight dark one for contrast against bright footage, and a
       wide soft one so the mark still reads over a busy, mid-tone frame. */
    text-shadow: 0 2px 6px rgba(0,0,0,.55), 0 0 26px rgba(0,0,0,.4);
  }}
</style>
<div class="mark">{name}</div>
"""


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else BRAND_NAME
    chrome = _find_chrome()

    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "mark.html")
        with open(page, "w") as f:
            f.write(_html(name))

        shot = os.path.join(tmp, "out.png")
        subprocess.run([
            chrome,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            # Transparent canvas -- without this Chrome paints an opaque white
            # background and the overlay becomes a white box.
            "--default-background-color=00000000",
            f"--window-size={CANVAS_W},{CANVAS_H}",
            f"--screenshot={shot}",
            f"file://{page}",
        ], check=True, capture_output=True)

        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        os.replace(shot, OUT_PATH)

    print(f"wrote {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes) for {name!r}")
    print(f"rasterised with {chrome}")


if __name__ == "__main__":
    main()
