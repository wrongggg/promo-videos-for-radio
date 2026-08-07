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

BRAND_NAME = "ONREPEAT.MOV"

# Lives under server/static/ rather than assets/ -- assets/ is gitignored (it's
# populated at render time by visuals.install_vendor), and the wordmark has to
# ship with the repo or a deployed container has nothing to watermark with.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND_DIR = os.path.join(ROOT, "server", "static", "brand")
FONT_PATH = os.path.join(BRAND_DIR, "bebas-neue.woff2")
OUT_PATH = os.path.join(BRAND_DIR, "watermark.png")

# A full-frame overlay, not a corner mark: compose.watermark_video composites it
# at 0,0 without scaling, so these are literally the output frame's dimensions.
# A corner mark is trivially cropped off, which defeats the point -- the job here
# is to make a video obviously un-postable while leaving it readable enough to
# judge. Tiled and rotated does that; a single mark does not.
CANVAS_W, CANVAS_H = 1080, 1920
FONT_SIZE = 52
# Rows x columns of marks. 3x2 puts 6 on screen, with alternate rows offset half
# a column so the tiling doesn't read as a rigid grid.
ROWS, COLS = 3, 2
ANGLE_DEG = -30
# Low enough to see the footage through it, high enough that nobody publishes it.
# The paired shadow is what keeps it legible over bright frames -- pure white at
# this opacity vanishes against pale artwork on its own.
MARK_OPACITY = 0.20


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

    # Cell centres, with odd rows nudged half a column across. Rotation happens
    # per mark about its own centre, so a mark near an edge runs off the canvas
    # and is clipped -- which is what makes the tiling read as continuous rather
    # than as six things arranged on a page.
    cell_w, cell_h = CANVAS_W / COLS, CANVAS_H / ROWS
    marks = []
    for row in range(ROWS):
        for col in range(COLS):
            cx = (col + 0.5) * cell_w + (cell_w / 2 if row % 2 else 0)
            cy = (row + 0.5) * cell_h
            marks.append(f'<div class="mark" style="left:{cx:.1f}px; top:{cy:.1f}px">{name}</div>')

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
    position: relative; overflow: hidden;
  }}
  .mark {{
    position: absolute;
    /* translate(-50%,-50%) centres the mark on its cell point before the
       rotation is applied, so each one spins about itself rather than swinging
       around the canvas origin. */
    transform: translate(-50%, -50%) rotate({ANGLE_DEG}deg);
    font-family: "Brand", sans-serif;
    font-size: {FONT_SIZE}px;
    letter-spacing: 0.22em;
    text-indent: 0.22em;
    color: rgba(255,255,255,{MARK_OPACITY});
    white-space: nowrap;
    /* Shadow alpha is scaled to the mark's own opacity -- at full strength it
       would read as a dark smear around ghosted text. It exists only to hold
       the letterforms apart from bright footage. */
    text-shadow: 0 1px 3px rgba(0,0,0,{MARK_OPACITY * 0.9:.2f});
  }}
</style>
{chr(10).join(marks)}
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
