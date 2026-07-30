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


def choices(palettes: dict | None = None) -> list[dict]:
    """What the picker in the UI renders, thumbnail included.

    `palettes` maps a style key to the palette that theme actually ships with,
    so each thumbnail previews its own colours rather than a shared stand-in --
    which is most of what makes the five readable at a glance."""
    palettes = palettes or {}
    return [
        {"key": k, "label": v["label"], "blurb": v["blurb"],
         "thumb": thumbnail_svg(k, palettes.get(k))}
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
        ".theme-thumb svg .tp-cover, .theme-thumb svg .tp-bar {"
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
        # Two triggers, not just :hover -- a `.previewing` class means the
        # preview also works for keyboard focus or a tap on touch (where there
        # is no hover at all), and makes the behaviour testable.
        base = f'.theme-card[data-mode="preset:{key}"]'
        sel = f'{base}:hover .theme-thumb svg, {base}.previewing .theme-thumb svg'
        out.append(
            f"{sel} .tp-a {{ animation: tpa-{key} {PREVIEW_SECS}s {ease} infinite; }}"
            f"{sel} .tp-b {{ animation: tpb-{key} {PREVIEW_SECS}s {ease} infinite; }}"
            f"{sel} .tp-bar {{ animation: tpt-{key} 0.5s {t_ease} both; }}"
        )
        if ent in _STAGGERED:
            for n in range(1, 4):
                delay = f"animation-delay: {0.07 * n:.2f}s;"
                out.append(f"{base}:hover .theme-thumb svg .tp-bar{n} {{ {delay} }}"
                           f"{base}.previewing .theme-thumb svg .tp-bar{n} {{ {delay} }}")
    return "\n".join(out)


def thumbnail_svg(key: str, palette: dict | None = None) -> str:
    """A 120x213 (9:16) inline SVG preview of one style.

    Generated from the style definition itself rather than drawn by hand, so a
    change to type, alignment, colour or patch shows up in the picker without
    anyone remembering to update an image. Inline SVG also keeps the picker
    free of binary assets and network requests."""
    s = get(key)
    pal = palette or {"orb1": "#7b5cff", "orb2": "#00d4ff", "accent": "#ff2e88"}
    a1, a2 = pal.get("orb1", "#7b5cff"), pal.get("orb2", "#00d4ff")
    W, H = 120, 213

    left = s["align"] == "left"
    x = 12 if left else W // 2
    anchor = "start" if left else "middle"

    # Title bars stand in for the type: their height tracks the style's actual
    # title size, which is what makes XL obviously XL in the picker.
    t_h = max(6, round(s["title"]["max_size"] * 0.075))
    a_h = max(3, round(s["artist"]["size"] * 0.10))
    bar_w = 96 if left else 88
    body_y = H // 2 - 18 if s["anchor"] == "center" else H - 34 - t_h * 2 - a_h

    def bar(bx, by, bw, bh, fill, op=1.0, r=1, cls=""):
        extra = f' class="tp-bar {cls}"' if cls else ""
        return (f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="{r}" '
                f'fill="{fill}" opacity="{op}"{extra}/>')

    panel = s.get("panel")
    ink = s["color"] == INK
    text_fill = "#0b0b0d" if ink else "#ffffff"

    # Two stacked "covers". Statically only .tp-a shows (.tp-b is opacity 0 in
    # thumbnail_css); on hover they cross over using the style's real
    # transition, which is the whole point of the preview.
    parts = [
        f'<rect width="{W}" height="{H}" fill="#0a0a0f"/>',
        f'<g class="tp-cover tp-a">{_patch_thumb(s["patch"], a1, a2)}</g>',
        f'<g class="tp-cover tp-b">{_patch_thumb(s["patch"], a2, a1)}</g>',
        # scrim
        f'<rect width="{W}" height="{H}" fill="url(#g{key})"/>',
    ]

    px = 8 if left else (W - bar_w) // 2 - 6
    if panel:
        parts.append(
            f'<rect x="{px}" y="{body_y - 12}" width="{bar_w + 12}" '
            f'height="{t_h * 2 + a_h + 34}" rx="7" fill="#ffffff" opacity="0.95"/>'
        )

    tx = 14 if left else (W - bar_w) // 2
    parts += [
        bar(tx, body_y, bar_w, t_h, text_fill, 0.95, cls="tp-bar1"),
        bar(tx, body_y + t_h + 5, round(bar_w * 0.66), t_h, text_fill, 0.95, cls="tp-bar2"),
        bar(tx, body_y + t_h * 2 + 14, round(bar_w * 0.42), a_h, text_fill, 0.7, cls="tp-bar3"),
    ]

    grad = (
        f'<defs><linearGradient id="g{key}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#000" stop-opacity="{0.5 if s["scrim"] == "heavy" else 0.25}"/>'
        f'<stop offset="40%" stop-color="#000" stop-opacity="0.10"/>'
        f'<stop offset="100%" stop-color="#000" stop-opacity="{0.9 if s["scrim"] == "heavy" else 0.7}"/>'
        f'</linearGradient></defs>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" aria-label="{s["label"]} preview">'
        f'{grad}{"".join(parts)}</svg>'
    )


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
