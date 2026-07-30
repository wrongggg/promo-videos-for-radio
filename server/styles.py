"""Visual styles -- the part of a theme you can actually see and name.

A theme used to be only colours plus two enums (motion, frame), so every promo
came out with identical typography, identical text placement and identical
entrance animations. A style bundles everything else that makes one look
distinct from another:

  * typeface, size, weight, case and letter-spacing for each text role
  * where the text block sits and how it is aligned
  * how text enters and leaves
  * which Hydra patch runs behind it
  * how heavy the scrim is under the text

Two rules hold across every style, both deliberate:

  * Text is white or near-black -- never a palette colour. Coloured type over
    unpredictable album artwork was failing contrast (the artist name measured
    1.73:1 against a 3:1 floor) and it dates a promo instantly. Palette colour
    now lives in the orbs, the progress bar and the synth, where it can be as
    saturated as it likes without touching legibility.
  * Every family here is one the renderer can resolve and supply itself. The
    first pass used macOS system stacks (SF Mono, Avenir Next, Iowan Old Style,
    Haettenschweiler) on the theory that avoiding webfonts protected
    determinism. That was wrong twice over: the renderer runs headless on a
    host that has none of those faces, so all five styles would have collapsed
    into the same fallback sans, and the renderer already loads its own fonts
    before capturing, so a resolved family is deterministic anyway. These names
    were checked against the renderer one by one -- Roboto Mono, Space Grotesk,
    DM Sans, Lora, Anton and Libre Baskerville are NOT resolvable and must not
    be used without a matching @font-face.
"""
import os

# 'Noto Sans' and 'Noto Sans JP' sit in every stack because they are the only
# two broad-coverage families the renderer will supply itself -- everything the
# display faces don't cover (Cyrillic, Greek, Hebrew, Arabic, CJK, Devanagari,
# Thai) lands on them. See languages.py for the render test that confirmed all
# ten scripts, and for the caveat that the Latin-only display faces mean XL and
# Poppy lose some character in non-Latin languages.
_FALLBACK = "'Noto Sans', 'Noto Sans JP'"

FONTS = {
    "grotesque": f"'Inter', {_FALLBACK}, system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif",
    "mono": f"'IBM Plex Mono', ui-monospace, {_FALLBACK}, Menlo, Consolas, monospace",
    "serif": f"'Playfair Display', Georgia, 'Times New Roman', {_FALLBACK}, serif",
    "condensed": f"'Bebas Neue', 'Oswald', {_FALLBACK}, sans-serif",
    "display": f"'Archivo Black', 'Inter', {_FALLBACK}, system-ui, sans-serif",
}

WHITE = "#ffffff"
INK = "#0b0b0d"

# GSAP entrance/exit pairs. Each style picks one, so a promo is consistent
# throughout instead of cycling a different animation every scene.
#
# `chars: True` animates the title one character at a time (typing, spinning,
# scattering). compose.py splits the title into <span class="ch"> for those and
# points the tween at the spans instead of the block.
#
# Every value animated here is a transform or opacity. Layout properties --
# letterSpacing, width, font-size, character count -- snap to whole device
# pixels, so under seek-by-frame capture their ease-out tails stutter instead
# of gliding. The linter rejects them outright.
ENTRANCES = {
    "rise": {
        "from": "{ y: 90, opacity: 0 }",
        "to": "{ y: 0, opacity: 1, duration: 0.9, ease: 'power3.out' }",
        "exit": "{ opacity: 0, y: -40, duration: 0.5, ease: 'power2.in' }",
    },
    "fade": {
        "from": "{ opacity: 0 }",
        "to": "{ opacity: 1, duration: 1.2, ease: 'power1.out' }",
        "exit": "{ opacity: 0, duration: 0.6, ease: 'power1.in' }",
    },
    "slide": {
        "from": "{ x: -120, opacity: 0 }",
        "to": "{ x: 0, opacity: 1, duration: 0.8, ease: 'power4.out' }",
        "exit": "{ opacity: 0, x: 90, duration: 0.45, ease: 'power3.in' }",
    },
    "snap": {
        "from": "{ scale: 1.25, opacity: 0 }",
        "to": "{ scale: 1, opacity: 1, duration: 0.45, ease: 'power4.out' }",
        "exit": "{ opacity: 0, scale: 0.94, duration: 0.35, ease: 'power2.in' }",
    },
    "drift": {
        "from": "{ y: 44, opacity: 0, scale: 1.05 }",
        "to": "{ y: 0, opacity: 1, scale: 1, duration: 1.7, ease: 'power2.out' }",
        "exit": "{ opacity: 0, scale: 1.03, duration: 0.85, ease: 'power1.in' }",
    },

    # --- per-character ---
    "type": {
        # A hard cut per character with no ease, which is what makes it read as
        # typing rather than fading. Opacity only, so nothing reflows.
        "from": "{ opacity: 0 }",
        "to": "{ opacity: 1, duration: 0.01, ease: 'none', stagger: 0.045 }",
        "exit": "{ opacity: 0, duration: 0.4, ease: 'power1.in' }",
        "chars": True,
    },
    "spin": {
        "from": "{ opacity: 0, rotation: -90, scale: 0.4 }",
        "to": "{ opacity: 1, rotation: 0, scale: 1, duration: 0.55, ease: 'back.out(2)', stagger: 0.03 }",
        "exit": "{ opacity: 0, rotation: 25, duration: 0.4, ease: 'power2.in' }",
        "chars": True,
    },
    "flip": {
        "from": "{ opacity: 0, rotationX: -90, y: 20 }",
        "to": "{ opacity: 1, rotationX: 0, y: 0, duration: 0.5, ease: 'power3.out', stagger: 0.035 }",
        "exit": "{ opacity: 0, rotationX: 45, duration: 0.4, ease: 'power2.in' }",
        "chars": True,
    },
    "scatter": {
        # Offsets come from the character index, not Math.random -- the
        # composition has to render identically on every pass.
        #
        # GSAP takes function-based values per *property*; handing it a function
        # as the whole fromVars object silently does nothing, which is exactly
        # how this first shipped (the letters just appeared in place).
        "from": ("{ opacity: 0, scale: 0.7,"
                 " x: function (i) { return ((i * 37) % 17 - 8) * 11; },"
                 " y: function (i) { return ((i * 61) % 13 - 6) * 14; },"
                 " rotation: function (i) { return ((i * 29) % 11 - 5) * 7; } }"),
        "to": "{ opacity: 1, x: 0, y: 0, rotation: 0, scale: 1, duration: 0.7, ease: 'power3.out', stagger: 0.02 }",
        "exit": "{ opacity: 0, scale: 0.9, duration: 0.4, ease: 'power2.in' }",
        "chars": True,
    },
    "wave": {
        "from": "{ opacity: 0, y: 52 }",
        "to": "{ opacity: 1, y: 0, duration: 0.75, ease: 'elastic.out(1, 0.6)', stagger: 0.028 }",
        "exit": "{ opacity: 0, y: -26, duration: 0.4, ease: 'power2.in' }",
        "chars": True,
    },
    "stamp": {
        "from": "{ opacity: 0, scale: 2.4, rotation: -8 }",
        "to": "{ opacity: 1, scale: 1, rotation: 0, duration: 0.32, ease: 'power4.out' }",
        "exit": "{ opacity: 0, scale: 1.1, duration: 0.3, ease: 'power2.in' }",
    },
}

