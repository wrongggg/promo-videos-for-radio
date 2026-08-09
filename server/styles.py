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
    # The bundled faces (see FONT_FACES). Nabla is a COLRv1 colour font -- the
    # liquid-chrome 3D look ships inside the glyphs themselves, no CSS trickery
    # -- and Instrument Serif's italic is the flowing editorial voice.
    "chrome": f"'Nabla', 'Archivo Black', {_FALLBACK}, system-ui, sans-serif",
    "flowy": f"'Instrument Serif', 'Playfair Display', Georgia, {_FALLBACK}, serif",
    # The renderer's own set is small and dated -- Inter, Playfair and Bebas
    # were carrying four styles apiece, which is why fourteen styles read as
    # about five. These are bundled for range: contemporary, rounder, heavier,
    # and each one distinct enough that a theme is recognisable by its type
    # alone. All variable except Bagel Fat One, so one file covers every weight
    # a style asks for and nothing gets synthesised.
    "grotesk": f"'Space Grotesk', 'Inter', {_FALLBACK}, system-ui, sans-serif",
    "editorial": f"'Bricolage Grotesque', 'Inter', {_FALLBACK}, system-ui, sans-serif",
    "round": f"'Gabarito', 'Inter', {_FALLBACK}, system-ui, sans-serif",
    "geo": f"'Unbounded', 'Archivo Black', {_FALLBACK}, system-ui, sans-serif",
    "fat": f"'Bagel Fat One', 'Archivo Black', {_FALLBACK}, system-ui, sans-serif",
    "tall": f"'Big Shoulders Display', 'Bebas Neue', {_FALLBACK}, sans-serif",
    "soft": f"'Fraunces', 'Playfair Display', Georgia, {_FALLBACK}, serif",
}

# Fonts the renderer cannot supply itself, shipped as project assets. The rule
# at the top of this file stands: a family outside the renderer's set must not
# be used without a matching @font-face -- these are those @font-faces. Files
# live in server/static/fonts and install_fonts() copies them into each job.
#
# Entries are (family, path, font-style, font-weight). The weight is a range
# for the variable faces: declaring the real range is what lets a style ask for
# 800 and get the drawn 800 rather than a synthesised smear of the 400.
FONT_FACES = {
    "chrome": (("Nabla", "assets/fonts/nabla.woff2", "normal", "400"),),
    "flowy": (("Instrument Serif", "assets/fonts/instrument-serif.woff2", "normal", "400"),
              ("Instrument Serif", "assets/fonts/instrument-serif-italic.woff2", "italic", "400")),
    "grotesk": (("Space Grotesk", "assets/fonts/spacegrotesk.woff2", "normal", "300 700"),),
    "editorial": (("Bricolage Grotesque", "assets/fonts/bricolage.woff2", "normal", "200 800"),),
    "round": (("Gabarito", "assets/fonts/gabarito.woff2", "normal", "400 900"),),
    "geo": (("Unbounded", "assets/fonts/unbounded.woff2", "normal", "200 900"),),
    "fat": (("Bagel Fat One", "assets/fonts/bagelfatone.woff2", "normal", "400"),),
    "tall": (("Big Shoulders Display", "assets/fonts/bigshoulders.woff2", "normal", "100 900"),),
    "soft": (("Fraunces", "assets/fonts/fraunces.woff2", "normal", "100 900"),),
}

_FONT_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "static", "fonts")


def font_face_css(style: dict) -> str:
    """@font-face rules for a style's bundled faces, or '' for renderer fonts.

    Covers the secondary face as well as the headline's: a style whose display
    face is too heavy to set a track title at 30px points `secondary_font` at
    something readable, and that face needs its @font-face too or the small
    type silently falls back.

    font-display: block, because a flash of fallback glyphs on frame 0 would
    bake into the render; better to hold the frame until the face is in."""
    keys = [style["font"]]
    if style.get("secondary_font") and style["secondary_font"] not in keys:
        keys.append(style["secondary_font"])
    seen, out = set(), []
    for key in keys:
        for fam, path, sty, weight in FONT_FACES.get(key) or ():
            if (fam, sty) in seen:
                continue
            seen.add((fam, sty))
            out.append(
                f"@font-face {{ font-family: '{fam}'; src: url('{path}') format('woff2'); "
                f"font-style: {sty}; font-weight: {weight}; font-display: block; }}\n")
    return "".join(out)


def install_fonts(project_dir: str) -> None:
    """Copy the bundled woff2 files into the job, same contract as the Hydra
    vendor bundle: compositions must not fetch from the network."""
    import shutil
    dest = os.path.join(project_dir, "assets", "fonts")
    os.makedirs(dest, exist_ok=True)
    try:
        names = os.listdir(_FONT_SRC_DIR)
    except OSError:
        return
    for name in names:
        if name.endswith(".woff2"):
            shutil.copyfile(os.path.join(_FONT_SRC_DIR, name),
                            os.path.join(dest, name))

WHITE = "#ffffff"
INK = "#0b0b0d"

# GSAP entrance/exit pairs. Each style picks one, so a promo is consistent
# throughout instead of cycling a different animation every scene.
#
# `chars: True` animates the title one character at a time (typing, spinning,
# scattering). compose.py splits the title into <span class="ch"> for those and
# points the tween at the spans instead of the block.
#
# Every value animated here is a transform, opacity, or a blur that resolves to
# zero. Layout properties -- letterSpacing, width, font-size, character count --
# snap to whole device pixels, so under seek-by-frame capture their ease-out
# tails stutter instead of gliding. The linter rejects them outright.
#
# The house motion grammar, applied everywhere at once so the styles read as
# one product:
#   * Arrivals decelerate hard -- expo.out or power4.out -- and land inside a
#     second. The old power1/power3 tails read as easing curves; a 2026 arrival
#     reads as an object stopping.
#   * Block-level entrances resolve out of a soft defocus (filter blur -> 0),
#     the same move the artwork's dissolve already makes. Per-character tweens
#     stay transform-only: a blur forces a compositor layer per glyph, and
#     hosted renders die on the box's envelope, not the composition's taste.
#   * Overshoot is a settle (back.out <= 1.7), never a wobble -- elastic and
#     the -90-degree spin are gone.
#   * Exits are shorter than entrances, accelerate in, and never steal focus.
ENTRANCES = {
    "rise": {
        "from": "{ y: 64, opacity: 0, filter: 'blur(12px)' }",
        "to": "{ y: 0, opacity: 1, filter: 'blur(0px)', duration: 0.9, ease: 'expo.out' }",
        "exit": "{ opacity: 0, y: -30, filter: 'blur(8px)', duration: 0.5, ease: 'power2.in' }",
    },
    "fade": {
        # A focus pull, not a dim: the block is already in place and simply
        # resolves. The 1.02 scale settle is what stops it reading as a JPEG
        # loading in.
        "from": "{ opacity: 0, scale: 1.02, filter: 'blur(14px)' }",
        "to": "{ opacity: 1, scale: 1, filter: 'blur(0px)', duration: 1.05, ease: 'power2.out' }",
        "exit": "{ opacity: 0, filter: 'blur(10px)', duration: 0.55, ease: 'power1.in' }",
    },
    "slide": {
        # The skew is the motion blur's partner: a few degrees of shear on the
        # way in reads as velocity, and it must settle to exactly 0 with the x.
        "from": "{ x: -100, opacity: 0, skewX: 6, filter: 'blur(10px)' }",
        "to": "{ x: 0, opacity: 1, skewX: 0, filter: 'blur(0px)', duration: 0.8, ease: 'expo.out' }",
        "exit": "{ opacity: 0, x: 64, skewX: -4, filter: 'blur(8px)', duration: 0.45, ease: 'power3.in' }",
    },
    "snap": {
        "from": "{ scale: 1.14, opacity: 0, filter: 'blur(10px)' }",
        "to": "{ scale: 1, opacity: 1, filter: 'blur(0px)', duration: 0.55, ease: 'expo.out' }",
        "exit": "{ opacity: 0, scale: 0.97, duration: 0.35, ease: 'power2.in' }",
    },
    "drift": {
        "from": "{ y: 30, opacity: 0, scale: 1.035, filter: 'blur(12px)' }",
        "to": "{ y: 0, opacity: 1, scale: 1, filter: 'blur(0px)', duration: 1.5, ease: 'power3.out' }",
        "exit": "{ opacity: 0, scale: 1.02, filter: 'blur(8px)', duration: 0.55, ease: 'power1.in' }",
    },

    # --- per-character (transform + opacity only -- see note above) ---
    "type": {
        # A hard cut per character with no ease, which is what makes it read as
        # typing rather than fading. Opacity only, so nothing reflows.
        "from": "{ opacity: 0 }",
        "to": "{ opacity: 1, duration: 0.01, ease: 'none', stagger: 0.04 }",
        "exit": "{ opacity: 0, duration: 0.35, ease: 'power1.in' }",
        "chars": True,
    },
    "spin": {
        # Was a -90-degree back.out(2) cartwheel. The 2026 read of "letters
        # spin into place" is a short tumble that settles, radiating out from
        # the centre of the word rather than marching left to right.
        "from": "{ opacity: 0, rotation: -18, y: 34, scale: 0.85 }",
        "to": ("{ opacity: 1, rotation: 0, y: 0, scale: 1, duration: 0.6, "
               "ease: 'back.out(1.7)', stagger: { each: 0.024, from: 'center' } }"),
        "exit": "{ opacity: 0, rotation: 6, y: -20, duration: 0.4, ease: 'power2.in' }",
        "chars": True,
    },
    "flip": {
        # transformPerspective is what makes the rotation read as a board tile
        # turning instead of a letter squashing -- without it GSAP renders
        # rotationX as a flat scaleY.
        "from": "{ opacity: 0, rotationX: -85, y: 12, transformPerspective: 700 }",
        "to": "{ opacity: 1, rotationX: 0, y: 0, duration: 0.7, ease: 'expo.out', stagger: 0.028 }",
        "exit": "{ opacity: 0, rotationX: 50, duration: 0.4, ease: 'power2.in' }",
        "chars": True,
    },
    "scatter": {
        # Offsets come from the character index, not Math.random -- the
        # composition has to render identically on every pass.
        #
        # GSAP takes function-based values per *property*; handing it a function
        # as the whole fromVars object silently does nothing, which is exactly
        # how this first shipped (the letters just appeared in place).
        #
        # The offsets are deliberately tight (under ~60px) -- at the old 11-14x
        # multipliers this was confetti; at these it reads as the word snapping
        # into alignment from a near miss.
        "from": ("{ opacity: 0, scale: 0.92,"
                 " x: function (i) { return ((i * 37) % 17 - 8) * 7; },"
                 " y: function (i) { return ((i * 61) % 13 - 6) * 9; },"
                 " rotation: function (i) { return ((i * 29) % 11 - 5) * 4; } }"),
        "to": "{ opacity: 1, x: 0, y: 0, rotation: 0, scale: 1, duration: 0.65, ease: 'power4.out', stagger: 0.016 }",
        "exit": "{ opacity: 0, scale: 0.95, y: -16, duration: 0.4, ease: 'power2.in' }",
        "chars": True,
    },
    "wave": {
        # The swell: each letter rises with a soft settle, and the stagger
        # itself accelerates (ease on the stagger object) so the cascade gathers
        # like water rather than ticking along at one rate. The old
        # elastic.out(1, 0.6) wobbled three times per letter; one clean
        # overshoot is bigger *and* calmer.
        "from": "{ opacity: 0, y: 46 }",
        "to": ("{ opacity: 1, y: 0, duration: 0.85, ease: 'back.out(1.4)', "
               "stagger: { each: 0.03, ease: 'sine.in' } }"),
        "exit": "{ opacity: 0, y: -22, duration: 0.45, ease: 'power2.in' }",
        "chars": True,
    },
    "stamp": {
        # A press, not a slam: the old 2.4x/-8deg arrival spent most of its
        # travel invisible anyway (opacity ramps with the tween), so all it
        # bought was a lurch on the last frames. 1.35x out of a defocus lands
        # with the same weight and no camp.
        "from": "{ opacity: 0, scale: 1.35, filter: 'blur(10px)' }",
        "to": "{ opacity: 1, scale: 1, filter: 'blur(0px)', duration: 0.45, ease: 'expo.out' }",
        "exit": "{ opacity: 0, scale: 1.05, filter: 'blur(6px)', duration: 0.35, ease: 'power2.in' }",
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
    # A soft-focus cross dissolve: the outgoing sleeve loses definition as it
    # leaves and the incoming one resolves out of a blur, so for a moment the
    # two are genuinely mixed rather than one being laid over the other. Given
    # a longer window than the rest because a dissolve read at 0.55s just looks
    # like a slow fade -- the melt needs time to be seen as a melt.
    #
    # Done with `filter: blur()` rather than an SVG feTurbulence threshold,
    # which is the textbook grain dissolve and would look better: turbulence is
    # recomputed per frame over the full 1080x1920 and is far and away the most
    # expensive filter primitive, and renders here already die on the host's
    # envelope rather than on anything in the composition. A GPU-composited
    # blur costs almost nothing by comparison.
    "dissolve": {"secs": 0.95, "kind": "dissolve"},
    # Blocks: the outgoing sleeve degrades into fewer and fewer pixels and the
    # incoming one resolves back out of them.
    #
    # Real nearest-neighbour downsampling, not a filter imitating it. The
    # artwork is laid out at 1/f of its box and scaled back up by f with
    # `image-rendering: pixelated`, so the browser rasterises *less* than it
    # normally would and the upscale is a GPU blit -- this is the one
    # transition here that is cheaper than playing the clip straight. f steps
    # through a fixed ladder rather than sliding, because pixelation that
    # eases between block sizes reads as a wobble; jumping between them is
    # what makes it read as pixels.
    # 1.15s, not 0.8: the block ladder needs room to be read as a ladder. At
    # 0.8 the whole coarse half went past in under a third of a second and
    # under 20% opacity, so what you actually saw was a flicker, not pixels.
    "pixelate": {"secs": 1.15, "kind": "pixelate"},
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
        "font": "grotesk",
        "primary": {"max_size": 104, "weight": 700, "case": "none", "spacing": -1, "line": 1.06},
        "secondary": {"size": 32, "weight": 600, "case": "uppercase", "spacing": 6},
        "trivia": {"size": 30, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "center", "anchor": "bottom", "bottom_gap": 230,
        "entrance": "rise", "transition": "fade", "patch": "haze", "scrim": "soft",
    },
    "poppy": {
        "label": "Poppy",
        "blurb": "Black type on a bright frosted-glass card, bouncy entrances, saturated colour behind. Loud and friendly.",
        "font": "round",
        "primary": {"max_size": 88, "weight": 800, "case": "none", "spacing": -1, "line": 1.04},
        "secondary": {"size": 29, "weight": 400, "case": "uppercase", "spacing": 5},
        "trivia": {"size": 29, "weight": 500},
        "color": INK,
        # The card is what makes black type safe over arbitrary album art, but
        # a rounded rectangle floating short of three edges reads as pasted on
        # however it is filled. Run it edge to edge instead and off the bottom
        # of the frame, and it stops being a box on a picture and becomes the
        # bottom of the poster. Solid white, no radius: the crop is doing the
        # work the frosting was, so nothing has to fake depth.
        "panel": {"bg": "#ffffff", "pad": "58px 60px", "radius": 0, "bleed": True,
                  "shadow": "0 -18px 60px rgba(0,0,0,0.28)"},
        "align": "left", "anchor": "bottom", "bottom_gap": 220,
        "entrance": "snap", "transition": "zoom", "patch": "kaleid", "scrim": "light",
    },
    "xl": {
        "label": "XL",
        "blurb": "Type as the whole picture. Enormous condensed caps that fill the frame and break mid-word, hard cuts, stark bars.",
        "font": "tall",
        # break_all lets a long name wrap wherever it runs out of frame --
        # the mid-word break IS the look (a magazine masthead cropped by its
        # own page), and it is what buys the extra 30px of size.
        "break_all": True,
        "primary": {"max_size": 240, "weight": 800, "case": "uppercase", "spacing": 1, "line": 0.88},
        "secondary": {"size": 44, "weight": 400, "case": "uppercase", "spacing": 10},
        "trivia": {"size": 32, "weight": 500},
        "color": WHITE, "panel": None,
        # 220 rather than the old 190: with the scene's 60px padding the
        # block's lowest pixel now clears Instagram's ~250px bottom overlay
        # (reply bar) instead of sitting right on its edge.
        "align": "left", "anchor": "bottom", "bottom_gap": 220,
        "entrance": "snap", "transition": "swap", "patch": "bars", "scrim": "heavy",
    },
    "terminal": {
        "label": "Terminal",
        "blurb": "Titles type themselves out character by character in mono, over scrolling bars. Deliberately machine-like.",
        "font": "mono",
        "primary": {"max_size": 84, "weight": 600, "case": "uppercase", "spacing": 0, "line": 1.12},
        "secondary": {"size": 28, "weight": 500, "case": "uppercase", "spacing": 4},
        "trivia": {"size": 29, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "left", "anchor": "bottom", "bottom_gap": 250,
        "entrance": "type", "transition": "swap", "patch": "bars", "scrim": "heavy",
    },
    "kinetic": {
        "label": "Kinetic",
        "blurb": "Every letter spins into place and the covers spin with them. The loudest option here.",
        "font": "fat",
        "primary": {"max_size": 86, "weight": 400, "case": "uppercase", "spacing": 0, "line": 1.04},
        "secondary": {"size": 30, "weight": 400, "case": "uppercase", "spacing": 6},
        "trivia": {"size": 29, "weight": 500},
        "color": WHITE, "secondary_font": "grotesk", "panel": None,
        "align": "center", "anchor": "bottom", "bottom_gap": 235,
        "entrance": "spin", "transition": "spin", "patch": "kaleid", "scrim": "heavy",
    },
    "tidal": {
        "label": "Tidal",
        "blurb": "Letters rise on an elastic swell, one after another, over slow haze. Big but unhurried.",
        "font": "soft",
        "primary": {"max_size": 104, "weight": 500, "case": "none", "spacing": 0, "line": 1.1},
        "secondary": {"size": 30, "weight": 400, "case": "uppercase", "spacing": 9},
        "trivia": {"size": 30, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "center", "anchor": "bottom", "bottom_gap": 240,
        "entrance": "wave", "transition": "fade", "patch": "haze", "scrim": "soft",
    },

    # --- added when the five near-duplicates came out -----------------------
    #
    # Each of these takes one of the entrances the cut styles were using, which
    # is deliberate: Liner Notes, Slow Ambient, Carousel, Flipboard and Confetti
    # were redundant as *looks*, but "slide", "drift", "flip" and "scatter" are
    # motions and there was no reason to lose them. Every entrance in ENTRANCES
    # except the plain "fade" fallback is now on exactly one style.
    "stack": {
        "label": "Stack",
        "blurb": "Artist set enormous and broken over several lines, leading crushed, filling the frame edge to edge. The loudest thing here.",
        "font": "display",
        "primary": {"max_size": 150, "weight": 400, "case": "uppercase", "spacing": -2, "line": 0.86},
        "secondary": {"size": 34, "weight": 400, "case": "uppercase", "spacing": 6},
        "trivia": {"size": 30, "weight": 500},
        "color": WHITE, "panel": None,
        # 220 for the same Instagram bottom-overlay clearance as XL.
        "align": "left", "anchor": "bottom", "bottom_gap": 220,
        "entrance": "scatter", "transition": "zoom", "patch": "kaleid", "scrim": "heavy",
    },
    "masthead": {
        "label": "Masthead",
        "blurb": "One line of condensed caps stretched across the full width and centred, letters flipping into place. A cover line, not a caption.",
        "font": "condensed",
        "primary": {"max_size": 150, "weight": 400, "case": "uppercase", "spacing": 3, "line": 0.94},
        "secondary": {"size": 34, "weight": 400, "case": "uppercase", "spacing": 9},
        "trivia": {"size": 30, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "center", "anchor": "bottom", "bottom_gap": 255,
        "entrance": "flip", "transition": "swap", "patch": "haze", "scrim": "heavy",
    },
    "swiss": {
        "label": "Swiss",
        "blurb": "Restraint as the whole idea: one weight, tight tracking, a lot of empty frame. Lets the sleeve carry it.",
        "font": "editorial",
        "primary": {"max_size": 84, "weight": 500, "case": "none", "spacing": -1.5, "line": 1.1},
        "secondary": {"size": 30, "weight": 400, "case": "uppercase", "spacing": 4},
        "trivia": {"size": 29, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "left", "anchor": "bottom", "bottom_gap": 270,
        "entrance": "slide", "transition": "slide", "patch": "grain", "scrim": "soft",
    },
    "plate": {
        "label": "Plate",
        "blurb": "High-contrast serif in black on a white card, centred and unhurried. The fashion-title register.",
        "font": "serif",
        "primary": {"max_size": 104, "weight": 400, "case": "none", "spacing": 0, "line": 1.12},
        "secondary": {"size": 28, "weight": 400, "case": "uppercase", "spacing": 11},
        "trivia": {"size": 29, "weight": 400},
        "color": INK,
        # Square corners and solid white, unlike Poppy's rounded glass -- a
        # printed plate, not a sticker, and the one card that stays opaque on
        # purpose. Only gains a soft lift off the page.
        "panel": {"bg": "rgba(255,255,255,0.96)", "pad": "58px 62px", "radius": 0,
                  "shadow": "0 30px 80px rgba(0,0,0,0.25)"},
        "align": "center", "anchor": "bottom", "bottom_gap": 215,
        "entrance": "drift", "transition": "fade", "patch": "flow", "scrim": "light",
    },
    "index": {
        "label": "Index",
        "blurb": "Mono type stamped into a hard black block, set like a catalogue card. Minimal without being small.",
        "font": "mono",
        "primary": {"max_size": 80, "weight": 600, "case": "uppercase", "spacing": -0.5, "line": 1.14},
        "secondary": {"size": 30, "weight": 400, "case": "uppercase", "spacing": 5},
        "trivia": {"size": 29, "weight": 400},
        "color": WHITE,
        # No card. Index only ever runs on Gallery, which is a flat white
        # field, and a dark box floating on white is the one thing that read as
        # pasted-on rather than designed -- a rectangle with nothing to frost
        # over and no edge to justify it. What makes a catalogue card is the
        # setting, not the box: mono, ink, hung under a short rule with a lot
        # of white around it. Terminal stays distinct because it sits on
        # artwork under a heavy scrim rather than on a page.
        "panel": None,
        "rule": {"width": 150, "weight": 3, "gap": 30},
        "align": "left", "anchor": "bottom", "bottom_gap": 235,
        "entrance": "stamp", "transition": "swap", "patch": "bars", "scrim": "light",
    },

    # --- the contemporary set ----------------------------------------------
    #
    # Four styles that lean on capabilities the first twelve never used: a
    # bundled colour font, a bundled italic, the artwork inside the letterforms,
    # and a headline that moves instead of arriving.
    "chrome": {
        "label": "Chrome",
        "blurb": "Liquid-metal colour type -- the chrome lives inside the glyphs -- rippling in letter by letter over saturated colour.",
        "font": "chrome",
        "primary": {"max_size": 118, "weight": 400, "case": "uppercase", "spacing": 2, "line": 1.04},
        "secondary": {"size": 30, "weight": 600, "case": "uppercase", "spacing": 6},
        "trivia": {"size": 29, "weight": 500},
        # Nabla carries its own colour; WHITE is only the fallback face's colour.
        # The secondary lines drop to the grotesque -- a colour font at 30px is
        # noise, not chrome.
        "color": WHITE, "panel": None, "secondary_font": "grotesque",
        "align": "center", "anchor": "bottom", "bottom_gap": 230,
        "entrance": "wave", "transition": "zoom", "patch": "kaleid", "scrim": "soft",
    },
    "flux": {
        "label": "Ink",
        "blurb": "Flowing editorial italic, large and unhurried. The literary register -- for sleeves that read like book covers.",
        "font": "flowy",
        "italic": True,
        "primary": {"max_size": 132, "weight": 400, "case": "none", "spacing": 0, "line": 1.02},
        "secondary": {"size": 30, "weight": 400, "case": "uppercase", "spacing": 6},
        "trivia": {"size": 29, "weight": 400},
        "color": WHITE, "panel": None, "secondary_font": "grotesque",
        "align": "center", "anchor": "bottom", "bottom_gap": 230,
        "entrance": "drift", "transition": "dissolve", "patch": "flow", "scrim": "soft",
    },
    "cutout": {
        "label": "Cutout",
        "blurb": "The artist's name cut out of the artwork itself -- the sleeve shows through the letters. Type and image as one object.",
        "font": "geo",
        # compose.py fills the letterforms with the scene's own artwork via
        # background-clip: text; the colour below is only the no-artwork
        # fallback.
        "art_text": True,
        "primary": {"max_size": 150, "weight": 700, "case": "uppercase", "spacing": 0, "line": 0.96},
        "secondary": {"size": 30, "weight": 600, "case": "uppercase", "spacing": 5},
        "trivia": {"size": 29, "weight": 400},
        "color": WHITE, "panel": None, "secondary_font": "grotesque",
        "align": "center", "anchor": "bottom", "bottom_gap": 225,
        "entrance": "fade", "transition": "swap", "patch": "haze", "scrim": "soft",
    },
    # Cover Star's own style. It used to borrow XL, which meant it inherited
    # break_all -- the deliberate mid-word crop -- and on the one layout whose
    # headline hangs over an opaque sleeve the cropped remainder landed behind
    # the artwork. It also wanted a white outline to survive the overlap, which
    # read as a sticker. Filling the letters with the sleeve instead solves
    # both: the type and the picture become the same image, which is the point
    # of hanging one over the other. Same trick as Cutout, different face --
    # Archivo Black's wide counters show more artwork than Unbounded's.
    "coverpiece": {
        "label": "Cover Piece",
        "blurb": "The name cut from the sleeve and hung over it -- letters and picture are one image, neither complete without the other.",
        "font": "display",
        "art_text": True,
        "primary": {"max_size": 170, "weight": 400, "case": "uppercase", "spacing": -2, "line": 0.9},
        "secondary": {"size": 34, "weight": 600, "case": "uppercase", "spacing": 7},
        "trivia": {"size": 29, "weight": 400},
        "color": WHITE, "panel": None, "secondary_font": "grotesk",
        "align": "left", "anchor": "bottom", "bottom_gap": 220,
        "entrance": "fade", "transition": "dissolve", "patch": "haze", "scrim": "soft",
    },
    "reel": {
        "label": "Reel",
        "blurb": "The name as a full-width ticker, sliding through the frame for the whole scene. Motion as the typography.",
        "font": "tall",
        # compose.py duplicates the name into a seamless track and scrolls it
        # by its own width -- no measurement, deterministic at any seek.
        "ticker": True,
        "primary": {"max_size": 185, "weight": 700, "case": "uppercase", "spacing": 2, "line": 0.9},
        "secondary": {"size": 32, "weight": 500, "case": "uppercase", "spacing": 7},
        "trivia": {"size": 29, "weight": 400},
        "color": WHITE, "panel": None,
        "align": "left", "anchor": "bottom", "bottom_gap": 230,
        "entrance": "slide", "transition": "slide", "patch": "bars", "scrim": "heavy",
    },
}

DEFAULT_STYLE = "classic"
STYLE_KEYS = tuple(STYLES.keys())


# --------------------------------------------------------------------------
# Layout -- where the artwork sits
#
# Until this existed there was exactly one answer, and it was a bad one for the
# subject: album art is square, the frame is 9:16, and both media layers were
# 1080x1920 `object-fit: cover`. A square sleeve was therefore scaled 1.78x,
# had 44% of its width cropped off, was drawn at reduced opacity and then put
# under a scrim. The one thing a music promo is about was the one thing you
# could not see -- and with only one layout, every style was the same picture
# with different type on it.
#
# Layout is orthogonal to style: a theme picks one of each. Geometry is quoted
# in real 1080x1920 render pixels rather than percentages, because that is the
# only coordinate system the composition actually has.
#
#   art          the box the sleeve is drawn into. None means the full frame.
#   backdrop     a blown-up, blurred copy of the same image behind it, so the
#                colour reaches the edges without cropping the sleeve itself.
#   field        a flat colour behind everything, by palette.field() mode.
#                Not every layout is tinted -- "white" keeps one genuinely
#                clean, and "complement" sets the sleeve against its opposite
#                so a warm cover does not produce a warm everything.
#   scrim        the old under-text gradient. Only useful when type sits over
#                artwork; over a flat field it just dirties the colour.
#   text_bottom  the frame-pixel line the artist/title block's BOTTOM edge
#                sits on; the block grows upward from it. Bottom-anchored on
#                purpose: Instagram draws its reply bar over roughly the last
#                250px of a story, so anchoring the block's lowest pixel to a
#                line above that zone guarantees clearance no matter how tall
#                the trivia runs -- the old (top, height)+centered box could
#                quietly let a tall block spill into the overlay. None defers
#                to the style's own anchor, which keeps "bleed" as before.
#   text_on_field  whether that block lands on the flat colour rather than on
#                the picture. Decides the type colour -- see _resolve_text.
#   header_band  whether the show/episode header keeps its contrast band.
#                Only layouts whose header actually sits over artwork need it;
#                over a flat field or a dark backdrop wash it reads as a smear.
#   float_art    a slow vertical drift on the sleeve's box, plus rounded
#                corners. Only for boxes inset from the frame edges -- a flush
#                band that moves reveals its own seams.
LAYOUTS = {
    "bleed": {
        "label": "Full bleed",
        "blurb": "Artwork across the whole frame, dimmed, type over the top. Suits dark cinematic covers.",
        "art_rect": None, "art_opacity": 0.88, "backdrop": False, "field": None,
        "scrim": True, "text_bottom": None, "text_on_field": False,
        "header_band": True, "float_art": False,
    },
    "canvas": {
        "label": "Canvas",
        "blurb": "The sleeve whole and sharp, over a blurred blow-up of itself. Nothing cropped.",
        "art_rect": (100, 470, 880, 880),
        "art_opacity": 1.0, "backdrop": True, "field": None,
        "scrim": False, "text_bottom": 1670, "text_on_field": False,
        "header_band": False, "float_art": True,
    },
    "press": {
        "label": "Press",
        "blurb": "Sleeve flush to the top edge, hard cut to clean white below. Type sits in the white.",
        "art_rect": (0, 0, 1080, 1080),
        # White, not a colour derived from the sleeve: the derived field kept
        # reading as a wash of the artwork, and against real covers the clean
        # gallery-print white is simply the stronger page.
        "art_opacity": 1.0, "backdrop": False, "field": "white",
        "scrim": False, "text_bottom": 1660, "text_on_field": True,
        # Art runs to the top edge, so the header sits over the sleeve and
        # keeps its band.
        "header_band": True, "float_art": False,
    },
    "gallery": {
        "label": "Gallery",
        "blurb": "A small sleeve held high on plain white, wide even margins, type far below. Almost all air.",
        # Held high, but not into the header. The header block runs from 250px
        # to roughly 315px, with its scrim fading out by ~360px -- a sleeve at
        # 340px started underneath the episode label and read as one crowded
        # object rather than as air. 430px clears it; the sleeve loses 70px so
        # the gap to the type below survives the move (bottom 980 vs 960).
        "art_rect": (265, 430, 550, 550),
        "art_opacity": 1.0, "backdrop": False, "field": "white",
        "scrim": False, "text_bottom": 1650, "text_on_field": True,
        "header_band": False, "float_art": True,
    },
    "offset": {
        "label": "Bleed off",
        "blurb": "Sleeve oversized and pushed off the right edge. Deliberately off-balance.",
        # Raised and trimmed from (250, 400, 1180): at the old rect the sleeve
        # ran to y1580 and forced the text block down into Instagram's bottom
        # overlay zone. Still pushed well off the right edge.
        "art_rect": (280, 300, 1100, 1100),
        "art_opacity": 1.0, "backdrop": False, "field": "derived",
        "scrim": False, "text_bottom": 1670, "text_on_field": True,
        "header_band": False, "float_art": True,
    },
    "split": {
        "label": "Split",
        "blurb": "One hard horizontal cut. Picture above, a field in the opposite colour below, type filling it.",
        "art_rect": (0, 0, 1080, 1056),
        "art_opacity": 1.0, "backdrop": False, "field": "complement",
        "scrim": False, "text_bottom": 1660, "text_on_field": True,
        "header_band": True, "float_art": False,
    },
    "strip": {
        "label": "Strip",
        "blurb": "The sleeve as a full-width band with colour holding it top and bottom.",
        # A shorter band than the old square (0, 410, 1080, 1080): cropping the
        # sleeve to a letterboxed band is the layout's whole idea, and ending
        # at y1340 buys the text block room above the Instagram overlay zone.
        # White above and below, like a photograph tipped into a book page --
        # the derived tint made it a poster; white makes it a plate.
        "art_rect": (0, 400, 1080, 940),
        "art_opacity": 1.0, "backdrop": False, "field": "white",
        "scrim": False, "text_bottom": 1670, "text_on_field": True,
        "header_band": False, "float_art": False,
    },
    "cover": {
        "label": "Cover",
        "blurb": "The name set enormous above the sleeve and cut out of it -- the letters are the artwork, the artwork is the subject.",
        # The magazine-cover move, readable edition: the headline runs OVER
        # the artwork on its own layer (a first cut drew it underneath, which
        # was handsome and illegible), with a white stroke and soft shadow so
        # ink type reads on the white page and on the sleeve alike. Headline
        # at 420 clears the show/episode header (it ends ~380).
        "art_rect": (140, 680, 800, 800),
        "art_opacity": 1.0, "backdrop": False, "field": "white",
        "scrim": False, "text_bottom": 1660, "text_on_field": True,
        "header_band": False, "float_art": True, "drift_px": 16,
        # Anchored by its BOTTOM edge, 24px above the sleeve, rather than by a
        # fixed top. An art-filled headline is transparent -- it is the sleeve
        # showing through the letterforms -- so any part of it that crosses the
        # sleeve has nothing to contrast against and simply disappears. A fixed
        # top could not prevent that: the block's height depends on how many
        # lines the artist's name takes, so one-word names cleared the artwork
        # and two-line ones lost their second line into it. Growing upward from
        # the sleeve's edge keeps every line on the field whatever the name.
        "headline_overlap": True, "headline_bottom": 656,
    },
}

# Geometry is declared once as numbers and the CSS is generated from it, so the
# picker's thumbnails are drawn from the same rectangles the renderer uses. A
# second hand-written copy of these positions is how a preview starts quietly
# lying about what you are going to get.
for _lay in LAYOUTS.values():
    _r = _lay["art_rect"]
    _lay["art"] = f"left:{_r[0]}px; top:{_r[1]}px; width:{_r[2]}px; height:{_r[3]}px;" if _r else None
    _b = _lay["text_bottom"]
    _lay["text"] = f"bottom:{1920 - _b}px;" if _b else None

DEFAULT_LAYOUT = "bleed"
LAYOUT_KEYS = tuple(LAYOUTS.keys())


def layout(key: str | None) -> dict:
    return LAYOUTS.get(key or "", LAYOUTS[DEFAULT_LAYOUT])


# Stand-in field colours for the picker. The real ones are sampled from each
# sleeve at render time (see palette.field); these only have to show which
# layout tints its ground, which leaves it white, and which flips to the
# opposite hue.
_FIELD_SWATCH = {
    "derived": "#122c3a",
    "complement": "#3a2012",
    "white": "#f4f4f0",
    "ink": "#0b0b0d",
    None: "#0a0a0f",
}


def layout_thumb(key: str) -> str:
    """A 120x213 diagram of where the sleeve and the type land.

    Drawn from art_rect / text_rect, so it cannot drift from the composition.
    Deliberately a diagram and not a rendering: what a layout decides is
    position, and a tiny picture of a cover would hide that behind the cover."""
    lay = LAYOUTS.get(key, LAYOUTS[DEFAULT_LAYOUT])
    W, H = 120, 213
    sx, sy = W / 1080, H / 1920
    field = _FIELD_SWATCH.get(lay.get("field"), "#0a0a0f")
    light = lay.get("field") == "white"
    parts = [f'<rect width="{W}" height="{H}" fill="{field}"/>']

    if lay.get("backdrop"):
        # The blurred blow-up, as a soft wash rather than an edge.
        parts.append(f'<rect width="{W}" height="{H}" fill="url(#lgb{key})"/>')

    r = lay["art_rect"]
    if r is None:
        parts.append(f'<rect width="{W}" height="{H}" fill="url(#lga{key})" opacity="0.85"/>')
    else:
        x, y, w, h = r[0] * sx, r[1] * sy, r[2] * sx, r[3] * sy
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                     f'fill="url(#lga{key})"/>')

    # Two bars for the artist/title lockup, always together -- they are one
    # block in every layout, never split across the artwork. text_bottom is
    # the block's bottom line, so the bars stack up from just above it.
    bar = "#111214" if light else "#ffffff"
    if lay["text_bottom"]:
        ty = lay["text_bottom"] * sy - 24
    else:
        ty = H - 42
    parts.append(f'<rect x="9" y="{ty:.1f}" width="58" height="7" rx="1" fill="{bar}" opacity="0.92"/>')
    parts.append(f'<rect x="9" y="{ty + 11:.1f}" width="34" height="4" rx="1" fill="{bar}" opacity="0.6"/>')

    defs = (
        f'<defs>'
        f'<linearGradient id="lga{key}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="#8f9aa8"/><stop offset="100%" stop-color="#2b3038"/>'
        f'</linearGradient>'
        f'<radialGradient id="lgb{key}" cx="50%" cy="45%" r="70%">'
        f'<stop offset="0%" stop-color="#5c6470"/><stop offset="100%" stop-color="#14161a"/>'
        f'</radialGradient>'
        f'</defs>'
    )
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
            f'height="{H}" role="img" aria-label="{lay["label"]} layout">{defs}{"".join(parts)}</svg>')


def layout_choices() -> list[dict]:
    return [{"key": k, "label": v["label"], "blurb": v["blurb"], "thumb": layout_thumb(k)}
            for k, v in LAYOUTS.items()]


def get(key: str | None) -> dict:
    return STYLES.get(key or "", STYLES[DEFAULT_STYLE])


# Two invented tracks, one per cover. The hand-over swaps both together.
PREVIEW_TRACKS = (("Afterglow", "SABLE"), ("Halcyon", "MIRA VOSS"))


def _title_spans(title: str, per_char: bool) -> str:
    """Title markup. Per-character entrances need one span per character, but
    characters are grouped into nowrap word spans first: every character being
    an inline-block gives the line a break opportunity between each pair, so a
    two-word title could split mid-word."""
    if not per_char:
        return f'<span class="tp-ch tp-ch0">{title}</span>'
    words = []
    n = 0
    for word in title.split(" "):
        chars = "".join(
            f'<span class="tp-ch tp-ch{(n + j) % 6}">{c}</span>'
            for j, c in enumerate(word)
        )
        n += len(word)
        words.append(f'<span class="tp-word">{chars}</span>')
    return '<span class="tp-sp"> </span>'.join(words)


def thumbnail_markup(key: str, palette: dict | None = None,
                     style_key: str | None = None, layout_key: str | None = None) -> str:
    """The full preview tile: generated SVG for the backdrop and covers, with a
    real-type text block laid over it -- one block per cover, so the words swap
    when the picture does."""
    st = get(style_key or key)
    svg = thumbnail_svg(key, palette, style_key, layout_key)
    per_char = bool(ENTRANCES[st["entrance"]].get("chars"))

    # Scale the real type to the tile (112px wide against a 1080px frame), then
    # clamp. XL's 210px would land at 24px here and run straight off the edge,
    # so the ceiling matters more than the ratio -- the tile shows which
    # typeface and how it moves, not the true size.
    t_px = max(11, min(16, round(st["primary"]["max_size"] * 0.115)))
    a_px = max(6, min(9, round(st["secondary"]["size"] * 0.20)))

    blocks = ""
    for blk, (title, artist) in zip(("a", "b"), PREVIEW_TRACKS):
        blocks += (
            f'<span class="tp-txt tp-txt-{blk} tp-{key}">'
            f'<span class="tp-title" style="font-size:{t_px}px">'
            f'{_title_spans(artist, per_char)}</span>'
            f'<span class="tp-artist" style="font-size:{a_px}px">{title}</span>'
            f'</span>'
        )
    return svg + blocks


def preview_layout_css(themes: dict | None = None) -> str:
    """Per-theme positioning and typography for the preview text block."""
    # The bundled faces, served from /static/fonts so the tiles show the real
    # chrome and the real italic rather than their fallbacks. The declared
    # weight has to be the face's real range or a tile asking for 800 gets a
    # synthesised bold and stops matching what renders.
    out = [
        f"@font-face {{ font-family: '{fam}'; "
        f"src: url('/static/fonts/{os.path.basename(path)}') format('woff2'); "
        f"font-style: {sty}; font-weight: {weight}; font-display: swap; }}"
        for faces in FONT_FACES.values() for fam, path, sty, weight in faces
    ]
    out += [
        ".theme-thumb { position: relative; }",
        ".tp-txt { position: absolute; left: 0; right: 0; display: flex;"
        " flex-direction: column; padding: 0 8px; pointer-events: none;"
        " overflow: hidden; }",
        # Both track blocks occupy the same slot; the loop decides which shows.
        # Track B is hidden at rest -- including its card background, or a
        # panel style would show an empty white card over track A.
        ".tp-txt-b { opacity: 0; }",
        ".tp-sp { display: inline; }",
        ".tp-word { display: inline-block; white-space: nowrap; }",
        ".tp-title { display: block; line-height: 1.02; }",
        ".tp-ch { display: inline-block; will-change: transform, opacity; }",
        ".tp-artist { display: block; opacity: .75; margin-top: 3px; letter-spacing: 1px; }",
    ]
    for key, theme in (themes or {}).items():
        st = get(theme.get("style"))
        lay = layout(theme.get("layout"))
        font = FONTS[st["font"]]
        # Which ground the type lands on is the layout's call, not the style's
        # -- exactly as in the rendered frame (see resolve_text). Getting this
        # wrong here would show white-on-white in the picker for a white-type
        # style over a white field.
        colour, panel_spec, _ = resolve_text(st, lay, lay.get("field") == "white")
        ink = colour == INK
        align = "center" if st["align"] == "center" else "flex-start"
        # The block sits where the layout puts it, scaled from render pixels to
        # the tile; only full bleed falls back to the style's own anchor. A
        # bottom anchor here mirrors the composition's bottom-anchored block.
        if lay["text_bottom"]:
            pos = f"bottom: {(1920 - lay['text_bottom']) / 1920 * 100:.1f}%;"
        else:
            pos = "top: 46%;" if st["anchor"] == "center" else "bottom: 12%;"
        if panel_spec:
            # Glass cards preview as glass -- translucent with a small real
            # backdrop blur -- so the tile doesn't promise an opaque slab.
            if panel_spec.get("glass"):
                card = "rgba(255,255,255,.66)" if ink else "rgba(10,10,16,.5)"
                glass = " backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);"
            else:
                card = "rgba(255,255,255,.95)" if ink else "rgba(9,9,12,.92)"
                glass = ""
            panel = (f" background: {card}; border-radius: {min(panel_spec['radius'], 6)}px;"
                     " padding: 6px 8px; margin: 0 6px;" + glass)
        else:
            panel = ""
        shadow = "" if ink else " text-shadow: 0 1px 4px rgba(0,0,0,.8);"
        case = "uppercase" if st["primary"]["case"] == "uppercase" else "none"
        out.append(
            f".tp-{key} {{ font-family: {font}; color: {colour}; {pos}"
            f" align-items: {align}; text-align: {st['align']};"
            f" font-weight: {st['primary']['weight']}; text-transform: {case};"
            f"{shadow}{panel} }}"
        )
    return "\n".join(out)


def choices(themes: dict | None = None) -> list[dict]:
    """What the picker in the UI renders, thumbnail included.

    One entry per *theme*, not per style: a theme is the whole decision -- type,
    layout and palette together -- and it is the only thing the user is asked to
    make. Style and layout remain separate vocabularies inside this module so
    they can be composed without duplicating geometry, but that is authoring,
    not a second control to hand over.

    No blurb: the tile animates what the theme does, which says it better than a
    sentence. The blurbs stay in STYLES because the custom-theme prompt still
    needs them to describe each style to the model (see curator.STYLE_MENU)."""
    return [
        {"key": k,
         "label": v.get("label", k.title()),
         "thumb": thumbnail_markup(k, (v.get("palettes") or [{}])[0],
                                   v.get("style"), v.get("layout"))}
        for k, v in (themes or {}).items()
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
# The loop shows two covers and TWO hand-overs -- A to B at a quarter of the
# way through and B back to A at three quarters -- so it genuinely rotates
# rather than playing once and snapping. The first version had a single
# hand-over: track B arrived at ~34% and then sat there for the remaining
# two-thirds of the loop before the whole thing cut back to track A, which read
# as a preview that had stalled and then glitched.
#
# Every window below is symmetric about that: each cover holds the screen for
# the same span, and the state at 100% is by construction the state at 0%, so
# there is no seam to see when the loop wraps.
PREVIEW_SECS = 4.6

# Where each cover sits when it is not the one on screen: `in` is the pose it
# animates FROM on the way in, `out` the pose it animates TO on the way out.
# Both hand-overs are built from this one pair, so a cover always leaves the
# way the next one arrives -- a slide always travels the same direction, a zoom
# always pushes through rather than alternating in and out.
_COVER_STATES = {
    "fade":  {"in": "opacity: 0; transform: none;",
              "out": "opacity: 0; transform: none;"},
    # Slide is a parallax hand-over in the renderer: the incoming cover crosses
    # the stage while the outgoing one gives way at part speed, both fading.
    "slide": {"in": "opacity: 0; transform: translateX(55%) scale(1.04);",
              "out": "opacity: 0; transform: translateX(-35%);"},
    "zoom":  {"in": "opacity: 0; transform: scale(1.3); filter: blur(4px);",
              "out": "opacity: 0; transform: scale(0.84); filter: blur(3px);"},
    "spin":  {"in": "opacity: 0; transform: rotate(-8deg) scale(0.82);",
              "out": "opacity: 0; transform: rotate(6deg) scale(0.88);"},
    # A hard cut: no interpolation, so the steps() timing does the work.
    "swap":  {"in": "opacity: 0; transform: none;",
              "out": "opacity: 0; transform: none;"},
    "dissolve": {"in": "opacity: 0; transform: scale(1.09); filter: blur(4px);",
                 "out": "opacity: 0; transform: scale(0.97); filter: blur(4px);"},
    # The renderer pixelates by laying the artwork out small and scaling it
    # back up with nearest-neighbour sampling, which a keyframe on an SVG group
    # cannot do. The tile approximates it with a stepped scale-and-cut instead:
    # honest about the rhythm -- blocky, abrupt -- without claiming the exact
    # texture at 120px wide.
    "pixelate": {"in": "opacity: 0; transform: scale(1.14);",
                 "out": "opacity: 0; transform: scale(1.14);"},
}
_COVER_ON = "opacity: 1; transform: none;"

# A cover parked off-stage has to get from its exit pose back to its entry pose
# before its next turn. steps(1, end) on that one segment makes the reset a cut
# instead of a tween -- without it "slide" spends a third of the loop drifting
# from translateX(-100%) back to translateX(100%), straight across the tile, in
# the wrong direction.
_SNAP = " animation-timing-function: steps(1, end);"

# How the text bars arrive. Char-based entrances get a per-bar delay so the
# preview reads as a stagger rather than a single move.
# The entry state of each entrance, as bare declarations. Kept without a
# `from {}` wrapper so it can be placed at any point in a keyframe list -- the
# second track has to perform its entrance partway through the loop, not at 0%.
_TEXT_FROM = {
    "rise":    "opacity: 0; transform: translateY(12px); filter: blur(4px);",
    "fade":    "opacity: 0; filter: blur(4px);",
    "slide":   "opacity: 0; transform: translateX(-22px) skewX(6deg); filter: blur(3px);",
    "snap":    "opacity: 0; transform: scale(1.14); filter: blur(3px);",
    "drift":   "opacity: 0; transform: translateY(8px) scale(1.035); filter: blur(4px);",
    "type":    "opacity: 0; transform: scaleX(0);",
    "spin":    "opacity: 0; transform: rotate(-14deg) translateY(9px) scale(0.85);",
    "flip":    "opacity: 0; transform: perspective(300px) rotateX(-85deg);",
    "scatter": "opacity: 0; transform: translate(-8px, 7px) rotate(-7deg);",
    "wave":    "opacity: 0; transform: translateY(14px);",
    "stamp":   "opacity: 0; transform: scale(1.3); filter: blur(3px);",
}
# Both rest states declare filter so a blur in an entry pose interpolates from
# a defined value instead of jumping when the keyframe list mixes the two.
_SETTLED = "opacity: 1; transform: none; filter: blur(0px);"
_GONE = "opacity: 0; transform: none; filter: blur(0px);"

_STAGGERED = {"type", "spin", "flip", "scatter", "wave"}


def thumbnail_css(themes: dict | None = None) -> str:
    """Hover-preview CSS for every theme, emitted once into the page.

    Animations are only attached on hover, so nothing runs until the user points
    at a card -- eighteen looping previews playing at once would be noise, and
    on a long list it would burn battery for no reason."""
    out = [
        # Transforms on SVG children need a box to resolve percentages and a
        # sane origin, or translateX(100%) means something unexpected.
        ".theme-thumb svg .tp-cover {"
        " transform-box: fill-box; transform-origin: center; }",
        ".theme-thumb svg .tp-b { opacity: 0; }",
    ]
    for key, theme in (themes or {}).items():
        st = get(theme.get("style"))
        # The theme's own transition wins, exactly as it does in the render --
        # otherwise two themes sharing a style would preview identically while
        # one of them cuts and the other dissolves.
        tkey = theme.get("transition") or st.get("transition", "fade")
        kind = TRANSITIONS.get(tkey, TRANSITIONS["fade"])["kind"]
        cover = _COVER_STATES.get(kind, _COVER_STATES["fade"])
        c_in, c_out = cover["in"], cover["out"]
        ent = st["entrance"]
        # Both the hard cut and the block transition move in steps rather than
        # sliding between states.
        ease = "steps(1, end)" if kind == "swap" else (
            "steps(4, end)" if kind == "pixelate" else "cubic-bezier(.4,0,.2,1)")
        # The out-expo curve (fast arrival, long settle) that the composition's
        # expo.out entrances use; the preview should decelerate the same way.
        t_ease = "steps(1, end)" if ent == "type" else "cubic-bezier(.16,1,.3,1)"

        # Covers. A holds 82%->18% (across the wrap), hands over 18-32%, is
        # parked 32-67%, resets at 67-68% and comes back 68-82%. B is the same
        # cycle half a loop out of phase, so the two just keep trading places.
        out.append(
            f"@keyframes tpa-{key} {{ 0%,18% {{ {_COVER_ON} }} 32% {{ {c_out} }}"
            f" 67% {{ {c_out}{_SNAP} }} 68% {{ {c_in} }}"
            f" 82%,100% {{ {_COVER_ON} }} }}"
        )
        out.append(
            f"@keyframes tpb-{key} {{ 0% {{ {c_out} }} 17% {{ {c_out}{_SNAP} }}"
            f" 18% {{ {c_in} }} 32%,68% {{ {_COVER_ON} }}"
            f" 82%,100% {{ {c_out} }} }}"
        )

        frm = _TEXT_FROM.get(ent, _TEXT_FROM["fade"])
        # Text, on the same two hand-overs but sequenced rather than crossed:
        # each track's exit finishes exactly where the other's entrance starts
        # (25% and 75%), because a style with a card paints that card from the
        # block's own background -- two blocks on screen at once means one
        # card sitting over the other's words.
        #
        # The 73%/23% keyframes exist to hold GONE right up to the reset. The
        # jump from there to `frm` happens at opacity 0, so the entrance still
        # starts from its proper pose without that pose being visible first.
        out.append(
            f"@keyframes tpta-{key} {{ 0%,15% {{ {_SETTLED} }} 25%,73% {{ {_GONE} }}"
            f" 75% {{ {frm} }} 85%,100% {{ {_SETTLED} }} }}"
        )
        out.append(
            f"@keyframes tptb-{key} {{ 0%,23% {{ {_GONE} }} 25% {{ {frm} }}"
            f" 35%,65% {{ {_SETTLED} }} 75%,100% {{ {_GONE} }} }}"
        )

        # Visibility of the whole block, not just its text -- see the card note
        # above. Stepped, so exactly one of the two is ever on: A owns 75%->25%
        # across the wrap, B owns 25%->75%.
        out.append(f"@keyframes tpva-{key} {{ 0%,24% {{ opacity: 1; }}"
                   f" 25%,74% {{ opacity: 0; }} 75%,100% {{ opacity: 1; }} }}")
        out.append(f"@keyframes tpvb-{key} {{ 0%,24% {{ opacity: 0; }}"
                   f" 25%,74% {{ opacity: 1; }} 75%,100% {{ opacity: 0; }} }}")

        # Two triggers: :hover for the mouse, a `.previewing` class for keyboard
        # focus and touch, where there is no hover state at all.
        #
        # Selectors are built as explicit lists, never by concatenating another
        # selector string. Doing that once produced a stray sibling combinator
        # that matched at HIGHER specificity than the per-character delay rules,
        # so its `animation` shorthand reset every delay to 0s and the stagger
        # silently never happened.
        def rule(suffix: str, body: str, k=key) -> str:
            sels = [f'.theme-card[data-mode="preset:{k}"]{trig} {suffix}'
                    for trig in (":hover", ".previewing")]
            return ", ".join(sels) + " { " + body + " }"

        out.append(rule(".theme-thumb svg .tp-a",
                        f"animation: tpa-{key} {PREVIEW_SECS}s {ease} infinite;"))
        out.append(rule(".theme-thumb svg .tp-b",
                        f"animation: tpb-{key} {PREVIEW_SECS}s {ease} infinite;"))

        # Title characters and the artist line both animate, each on the loop
        # belonging to its own cover -- animating only the title looked
        # half-finished, and a preview that swaps the picture while keeping the
        # words reads as a glitch.
        for blk, kf in (("a", f"tpta-{key}"), ("b", f"tptb-{key}")):
            out.append(rule(f".tp-txt-{blk}",
                            f"animation: tpv{blk}-{key} {PREVIEW_SECS}s steps(1, end) infinite;"))
            out.append(rule(f".tp-txt-{blk} .tp-ch",
                            f"animation: {kf} {PREVIEW_SECS}s {t_ease} infinite;"))
            out.append(rule(f".tp-txt-{blk} .tp-artist",
                            f"animation: {kf} {PREVIEW_SECS}s cubic-bezier(.16,1,.3,1) infinite;"))

        if ent in _STAGGERED:
            # On a looping animation a delay shifts the phase, which still reads
            # as a stagger without desyncing a character from its own cover.
            for n in range(6):
                out.append(rule(f".tp-txt .tp-ch{n}",
                                f"animation-delay: {0.05 * n:.2f}s;"))
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
    """The two cover files, `cover-a`/`cover-b` preferred.

    Those two names are the committed, already-downscaled pair that ships with
    the repo, so production has covers without anyone remembering to upload
    anything. Any other images in the folder are still picked up (drop in a
    replacement and it works), but the canonical names win so the result does
    not depend on how a generated filename happens to sort."""
    try:
        names = sorted(
            n for n in os.listdir(PREVIEW_IMAGE_DIR)
            if os.path.splitext(n)[1].lower() in _PREVIEW_EXTS
        )
    except OSError:
        return []
    canonical = [n for n in names if os.path.splitext(n)[0].lower() in ("cover-a", "cover-b")]
    chosen = canonical if len(canonical) == 2 else names
    return [os.path.join(PREVIEW_IMAGE_DIR, n) for n in chosen[:2]]


# Anything at or under this is already tile-sized and is served as-is. It also
# keeps production off ffmpeg entirely for the committed pair -- if the resize
# failed there, the tiles would silently drop back to the generated art.
_PREVIEW_PASSTHROUGH_BYTES = 400 * 1024


def _ensure_thumb(src: str) -> str | None:
    """Downscaled copy of `src`, generated once and reused.

    Best-effort: if ffmpeg isn't usable the caller falls back to the generated
    artwork rather than serving a 8MB PNG into the picker."""
    try:
        if os.path.getsize(src) <= _PREVIEW_PASSTHROUGH_BYTES:
            return src
    except OSError:
        return None

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
    rel = os.path.relpath(thumb, os.path.join(os.path.dirname(PREVIEW_IMAGE_DIR)))
    return "/static/" + rel.replace(os.sep, "/")


def _cover_art(variant: int, a1: str, a2: str, accent: str, key: str,
               W: int | float = 120, H: int | float = 213) -> str:
    """A stand-in album cover.

    Used only until real cover images are dropped into static/preview -- see
    preview_image(). Deliberately abstract: overlapping washes of colour with
    grain and a vignette, the shapes a sleeve tends to use. No figure: a
    silhouette read as a stock portrait rather than as album art, which is the
    opposite of what a cover looks like.

    Two variants so a hand-over swaps to a different image, not to itself.

    Sized to whatever box the layout gives it -- a framed layout draws this
    into a square a fraction of the tile, not across the whole thing, so every
    coordinate is a fraction of W/H rather than a tile pixel."""
    v = "A" if variant == 0 else "B"
    if variant == 0:
        forms = (
            f'<circle cx="{W*.50:.1f}" cy="{H*.41:.1f}" r="{min(W, H)*.37:.1f}" fill="{accent}" opacity="0.85"/>'
            f'<circle cx="{W*.50:.1f}" cy="{H*.41:.1f}" r="{min(W, H)*.37:.1f}" fill="url(#cvA{key})" opacity="0.45"/>'
            f'<path d="M0 {H*.62:.1f} Q{W*.50:.1f} {H*.49:.1f} {W:.1f} {H*.65:.1f} L{W:.1f} {H:.1f} L0 {H:.1f} Z" fill="{a2}" opacity="0.55"/>'
        )
    else:
        forms = (
            f'<path d="M0 0 L{W:.1f} 0 L{W:.1f} {H*.55:.1f} L0 {H*.29:.1f} Z" fill="{a2}" opacity="0.9"/>'
            f'<circle cx="{W*.62:.1f}" cy="{H*.62:.1f}" r="{min(W, H)*.35:.1f}" fill="{accent}" opacity="0.75"/>'
            f'<rect x="0" y="{H*.70:.1f}" width="{W:.1f}" height="{H*.047:.1f}" fill="{a1}" opacity="0.8"/>'
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
        return (f'<image href="{src}" x="0" y="0" width="{W:.1f}" height="{H:.1f}" '
                f'preserveAspectRatio="xMidYMid slice"/>'
                f'<rect width="{W:.1f}" height="{H:.1f}" fill="url(#vig{key})"/>')
    return _cover_art(variant, a1, a2, accent, key, W, H)


def thumbnail_svg(key: str, palette: dict | None = None,
                  style_key: str | None = None, layout_key: str | None = None) -> str:
    """A 120x213 (9:16) preview of one theme.

    `key` identifies the theme (and namespaces the SVG ids); the style and
    layout it is built from are passed separately. Two themes can share a
    typographic style and differ only in where the sleeve sits, so the layout
    has to be visible here or half the picker would be pairs of identical
    tiles.

    Layered exactly like the rendered frame: synth patch at the back, artwork
    over it, scrim on top, and the text laid over that as HTML (see
    thumbnail_markup). Generated from the style definition rather than drawn by
    hand, so a change to palette, patch or scrim shows up in the picker without
    anyone remembering to update an image.

    There are no placeholder bars any more -- the preview shows real type, so a
    stand-in for it was just clutter sitting under the actual words."""
    st = get(style_key or key)
    lay = layout(layout_key)
    pal = palette or {"orb1": "#7b5cff", "orb2": "#00d4ff", "accent": "#ff2e88"}
    a1 = pal.get("orb1", "#7b5cff")
    a2 = pal.get("orb2", "#00d4ff")
    accent = pal.get("accent", "#ff2e88")
    W, H = 120, 213
    heavy = st["scrim"] == "heavy"

    # The sleeve's box, in tile space, straight off the layout's own rectangle.
    r = lay["art_rect"]
    if r is None:
        cx, cy, cw, ch = 0.0, 0.0, float(W), float(H)
    else:
        cx, cy = r[0] * W / 1080, r[1] * H / 1920
        cw, ch = r[2] * W / 1080, r[3] * H / 1920
    framed = r is not None

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
        f'<radialGradient id="bd{key}" cx="50%" cy="42%" r="75%">'
        f'<stop offset="0%" stop-color="{a1}" stop-opacity="0.55"/>'
        f'<stop offset="100%" stop-color="#0a0a0f" stop-opacity="0.95"/>'
        f'</radialGradient>'
        f'<linearGradient id="g{key}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#000" stop-opacity="{0.5 if heavy else 0.25}"/>'
        f'<stop offset="40%" stop-color="#000" stop-opacity="0.10"/>'
        f'<stop offset="100%" stop-color="#000" stop-opacity="{0.92 if heavy else 0.78}"/>'
        f'</linearGradient>'
        # Clips the pair of covers to the layout's box. It has to sit on a
        # wrapper rather than on the covers themselves: the hand-over animation
        # owns their `transform`, so the box cannot also be expressed there.
        f'<clipPath id="cl{key}">'
        f'<rect x="{cx:.2f}" y="{cy:.2f}" width="{cw:.2f}" height="{ch:.2f}"/>'
        f'</clipPath>'
        f'</defs>'
    )

    ground = _FIELD_SWATCH.get(lay.get("field"), "#0a0a0f")
    parts = [f'<rect width="{W}" height="{H}" fill="{ground}"/>']

    if lay.get("backdrop"):
        # Stands in for the blurred blow-up: colour reaching the edges with the
        # sleeve itself left uncropped.
        parts.append(f'<rect width="{W}" height="{H}" fill="url(#bd{key})"/>')

    parts += [
        # Two covers: .tp-a on screen, .tp-b arriving. thumbnail_css keeps
        # .tp-b hidden until the card is previewing. A real cover image if one
        # has been added, otherwise the generated stand-in.
        f'<g clip-path="url(#cl{key})"><g transform="translate({cx:.2f},{cy:.2f})">'
        f'<g class="tp-cover tp-a" opacity="0.95">{_cover_layer(0, a1, a2, accent, key, cw, ch)}</g>'
        f'<g class="tp-cover tp-b" opacity="0.95">{_cover_layer(1, a1, a2, accent, key, cw, ch)}</g>'
        f'</g></g>',
    ]
    if not framed:
        # The synth patch and the scrim belong to full-bleed only. Over a
        # printed colour field they are exactly the wash those layouts exist to
        # replace -- and screening a patch across a flat ground just dirties it.
        parts += [
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


def resolve_text(style: dict, lay: dict, field_is_light: bool) -> tuple[str, dict | None, bool]:
    """(colour, panel, needs_shadow) for the artist/title block.

    A style carries a text colour and the layout carries a background, and the
    two can contradict each other: Swiss is white type, Gallery is a white
    field, and white-on-white is not a look. Whichever the user picked, the
    pair has to resolve to something readable, so the layout wins wherever the
    type has left the artwork.

    Over a flat field the shadow and the panel both go too. Both exist to hold
    type apart from unpredictable imagery; on a solid colour the shadow only
    smears it and a card is a card sitting on nothing."""
    if not lay.get("text_on_field"):
        return style["color"], style.get("panel"), True

    # A card is part of a style's identity -- Poppy, Plate and Index are their
    # cards -- so it only comes off when the field has made it pointless. Which
    # way a card runs is told by the style's text colour: white type means a
    # dark card, ink type means a light one.
    panel = style.get("panel")
    dark_card = bool(panel) and style["color"] == WHITE
    if field_is_light:
        if dark_card:
            # A dark glass card has nothing to frost over a flat light field --
            # it renders as a grey slab. Swap it for an elevated paper card:
            # same geometry, ink type, a hairline edge and a soft drop so it
            # reads as a card lying on the page rather than a box printed on it.
            paper = {**panel, "bg": "rgba(255,255,255,0.92)",
                     "border": "1px solid rgba(11,11,13,0.10)",
                     "shadow": "0 24px 60px rgba(0,0,0,0.14)"}
            paper.pop("glass", None)
            return INK, paper, False
        return INK, None, False
    if panel:
        return style["color"], panel, False
    # No card, and ink type would vanish into a dark field.
    return WHITE, None, False


FRAME_HEIGHT = 1920


def _pad_parts(pad: str) -> tuple[float, float]:
    """(vertical, horizontal) from a CSS shorthand like "58px 60px"."""
    parts = [float(p.rstrip("px")) for p in pad.split()]
    return (parts[0], parts[1] if len(parts) > 1 else parts[0])


def _bleed_clearance(lay: dict) -> float:
    """How far a bleed band's text must sit above the frame's bottom edge.

    The layout already decided that: `text_bottom` is where it wants the
    block's lowest pixel, chosen to clear Instagram's reply overlay. A band
    runs to the crop, so that offset becomes the band's bottom padding rather
    than the block's own bottom offset, and the type does not move."""
    return FRAME_HEIGHT - lay["text_bottom"] if lay.get("text_bottom") else 250


def _overlap_anchor(lay: dict) -> str:
    """Where an overlapping headline is pinned. Bottom-anchored when the layout
    gives a `headline_bottom`, so the block grows upward and its lowest line
    lands in the same place no matter how many lines the name takes."""
    if lay.get("headline_bottom"):
        return f"bottom: {FRAME_HEIGHT - lay['headline_bottom']}px;"
    return f"top: {lay.get('headline_top', 250)}px;"


def text_css(style: dict, lay: dict | None = None, field_is_light: bool = False) -> str:
    """CSS for the text roles. Sizes for the title are a ceiling -- compose.py
    steps it down for long titles."""
    lay = lay or LAYOUTS[DEFAULT_LAYOUT]
    font = FONTS[style["font"]]
    # Display faces that only work at headline size (a colour font, a ticker
    # slab) hand their secondary lines to a quieter family.
    sec_font = FONTS[style.get("secondary_font", style["font"])]
    # The artist name is the promo's headline and the track title sits under
    # it, in every theme. Someone scrolling recognises the artist first -- it is
    # how a gig poster or a festival lineup is set -- and the hierarchy is a
    # product decision, so it does not vary per theme.
    primary, secondary, trivia = style["primary"], style["secondary"], style["trivia"]
    color, panel, needs_shadow = resolve_text(style, lay, field_is_light)
    align = style["align"]
    items = "center" if align == "center" else "flex-start"

    # Black type gets no shadow (it sits on its own light panel); white type
    # gets a soft one so it survives a bright patch of artwork. Over a flat
    # colour field neither applies -- see resolve_text.
    shadow = "0 4px 26px rgba(0,0,0,0.72)" if (needs_shadow and color != INK) else "none"

    if panel:
        # Optional glass keys: `glass` is a backdrop-filter list (the blur /
        # saturate / brightness that does the contrast work the old opaque
        # fills did), `border` a hairline that catches light on the pane's
        # edge, `shadow` the lift off the artwork.
        extras = ""
        if panel.get("glass"):
            extras += (f"backdrop-filter: {panel['glass']}; "
                       f"-webkit-backdrop-filter: {panel['glass']}; ")
        if panel.get("border"):
            extras += f"border: {panel['border']}; "
        if panel.get("shadow"):
            extras += f"box-shadow: {panel['shadow']}; "
        if panel.get("bleed"):
            # A band, not a card: it spans the frame and runs off the bottom
            # edge. The layout's own bottom offset moves from the block to the
            # band's inner padding, so the type still lands exactly where the
            # layout put it -- clear of Instagram's reply zone -- while the
            # white behind it carries on past the crop.
            clearance = _bleed_clearance(lay)
            pad_v, pad_h = _pad_parts(panel["pad"])
            panel_css = (
                f"background: {panel['bg']}; "
                f"padding: {pad_v:g}px {pad_h:g}px {clearance:g}px; "
                f"{extras}width: 100%; max-width: none; border-radius: 0; opacity: 0;"
            )
        else:
            panel_css = (
                f"background: {panel['bg']}; padding: {panel['pad']}; "
                f"border-radius: {panel['radius']}px; {extras}max-width: 900px; opacity: 0;"
            )
    else:
        panel_css = ""

    # A short rule hung above the block, for styles that dropped their card and
    # need something to hang the type off. Drawn as ::before on the headline
    # rather than on .meta-inner, and deliberately so: a pseudo-element cannot
    # be a GSAP target, but it inherits its parent's opacity -- and the
    # headline is what the entrance actually tweens. Hung off .meta-inner the
    # rule would snap in with the scene and sit there while the type animated
    # underneath it.
    rule = style.get("rule")
    rule_css = ""
    if rule and not panel:
        rule_css = f"""
.artist-name::before {{
  content: ""; display: block; width: {rule['width']}px; height: {rule['weight']}px;
  background: {color}; margin-bottom: {rule['gap']}px; opacity: 0.9;
}}"""

    # The layout places the block whenever it has an opinion; only "bleed"
    # leaves it to the style, which is what keeps that layout pixel-identical
    # to what shipped before any of this existed.
    if lay.get("text") and panel and panel.get("bleed"):
        # The band is the thing that reaches the edges, so the container keeps
        # no padding of its own and sits on the crop.
        position_css = ("position: absolute; left: 0; right: 0; margin: 0; "
                        "padding: 0; bottom: 0;")
    elif lay.get("text"):
        # Absolute against .scene, which means .scene's 60px padding no longer
        # applies and has to be restated here or the type runs to the bleed.
        # Anchored by its bottom edge (lay["text"] is a `bottom:` rule) so the
        # block grows upward and its lowest pixel stays out of Instagram's
        # bottom overlay zone whatever the content height.
        position_css = ("position: absolute; left: 0; right: 0; margin: 0; "
                        "padding: 0 60px; " + lay["text"])
    elif style["anchor"] == "center":
        position_css = "justify-content: center; margin: 0;"
    else:
        position_css = f"margin-top: auto; margin-bottom: {style['bottom_gap']}px;"

    # Per-capability extras on the headline. Italic is a voice, break-all is
    # the XXL mid-word crop, art-fill masks the letterforms with the scene's
    # own artwork (the image itself is set inline per scene by compose.py).
    headline_extras = ""
    if style.get("italic"):
        headline_extras += " font-style: italic;"
    # break_all styles get their word-break per scene from compose.py --
    # headline_fit decides whether this particular name should break at all.

    extra_blocks = ""
    if style.get("art_text"):
        # Two defences keep a pale sleeve legible (letters filled from a
        # mostly-white cover on a white field would otherwise vanish): a thin
        # stroke that draws every glyph's edge, and a ::before duplicate of
        # the text (via data-text) that paints a soft dark halo BEHIND the
        # filled letters. The halo cannot be a text-shadow on the element
        # itself -- that paints over the clipped image -- and cannot be a
        # filter, which the entrance blur tween would overwrite.
        extra_blocks += """
.artist-name.art-fill {
  position: relative;
  -webkit-background-clip: text; background-clip: text; color: transparent;
  background-size: cover; background-position: center; text-shadow: none;
  -webkit-text-stroke: 2px rgba(11,11,13,0.35);
}
.artist-name.art-fill::before {
  content: attr(data-text); position: absolute; inset: 0; z-index: -1;
  color: transparent; -webkit-text-stroke: 0;
  text-shadow: 0 4px 30px rgba(11,11,13,0.5), 0 1px 5px rgba(11,11,13,0.3);
}"""
    if style.get("ticker"):
        # The wrap escapes its container's 60px padding so the track runs edge
        # to edge; the track's two halves are identical, which is what makes an
        # xPercent scroll need no measurement.
        extra_blocks += """
.ticker-wrap { overflow: hidden; white-space: nowrap; width: 1080px; margin: 0 -60px; }
.ticker-track { display: inline-block; white-space: nowrap; }
.ticker-track .tick-sep { opacity: 0.4; padding: 0 42px; }"""
    if lay.get("headline_overlap"):
        # The headline lives on its own layer OVER the artwork (see
        # compose.py); this places it. z-index 3 clears the sleeve's box at 2,
        # and the white stroke + soft shadow keep ink type readable both on
        # the white field and across the artwork it overhangs.
        extra_blocks += f"""
.headline-overlap {{
  position: absolute; left: 0; right: 0; {_overlap_anchor(lay)}
  z-index: 3; padding: 0 60px; text-align: {align};
}}
.headline-overlap .artist-name {{
  -webkit-text-stroke: 7px #f4f4f0; paint-order: stroke fill;
  text-shadow: 0 8px 44px rgba(0,0,0,0.22);
}}
/* An art-filled headline brings its own stroke and halo (see .art-fill) and
   is transparent by definition, so the heavy white outline above would ring
   it in cream and defeat the point. */
.headline-overlap .artist-name.art-fill {{
  -webkit-text-stroke: 2px rgba(11,11,13,0.35);
  text-shadow: none;
}}"""

    return f"""
.meta-container {{
  position: relative; z-index: 10; width: 100%;
  display: flex; flex-direction: column; align-items: {items};
  text-align: {align}; {position_css}
}}
.meta-inner {{ display: flex; flex-direction: column; align-items: {items}; {panel_css} }}{rule_css}
.artist-name {{
  font-family: {font}; font-weight: {primary['weight']};
  text-transform: {_case_css(primary['case'])}; letter-spacing: {primary['spacing']}px;
  line-height: {primary['line']}; color: {color}; text-shadow: {shadow};
  margin-bottom: 10px; max-width: 950px;{headline_extras}
}}
.track-title {{
  font-family: {sec_font}; font-size: {secondary['size']}px; font-weight: {secondary['weight']};
  text-transform: {_case_css(secondary['case'])}; letter-spacing: {secondary['spacing']}px;
  color: {color}; text-shadow: {shadow}; margin-bottom: 14px; opacity: 0.92;
}}
.trivia-tag {{
  font-family: {sec_font}; font-size: {trivia['size']}px; font-weight: {trivia['weight']};
  line-height: 1.4; color: {color}; text-shadow: {shadow};
  letter-spacing: 0.2px; max-width: 840px; margin-bottom: 14px; opacity: 0.88;
}}
.scrim {{ background: {SCRIMS[style['scrim']]}; }}{extra_blocks}
"""


# Mean glyph advance as a fraction of font-size, per face, as (lowercase,
# uppercase). Measured off a real render -- A-Z and a-z, ink extent read back
# out of the PNG -- rather than estimated, because none of these faces are
# installed on the machine that composes the HTML: they are either supplied by
# the renderer or bundled as woff2, so nothing here can measure them locally.
#
# Each face is measured at the heaviest weight any style actually sets on it,
# not at 400. On a variable face that is not a rounding difference -- Big
# Shoulders goes 0.34 -> 0.45 between 400 and 800, a third wider -- and sizing
# the headline off the thin instance would let it overflow the frame.
#
# Re-measure whenever FONTS or a style's primary weight changes. 'mono' landing
# on ~0.60 for both cases is the check that the real faces loaded rather than a
# fallback, since IBM Plex Mono is a fixed 0.6em advance.
_ADVANCE = {
    "grotesque": (0.53, 0.67),
    "mono": (0.59, 0.60),
    "serif": (0.50, 0.64),
    "condensed": (0.29, 0.35),
    "display": (0.61, 0.76),
    "chrome": (0.51, 0.57),
    "flowy": (0.39, 0.47),
    "grotesk": (0.55, 0.63),
    "editorial": (0.54, 0.64),
    "round": (0.51, 0.63),
    "geo": (0.73, 0.88),
    "fat": (0.52, 0.66),
    "tall": (0.41, 0.45),
    "soft": (0.54, 0.71),
}

# .artist-name's own max-width, and the binding constraint in every layout: the
# 1080px frame less 60px of padding a side leaves 960.
_HEADLINE_MAX_WIDTH = 950
# .meta-inner's cap, which a panel style's padding eats into.
_PANEL_MAX_WIDTH = 900
# The advance figures are an alphabet mean, so a name built from wide glyphs
# ("WOMBAT MUMMY") runs over it. Give the estimate room instead of letting a
# name land a few pixels too wide and wrap to a second line anyway.
_FIT_MARGIN = 0.94
MIN_HEADLINE_SIZE = 34


def _headline_width(style: dict) -> float:
    """Pixels available to one line of the headline in this style."""
    panel = style.get("panel")
    if not panel:
        return _HEADLINE_MAX_WIDTH
    parts = (panel.get("pad") or "0").split()
    horizontal = parts[1] if len(parts) > 1 else parts[0]
    return min(_HEADLINE_MAX_WIDTH,
               _PANEL_MAX_WIDTH - 2 * float(horizontal.rstrip("px")))


def _text_width(style: dict, text: str, size: int) -> float:
    """Estimated rendered width of `text` set in `style` at `size`."""
    lower, upper = _ADVANCE[style["font"]]
    uppercased = style["primary"]["case"] == "uppercase"
    total = sum(size * (upper if uppercased or ch.isupper() else lower) for ch in text)
    return total + max(0, len(text) - 1) * style["primary"]["spacing"]


def headline_size(style: dict, text: str) -> int:
    """Step the headline down for long names so it never overflows the frame.

    The headline is the artist, so this has more work to do than it did for
    track titles -- collaborations like "Local Artist & Joey G ii & Klein Zage"
    are far longer than most track names.
    Thresholds scale off each style's own ceiling rather than being absolute,
    since a condensed 148px and a serif 112px break at different lengths."""
    ceiling = style["primary"]["max_size"]
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
    size = max(MIN_HEADLINE_SIZE, int(ceiling * factor))

    # Character count alone is the wrong measure for the one case that actually
    # overflows. "Fleetwood Mac" is 13 characters, so the table above leaves it
    # at the style's full ceiling -- but the headline may only wrap at a space,
    # and "FLEETWOOD" at Stack's 150px display is wider than the frame. No
    # amount of wrapping fixes that: the line either runs off the edge or the
    # browser goes looking for a break inside the word.
    #
    # So the longest word sets a hard floor on how large the type may be, and
    # the answer to a word that does not fit is to shrink it until it does.
    #
    # Two styles opt out of the floor on purpose: break_all styles WANT the
    # browser to break inside the word (the mid-word crop is the look), and a
    # ticker never wraps at all -- its line scrolls instead of fitting.
    if style.get("break_all") or style.get("ticker"):
        return size
    longest = max((text or "").split(), key=len, default="")
    if longest:
        available = _headline_width(style) * _FIT_MARGIN
        while size > MIN_HEADLINE_SIZE and _text_width(style, longest, size) > available:
            size -= 2
    return size


def headline_fit(style: dict, text: str) -> tuple[int, bool]:
    """(size, break_mid_word) for XXL break-capable styles.

    A mid-word break at a size chosen for fitting reads as a mistake --
    "FLEETWOOD M / AC" -- so the break is never a fallback, it is a choice
    made against three ordered preferences:

      1. One line at the ceiling: no break at all.
      2. Break at the spaces with each word filling its line, at whatever
         size that allows -- often LARGER than the ceiling ("FLEETWOOD" /
         "MAC" at ~300px). A word-boundary break at enormous size is the
         poster look, and it costs nothing when the words happen to fit.
      3. Only when a single word cannot fill a line at near-ceiling size,
         break mid-word -- and jump to 1.32x the ceiling when doing it, so
         the crop is unmistakably deliberate.

    Non-break styles just get headline_size."""
    if not style.get("break_all"):
        return headline_size(style, text), False
    ceiling = style["primary"]["max_size"]
    text = (text or "").strip()
    available = _headline_width(style) * _FIT_MARGIN
    if not text or _text_width(style, text, ceiling) <= available:
        return ceiling, False
    longest = max(text.split(), key=len, default="")
    unit = _text_width(style, longest, 100) / 100 or 1.0
    word_size = available / unit
    if word_size >= ceiling * 0.92:
        return int(min(word_size, ceiling * 1.45)), False
    return int(ceiling * 1.32), True