# How one track's artwork hands over to the next. Handled by the visuals
# runtime (visuals.py), which overlaps the outgoing and incoming images for
# `secs` and drives both from the same baked timeline.
TRANSITIONS = {
    "fade":  {"secs": 0.55, "kind": "fade"},
    "slide": {"secs": 0.55, "kind": "slide"},
    "zoom":  {"secs": 0.55, "kind": "zoom"},
    "swap":  {"secs": 0.0,  "kind": "swap"},   # hard cut, no overlap
    "spin":  {"secs": 0.6,  "kind": "spin"},
}

# Hydra patches. `A` is the accent as an rgb triple; `h.time` is set absolutely
# by the driver, never accumulated (see visuals.py).
PATCHES = {
    "kaleid": """
      h.osc(14, 0.06, 1.0)
        .color(A.r, A.g, A.b)
        .kaleid(6)
        .rotate(() => h.time * 0.14)
        .modulate(h.noise(2.6, 0.12), 0.32)
        .brightness(-0.14)
        .out(h.o0);
    """,
    "grain": """
      h.noise(3.2, 0.05)
        .color(A.r, A.g, A.b)
        .contrast(1.3)
        .modulate(h.osc(3, 0.02).rotate(() => h.time * 0.03), 0.12)
        .brightness(-0.22)
        .out(h.o0);
    """,
    "flow": """
      h.osc(4, 0.02, 0.6)
        .color(A.r, A.g, A.b)
        .rotate(() => h.time * 0.04)
        .modulate(h.noise(1.4, 0.03), 0.5)
        .blur(0.4)
        .brightness(-0.18)
        .out(h.o0);
    """,
    "bars": """
      h.osc(24, 0.0, 0.0)
        .thresh(0.5, 0.02)
        .color(A.r, A.g, A.b)
        .rotate(1.5708)
        .scrollX(() => h.time * 0.01)
        .brightness(-0.3)
        .out(h.o0);
    """,
    "haze": """
      h.voronoi(6, 0.12, 0.2)
        .color(A.r, A.g, A.b)
        .modulate(h.noise(1.8, 0.02), 0.28)
        .blur(0.6)
        .brightness(-0.24)
        .out(h.o0);
    """,
}

SCRIMS = {
    # Under-text darkening. Heavier scrims buy contrast on busy artwork.
    "soft": ("linear-gradient(180deg, rgba(0,0,0,0.35) 0%, rgba(0,0,0,0.10) 38%, "
             "rgba(0,0,0,0.78) 100%)"),
    "heavy": ("linear-gradient(180deg, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.30) 32%, "
              "rgba(0,0,0,0.92) 100%)"),
    "top": ("linear-gradient(180deg, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.45) 40%, "
            "rgba(0,0,0,0.25) 100%)"),
    # Used by the one light-on-dark inversion; the panel does the contrast work.
    "light": ("linear-gradient(180deg, rgba(0,0,0,0.20) 0%, rgba(0,0,0,0.05) 40%, "
              "rgba(0,0,0,0.45) 100%)"),
}


# Five looks, chosen to be told apart at a glance rather than to be five shades
# of the same idea. They differ on all four axes at once -- typeface, where the
# text sits, how it enters, and what runs behind it -- so the thumbnails read as
# genuinely different products.
STYLES = {
    "classic": {
        "label": "Classic",
        "blurb": "Clean and neutral. Centred sans type, gentle rise, soft haze behind. The safe choice for any show.",
        "font": "grotesque",
        "title": {"max_size": 104, "weight": 800, "case": "none", "spacing": -1, "line": 1.06},
        "artist": {"size": 32, "weight": 600, "case": "uppercase", "spacing": 6},
        "trivia": {"size": 30, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "center", "anchor": "bottom", "bottom_gap": 230,
        "entrance": "rise", "transition": "fade", "patch": "haze", "scrim": "soft",
    },
    "poppy": {
        "label": "Poppy",
        "blurb": "Black type on a bright white card, bouncy entrances, saturated colour behind. Loud and friendly.",
        "font": "display",
        "title": {"max_size": 88, "weight": 400, "case": "none", "spacing": -1, "line": 1.04},
        "artist": {"size": 29, "weight": 400, "case": "uppercase", "spacing": 5},
        "trivia": {"size": 29, "weight": 500},
        "color": INK,
        # The card is what makes black type safe over arbitrary album art, and
        # it is also what makes this style read as "pop" rather than just
        # "bright" -- a hard white block against a saturated backdrop.
        "panel": {"bg": "rgba(255,255,255,0.95)", "pad": "52px 58px", "radius": 28},
        "align": "left", "anchor": "bottom", "bottom_gap": 200,
        "entrance": "snap", "transition": "zoom", "patch": "kaleid", "scrim": "light",
    },
    "xl": {
        "label": "XL",
        "blurb": "Type as the whole picture. Enormous condensed caps that fill the frame, hard cuts, stark bars.",
        "font": "condensed",
        "title": {"max_size": 210, "weight": 400, "case": "uppercase", "spacing": 1, "line": 0.88},
        "artist": {"size": 44, "weight": 400, "case": "uppercase", "spacing": 10},
        "trivia": {"size": 32, "weight": 500},
        "color": WHITE, "panel": None,
        "align": "left", "anchor": "bottom", "bottom_gap": 190,
        "entrance": "snap", "transition": "swap", "patch": "bars", "scrim": "heavy",
    },
    "editorial": {
        "label": "Liner Notes",
        "blurb": "Monospaced and left-aligned like a sleeve credit. Quiet, factual, text-forward.",
        "font": "mono",
        "title": {"max_size": 86, "weight": 600, "case": "none", "spacing": -1, "line": 1.14},
        "artist": {"size": 29, "weight": 500, "case": "uppercase", "spacing": 5},
        "trivia": {"size": 30, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "left", "anchor": "bottom", "bottom_gap": 240,
        "entrance": "slide", "transition": "fade", "patch": "grain", "scrim": "heavy",
    },
    "ambient": {
        "label": "Slow Ambient",
        "blurb": "Airy letter-spaced serif that drifts in, centred high, over soft flow. For downtempo, jazz and classical.",
        "font": "serif",
        "title": {"max_size": 90, "weight": 400, "case": "none", "spacing": 1, "line": 1.18},
        "artist": {"size": 28, "weight": 400, "case": "uppercase", "spacing": 12},
        "trivia": {"size": 30, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "center", "anchor": "center", "bottom_gap": 0,
        "entrance": "drift", "transition": "fade", "patch": "flow", "scrim": "soft",
    },
    "terminal": {
        "label": "Terminal",
        "blurb": "Titles type themselves out character by character in mono, over scrolling bars. Deliberately machine-like.",
        "font": "mono",
        "title": {"max_size": 84, "weight": 600, "case": "uppercase", "spacing": 0, "line": 1.12},
        "artist": {"size": 28, "weight": 500, "case": "uppercase", "spacing": 4},
        "trivia": {"size": 29, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "left", "anchor": "bottom", "bottom_gap": 250,
        "entrance": "type", "transition": "swap", "patch": "bars", "scrim": "heavy",
    },
    "carousel": {
        "label": "Carousel",
        "blurb": "Artwork slides across like a deck of covers while titles push in from the side. Restless and modern.",
        "font": "grotesque",
        "title": {"max_size": 98, "weight": 800, "case": "none", "spacing": -1, "line": 1.05},
        "artist": {"size": 30, "weight": 600, "case": "uppercase", "spacing": 6},
        "trivia": {"size": 30, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "left", "anchor": "bottom", "bottom_gap": 225,
        "entrance": "slide", "transition": "slide", "patch": "flow", "scrim": "soft",
    },
    "kinetic": {
        "label": "Kinetic",
        "blurb": "Every letter spins into place and the covers spin with them. The loudest option here.",
        "font": "display",
        "title": {"max_size": 86, "weight": 400, "case": "uppercase", "spacing": 0, "line": 1.04},
        "artist": {"size": 30, "weight": 400, "case": "uppercase", "spacing": 6},
        "trivia": {"size": 29, "weight": 500},
        "color": WHITE, "panel": None,
        "align": "center", "anchor": "bottom", "bottom_gap": 235,
        "entrance": "spin", "transition": "spin", "patch": "kaleid", "scrim": "heavy",
    },
    "flipboard": {
        "label": "Flipboard",
        "blurb": "Letters flip over like an airport board, covers cut hard between tracks. Crisp and rhythmic.",
        "font": "condensed",
        "title": {"max_size": 150, "weight": 400, "case": "uppercase", "spacing": 1, "line": 0.92},
        "artist": {"size": 36, "weight": 400, "case": "uppercase", "spacing": 8},
        "trivia": {"size": 30, "weight": 500},
        "color": WHITE, "panel": None,
        "align": "left", "anchor": "bottom", "bottom_gap": 210,
        "entrance": "flip", "transition": "swap", "patch": "bars", "scrim": "heavy",
    },
    "confetti": {
        "label": "Confetti",
        "blurb": "Letters fly in from all directions and settle; artwork zooms through. Playful and chaotic.",
        "font": "grotesque",
        "title": {"max_size": 92, "weight": 900, "case": "none", "spacing": -1, "line": 1.04},
        "artist": {"size": 30, "weight": 700, "case": "uppercase", "spacing": 5},
        "trivia": {"size": 29, "weight": 500},
        "color": INK,
        "panel": {"bg": "rgba(255,255,255,0.95)", "pad": "50px 56px", "radius": 22},
        "align": "left", "anchor": "bottom", "bottom_gap": 205,
        "entrance": "scatter", "transition": "zoom", "patch": "kaleid", "scrim": "light",
    },
    "tidal": {
        "label": "Tidal",
        "blurb": "Letters rise on an elastic swell, one after another, over slow haze. Big but unhurried.",
        "font": "serif",
        "title": {"max_size": 104, "weight": 400, "case": "none", "spacing": 0, "line": 1.1},
        "artist": {"size": 30, "weight": 400, "case": "uppercase", "spacing": 9},
        "trivia": {"size": 30, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "center", "anchor": "bottom", "bottom_gap": 240,
        "entrance": "wave", "transition": "fade", "patch": "haze", "scrim": "soft",
    },
}

DEFAULT_STYLE = "classic"
STYLE_KEYS = tuple(STYLES.keys())


def get(key: str | None) -> dict:
    return STYLES.get(key or "", STYLES[DEFAULT_STYLE])


PREVIEW_TITLE = "Afterglow"
PREVIEW_ARTIST = "SABLE"


def thumbnail_markup(key: str, palette: dict | None = None) -> str:
    """The full preview tile: generated SVG for the backdrop and covers, with a
    real-type text block laid over it."""
    st = get(key)
    svg = thumbnail_svg(key, palette)

    ent = ENTRANCES[st["entrance"]]
    per_char = bool(ent.get("chars"))
    if per_char:
        # Characters are grouped per word. Each character is its own
        # inline-block, which gives the line a break opportunity between every
        # pair of them -- Flipboard split a title as "NIGHT FERR / Y". A nowrap
        # word span means a break can only land between words.
        words = []
        n = 0
        for word in PREVIEW_TITLE.split(" "):
            chars = "".join(
                f'<span class="tp-ch tp-ch{(n + j) % 6}">{c}</span>'
                for j, c in enumerate(word)
            )
            n += len(word)
            words.append(f'<span class="tp-word">{chars}</span>')
        title = '<span class="tp-sp"> </span>'.join(words)
    else:
        title = f'<span class="tp-ch tp-ch0">{PREVIEW_TITLE}</span>'

    # Scale the real type to the tile (112px wide against a 1080px frame), then
    # clamp. XL's 210px would land at 24px here and run straight off the edge,
    # so the ceiling matters more than the ratio -- the tile shows which
    # typeface and how it moves, not the true size.
    t_px = max(11, min(16, round(st["title"]["max_size"] * 0.115)))
    a_px = max(6, min(9, round(st["artist"]["size"] * 0.20)))

    return (
        f'{svg}'
        f'<span class="tp-txt tp-{key}">'
        f'<span class="tp-title" style="font-size:{t_px}px">{title}</span>'
        f'<span class="tp-artist" style="font-size:{a_px}px">{PREVIEW_ARTIST}</span>'
        f'</span>'
    )


def preview_layout_css() -> str:
    """Per-style positioning and typography for the preview text block."""
    out = [
        ".theme-thumb { position: relative; }",
        ".tp-txt { position: absolute; left: 0; right: 0; display: flex;"
        " flex-direction: column; padding: 0 8px; pointer-events: none;"
        " overflow: hidden; }",
        ".tp-sp { display: inline; }",
        ".tp-word { display: inline-block; white-space: nowrap; }",
        ".tp-title { display: block; line-height: 1.02; }",
        ".tp-ch { display: inline-block; will-change: transform, opacity; }",
        ".tp-artist { display: block; opacity: .75; margin-top: 3px; letter-spacing: 1px; }",
    ]
    for key, st in STYLES.items():
        font = FONTS[st["font"]]
        ink = st["color"] == INK
        colour = "#0b0b0d" if ink else "#fff"
        align = "center" if st["align"] == "center" else "flex-start"
        pos = ("top: 46%;" if st["anchor"] == "center" else "bottom: 12%;")
        panel = (" background: rgba(255,255,255,.95); border-radius: 5px;"
                 " padding: 6px 8px; margin: 0 6px;" if st.get("panel") else "")
        shadow = "" if ink else " text-shadow: 0 1px 4px rgba(0,0,0,.8);"
        case = "uppercase" if st["title"]["case"] == "uppercase" else "none"
        out.append(
            f".tp-{key} {{ font-family: {font}; color: {colour}; {pos}"
            f" align-items: {align}; text-align: {st['align']};"
            f" font-weight: {st['title']['weight']}; text-transform: {case};"
            f"{shadow}{panel} }}"
        )
    return "\n".join(out)


def choices(palettes: dict | None = None) -> list[dict]:
    """What the picker in the UI renders, thumbnail included.

    `palettes` maps a style key to the palette that theme actually ships with,
    so each thumbnail previews its own colours rather than a shared stand-in --
    which is most of what makes the five readable at a glance."""
    palettes = palettes or {}
    return [
        {"key": k, "label": v["label"], "blurb": v["blurb"],
         "thumb": thumbnail_markup(k, palettes.get(k))}
        for k, v in STYLES.items()
    ]


# Rough stand-ins for each patch in a still thumbnail. A thumbnail only has to
# convey "busy kaleidoscope" vs "quiet haze", so these are a few shapes rather
# than an attempt to run Hydra at preview size.
def _patch_thumb(patch: str, a1: str, a2: str) -> str:
    if patch == "kaleid":
        return (
            f'<g opacity="0.85">'
            f'<circle cx="60" cy="70" r="46" fill="{a1}"/>'
            f'<circle cx="84" cy="104" r="34" fill="{a2}" opacity="0.8"/>'
            f'<path d="M0 150 L60 60 L120 150 Z" fill="{a2}" opacity="0.55"/>'
            f'<path d="M0 60 L60 150 L120 60 Z" fill="{a1}" opacity="0.4"/></g>'
        )
    if patch == "bars":
        bars = "".join(
            f'<rect x="{x}" y="0" width="9" height="213" fill="{a1 if i % 2 else a2}" '
            f'opacity="{0.30 + (i % 3) * 0.16:.2f}"/>'
            for i, x in enumerate(range(0, 120, 15))
        )
        return f"<g>{bars}</g>"
    if patch == "grain":
        dots = "".join(
            f'<circle cx="{(i * 37) % 118 + 1}" cy="{(i * 61) % 210 + 2}" r="{1 + (i % 3)}" '
            f'fill="{a1 if i % 2 else a2}" opacity="0.5"/>'
            for i in range(70)
        )
        return f"<g>{dots}</g>"
    if patch == "flow":
        return (
            f'<g opacity="0.7">'
            f'<ellipse cx="34" cy="70" rx="62" ry="46" fill="{a1}"/>'
            f'<ellipse cx="92" cy="150" rx="58" ry="52" fill="{a2}"/></g>'
        )
    return (  # haze
        f'<g opacity="0.6">'
        f'<circle cx="40" cy="66" r="52" fill="{a1}"/>'
        f'<circle cx="86" cy="150" r="46" fill="{a2}"/></g>'
    )


# --- animated hover preview -------------------------------------------------
#
# The picker preview animates the generated SVG rather than embedding video.
# Real clips would be ~20MB each, would need re-rendering whenever a style
# changes, and would show whatever tracklist happened to be used to record them
# instead of the user's. Animating the same SVG that draws the static thumbnail
# keeps the preview honest: it is generated from the style definition, so it
# cannot drift from what renders.
#
# The loop shows two covers and one hand-over, using that style's real
# transition kind and entrance family.
PREVIEW_SECS = 3.0

# Cover hand-over, per transition kind. The window is 45%-62% of the loop.
_COVER_KEYFRAMES = {
    "fade": ("0%,45% { opacity: 1; transform: none; }"
             " 62%,100% { opacity: 0; transform: none; }",
             "0%,45% { opacity: 0; }"
             " 62%,100% { opacity: 1; }"),
    "slide": ("0%,45% { opacity: 1; transform: translateX(0); }"
              " 62%,100% { opacity: 1; transform: translateX(-100%); }",
              "0%,45% { opacity: 1; transform: translateX(100%); }"
              " 62%,100% { opacity: 1; transform: translateX(0); }"),
    "zoom": ("0%,45% { opacity: 1; transform: scale(1); }"
             " 62%,100% { opacity: 0; transform: scale(0.8); }",
             "0%,45% { opacity: 0; transform: scale(1.4); }"
             " 62%,100% { opacity: 1; transform: scale(1); }"),
    "spin": ("0%,45% { opacity: 1; transform: rotate(0deg) scale(1); }"
             " 62%,100% { opacity: 0; transform: rotate(12deg) scale(0.85); }",
             "0%,45% { opacity: 0; transform: rotate(-14deg) scale(0.72); }"
             " 62%,100% { opacity: 1; transform: rotate(0deg) scale(1); }"),
    # A hard cut: no interpolation, so the steps() timing does the work.
    "swap": ("0%,52% { opacity: 1; } 52.01%,100% { opacity: 0; }",
             "0%,52% { opacity: 0; } 52.01%,100% { opacity: 1; }"),
}

# How the text bars arrive. Char-based entrances get a per-bar delay so the
# preview reads as a stagger rather than a single move.
_TEXT_KEYFRAMES = {
    "rise":    "from { opacity: 0; transform: translateY(14px); }",
    "fade":    "from { opacity: 0; }",
    "slide":   "from { opacity: 0; transform: translateX(-26px); }",
    "snap":    "from { opacity: 0; transform: scale(1.35); }",
    "drift":   "from { opacity: 0; transform: translateY(9px) scale(1.05); }",
    "type":    "from { opacity: 0; transform: scaleX(0); }",
    "spin":    "from { opacity: 0; transform: rotate(-70deg) scale(0.4); }",
    "flip":    "from { opacity: 0; transform: scaleY(0.1); }",
    "scatter": "from { opacity: 0; transform: translate(-14px, 12px) rotate(-18deg); }",
    "wave":    "from { opacity: 0; transform: translateY(20px); }",
    "stamp":   "from { opacity: 0; transform: scale(2.2) rotate(-8deg); }",
}

_STAGGERED = {"type", "spin", "flip", "scatter", "wave"}


def thumbnail_css() -> str:
    """Hover-preview CSS for every style, emitted once into the page.

    Animations are only attached on hover, so nothing runs until the user points
    at a card -- eleven looping previews playing at once would be noise, and on
    a long list it would burn battery for no reason."""
    out = [
        # Transforms on SVG children need a box to resolve percentages and a
        # sane origin, or translateX(100%) means something unexpected.
        ".theme-thumb svg .tp-cover {"
        " transform-box: fill-box; transform-origin: center; }",
        ".theme-thumb svg .tp-b { opacity: 0; }",
    ]
    for key, st in STYLES.items():
        kind = TRANSITIONS.get(st.get("transition", "fade"), TRANSITIONS["fade"])["kind"]
        a_kf, b_kf = _COVER_KEYFRAMES.get(kind, _COVER_KEYFRAMES["fade"])
        ent = st["entrance"]
        t_kf = _TEXT_KEYFRAMES.get(ent, _TEXT_KEYFRAMES["fade"])
        ease = "steps(1, end)" if kind == "swap" else "cubic-bezier(.4,0,.2,1)"
        t_ease = "steps(1, end)" if ent == "type" else "cubic-bezier(.2,.7,.3,1)"

        out.append(f"@keyframes tpa-{key} {{ {a_kf} }}")
        out.append(f"@keyframes tpb-{key} {{ {b_kf} }}")
        out.append(f"@keyframes tpt-{key} {{ {t_kf} }}")

        # Two triggers: :hover for the mouse, a `.previewing` class for keyboard
        # focus and touch (where there is no hover state at all).
        #
        # Selectors are built as explicit lists, never by concatenating another
        # selector string. Doing that produced a stray sibling combinator that
        # matched at a HIGHER specificity than the per-character delay rules, so
        # its `animation` shorthand reset every delay to 0s and the stagger --
        # the whole point of the preview -- silently never happened.
        def rule(suffix: str, body: str) -> str:
            sels = [f'.theme-card[data-mode="preset:{key}"]{trig} {suffix}'
                    for trig in (":hover", ".previewing")]
            return ", ".join(sels) + " { " + body + " }"

        out.append(rule(".theme-thumb svg .tp-a",
                        f"animation: tpa-{key} {PREVIEW_SECS}s {ease} infinite;"))
        out.append(rule(".theme-thumb svg .tp-b",
                        f"animation: tpb-{key} {PREVIEW_SECS}s {ease} infinite;"))
        # Title characters and the artist line both animate -- the artist is
        # part of the feel, and animating only the title looked half-finished.
        out.append(rule(".tp-txt .tp-ch",
                        f"animation: tpt-{key} 0.55s {t_ease} both;"))
        out.append(rule(".tp-txt .tp-artist",
                        f"animation: tpt-{key} 0.5s cubic-bezier(.2,.7,.3,1) both;"
                        f" animation-delay: 0.3s;"))

        if ent in _STAGGERED:
            # Same selector shape as the rule above plus one class, so these
            # win on specificity as well as order.
            for n in range(6):
                out.append(rule(f".tp-txt .tp-ch{n}",
                                f"animation-delay: {0.06 * n:.2f}s;"))
    return "\n".join(out)


# Optional real cover images. Drop any two square files into
# server/static/preview and the tiles use them instead of the generated
# stand-in -- filenames don't matter, they're taken in sorted order. See
# PREVIEW_IMAGE_BRIEF for what to generate.
PREVIEW_IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "static", "preview")
_PREVIEW_THUMB_DIR = os.path.join(PREVIEW_IMAGE_DIR, "thumbs")
_PREVIEW_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# The tile is ~112px wide, so a 2048px source is three orders of magnitude more
# pixels than it can show. Generated art tends to arrive that size (the first
# pair were 8MB and 6MB PNGs), and every card references both images -- so they
# get downscaled once and cached, rather than shipping ~14MB into a picker.
PREVIEW_THUMB_WIDTH = 320


def _preview_sources() -> list[str]:
    try:
        names = sorted(
            n for n in os.listdir(PREVIEW_IMAGE_DIR)
            if os.path.splitext(n)[1].lower() in _PREVIEW_EXTS
        )
    except OSError:
        return []
    return [os.path.join(PREVIEW_IMAGE_DIR, n) for n in names[:2]]


def _ensure_thumb(src: str) -> str | None:
    """Downscaled copy of `src`, generated once and reused.

    Best-effort: if ffmpeg isn't usable the caller falls back to the generated
    artwork rather than serving a 8MB PNG into the picker."""
    stem = os.path.splitext(os.path.basename(src))[0]
    safe = "".join(c if c.isalnum() else "-" for c in stem)[:60]
    dest = os.path.join(_PREVIEW_THUMB_DIR, f"{safe}.jpg")
    if os.path.exists(dest) and os.path.getmtime(dest) >= os.path.getmtime(src):
        return dest
    try:
        import subprocess
        os.makedirs(_PREVIEW_THUMB_DIR, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vf", f"scale={PREVIEW_THUMB_WIDTH}:-1",
             "-q:v", "4", dest],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
        return dest if os.path.exists(dest) else None
    except Exception:
        return None


def preview_image(variant: int) -> str | None:
    """Web path of a real preview cover, or None if none has been added."""
    sources = _preview_sources()
    if len(sources) <= variant:
        return None
    thumb = _ensure_thumb(sources[variant])
    if not thumb:
        return None
    return f"/static/preview/thumbs/{os.path.basename(thumb)}"


def _cover_art(variant: int, a1: str, a2: str, accent: str, key: str) -> str:
    """A stand-in album cover.

    Used only until real cover images are dropped into static/preview -- see
    preview_image(). Deliberately abstract: overlapping washes of colour with
    grain and a vignette, the shapes a sleeve tends to use. No figure: a
    silhouette read as a stock portrait rather than as album art, which is the
    opposite of what a cover looks like.

    Two variants so a hand-over swaps to a different image, not to itself."""
    W, H = 120, 213
    v = "A" if variant == 0 else "B"
    if variant == 0:
        forms = (
            f'<circle cx="60" cy="88" r="44" fill="{accent}" opacity="0.85"/>'
            f'<circle cx="60" cy="88" r="44" fill="url(#cvA{key})" opacity="0.45"/>'
            f'<path d="M0 132 Q60 104 120 138 L120 213 L0 213 Z" fill="{a2}" opacity="0.55"/>'
        )
    else:
        forms = (
            f'<path d="M0 0 L120 0 L120 118 L0 62 Z" fill="{a2}" opacity="0.9"/>'
            f'<circle cx="74" cy="132" r="42" fill="{accent}" opacity="0.75"/>'
            f'<rect x="0" y="150" width="120" height="10" fill="{a1}" opacity="0.8"/>'
        )
    return (
        f'<rect width="{W}" height="{H}" fill="url(#cv{v}{key})"/>'
        f'<g filter="url(#blur{key})">{forms}</g>'
        f'<rect width="{W}" height="{H}" fill="url(#vig{key})"/>'
        f'<rect width="{W}" height="{H}" filter="url(#grain{key})" opacity="0.14"/>'
    )


# Hand this to an image model. Square, because real covers are square and the
# renderer crops them to 9:16 -- so anything vital must sit centre, and the
# lower third has to stay calm or the overlaid title stops being legible.
PREVIEW_IMAGE_BRIEF = """\
Square 1:1 abstract album cover artwork. No text, no lettering, no logos, no
faces or people. Keep the lower third visually calm and darker so overlaid
white type stays readable; put the interesting detail in the upper two thirds.
Print-quality, 35mm grain, slight chromatic aberration, rich but not neon.
"""


def _cover_layer(variant, a1, a2, accent, key, W, H) -> str:
    src = preview_image(variant)
    if src:
        return (f'<image href="{src}" x="0" y="0" width="{W}" height="{H}" '
                f'preserveAspectRatio="xMidYMid slice"/>'
                f'<rect width="{W}" height="{H}" fill="url(#vig{key})"/>')
    return _cover_art(variant, a1, a2, accent, key)


def thumbnail_svg(key: str, palette: dict | None = None) -> str:
    """A 120x213 (9:16) preview of one style.

    Layered exactly like the rendered frame: synth patch at the back, artwork
    over it, scrim on top, and the text laid over that as HTML (see
    thumbnail_markup). Generated from the style definition rather than drawn by
    hand, so a change to palette, patch or scrim shows up in the picker without
    anyone remembering to update an image.

    There are no placeholder bars any more -- the preview shows real type, so a
    stand-in for it was just clutter sitting under the actual words."""
    st = get(key)
    pal = palette or {"orb1": "#7b5cff", "orb2": "#00d4ff", "accent": "#ff2e88"}
    a1 = pal.get("orb1", "#7b5cff")
    a2 = pal.get("orb2", "#00d4ff")
    accent = pal.get("accent", "#ff2e88")
    W, H = 120, 213
    heavy = st["scrim"] == "heavy"

    defs = (
        f'<defs>'
        f'<linearGradient id="cvA{key}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{a1}"/><stop offset="100%" stop-color="#0b0b12"/>'
        f'</linearGradient>'
        f'<linearGradient id="cvB{key}" x1="1" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{a2}"/><stop offset="100%" stop-color="#0b0b12"/>'
        f'</linearGradient>'
        # Soft bokeh, a vignette and film grain -- the three things that make a
        # flat vector read as a photograph rather than a diagram.
        f'<filter id="blur{key}"><feGaussianBlur stdDeviation="7"/></filter>'
        f'<filter id="grain{key}"><feTurbulence type="fractalNoise" '
        f'baseFrequency="0.9" numOctaves="2"/></filter>'
        f'<radialGradient id="vig{key}" cx="50%" cy="42%" r="72%">'
        f'<stop offset="55%" stop-color="#000" stop-opacity="0"/>'
        f'<stop offset="100%" stop-color="#000" stop-opacity="0.6"/>'
        f'</radialGradient>'
        f'<linearGradient id="g{key}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#000" stop-opacity="{0.5 if heavy else 0.25}"/>'
        f'<stop offset="40%" stop-color="#000" stop-opacity="0.10"/>'
        f'<stop offset="100%" stop-color="#000" stop-opacity="{0.92 if heavy else 0.78}"/>'
        f'</linearGradient>'
        f'</defs>'
    )

    parts = [
        f'<rect width="{W}" height="{H}" fill="#0a0a0f"/>',
        # Two covers: .tp-a on screen, .tp-b arriving. thumbnail_css keeps
        # .tp-b hidden until the card is previewing.
        # A real cover image if one has been added, otherwise the generated
        # stand-in. preserveAspectRatio slice crops a square file to the tile
        # the same way the renderer crops artwork to 9:16.
        f'<g class="tp-cover tp-a" opacity="0.92">{_cover_layer(0, a1, a2, accent, key, W, H)}</g>',
        f'<g class="tp-cover tp-b" opacity="0.92">{_cover_layer(1, a1, a2, accent, key, W, H)}</g>',
        # The synth patch, screened lightly over the cover. Behind the cover it
        # was invisible at tile size; at 0.42 it drowned the artwork instead,
        # which is the opposite error -- the covers are dark, and screening a
        # bright patch over a dark image simply replaces it. 0.2 tints the tile
        # with each theme's patch while leaving the cover clearly the subject,
        # which is also how the rendered frame reads. The tiles stay easy to
        # tell apart on typography, layout and scrim regardless.
        f'<g opacity="0.2" style="mix-blend-mode: screen">'
        f'{_patch_thumb(st["patch"], a1, a2)}</g>',
        f'<rect width="{W}" height="{H}" fill="url(#g{key})"/>',
    ]

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" aria-label="{st["label"]} preview">'
        f'{defs}{"".join(parts)}</svg>'
    )


# --------------------------------------------------------------------------
# Composition text styling
#
# These three are consumed by compose.py for the rendered video, not by the
# picker. They were destroyed by an edit that replaced styles.py "from
# `def thumbnail_svg` to end of file" -- they happened to live after it, so
# they went with it, and every render failed with
# "module 'styles' has no attribute 'title_size'" until this was restored.
# --------------------------------------------------------------------------


def _case_css(value: str) -> str:
    return "none" if value == "none" else value


def text_css(style: dict) -> str:
    """CSS for the text roles. Sizes for the title are a ceiling -- compose.py
    steps it down for long titles."""
    font = FONTS[style["font"]]
    title, artist, trivia = style["title"], style["artist"], style["trivia"]
    color = style["color"]
    align = style["align"]
    items = "center" if align == "center" else "flex-start"

    # Black type gets no shadow (it sits on its own light panel); white type
    # gets a soft one so it survives a bright patch of artwork.
    shadow = "none" if color == INK else "0 4px 26px rgba(0,0,0,0.72)"

    panel = style.get("panel")
    if panel:
        panel_css = (
            f"background: {panel['bg']}; padding: {panel['pad']}; "
            f"border-radius: {panel['radius']}px; max-width: 900px; opacity: 0;"
        )
    else:
        panel_css = ""

    if style["anchor"] == "center":
        position_css = "justify-content: center; margin: 0;"
    else:
        position_css = f"margin-top: auto; margin-bottom: {style['bottom_gap']}px;"

    return f"""
.meta-container {{
  position: relative; z-index: 10; width: 100%;
  display: flex; flex-direction: column; align-items: {items};
  text-align: {align}; {position_css}
}}
.meta-inner {{ display: flex; flex-direction: column; align-items: {items}; {panel_css} }}
.track-title {{
  font-family: {font}; font-weight: {title['weight']};
  text-transform: {_case_css(title['case'])}; letter-spacing: {title['spacing']}px;
  line-height: {title['line']}; color: {color}; text-shadow: {shadow};
  margin-bottom: 18px; max-width: 950px;
}}
.artist-name {{
  font-family: {font}; font-size: {artist['size']}px; font-weight: {artist['weight']};
  text-transform: {_case_css(artist['case'])}; letter-spacing: {artist['spacing']}px;
  color: {color}; text-shadow: {shadow}; margin-bottom: 14px; opacity: 0.92;
}}
.trivia-tag {{
  font-family: {font}; font-size: {trivia['size']}px; font-weight: {trivia['weight']};
  line-height: 1.4; color: {color}; text-shadow: {shadow};
  letter-spacing: 0.2px; max-width: 840px; margin-bottom: 14px; opacity: 0.88;
}}
.scrim {{ background: {SCRIMS[style['scrim']]}; }}
"""


def title_size(style: dict, text: str) -> int:
    """Step the title down for long names so it never overflows the frame.
    Thresholds scale off each style's own ceiling rather than being absolute,
    since a condensed 148px and a serif 112px break at different lengths."""
    ceiling = style["title"]["max_size"]
    n = len(text or "")
    if n <= 14:
        factor = 1.0
    elif n <= 22:
        factor = 0.82
    elif n <= 32:
        factor = 0.66
    elif n <= 46:
        factor = 0.54
    else:
        factor = 0.45
    return max(34, int(ceiling * factor))
