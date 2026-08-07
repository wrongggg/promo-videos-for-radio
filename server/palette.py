"""Field colour pulled from the release artwork.

Several of the layouts in `styles.LAYOUTS` sit the sleeve on a flat colour
field rather than bleeding it across the whole frame, and that field has to
come from somewhere. Sampling the artwork is the only source that is free,
needs no API and cannot disagree with what is on screen.

Two things this deliberately does NOT do:

  * It does not average the image. Averaging a record sleeve gives mud --
    measured on the two bundled preview covers, the means are #453f3b and
    #386c82, which are the same brown-grey nothing you would get from any
    other cover. The hue survives averaging; the chroma does not.
  * It does not add a dependency. ffmpeg is already required (static-ffmpeg,
    and media_finder shells out to it for every clip), and one downscale call
    returns the nine pixels this needs.

Instead: downscale to 3x3, take the most colourful of the nine cells, keep its
hue and throw the rest away by forcing saturation and lightness to a target.
That is what turns a muddy sample into a field a title can sit on.

Deterministic, which the frame-seeking renderer requires: the same file always
produces the same nine pixels, and results are cached per (path, mode) so a
scene shown twice never re-shells.
"""
import colorsys
import os
import subprocess

# Fallbacks for when there is no artwork at all, or ffmpeg fails. A neutral
# slate rather than a colour, so a failed sample never looks like a choice.
FALLBACK_HUE = 0.58

# What each mode targets, as (saturation, lightness) in HLS.
_DEEP = (0.52, 0.15)
_MID = (0.46, 0.32)

_cache: dict[tuple[str, str], dict] = {}


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _grid(path: str) -> list[tuple[int, int, int]]:
    """Nine pixels: the artwork downscaled to 3x3. Empty list on any failure --
    a colour we could not sample must cost the promo its tint, not its render."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-vf", "scale=3:3",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=20,
        ).stdout
    except Exception:
        return []
    if len(out) < 27:
        return []
    return [tuple(out[i * 3:i * 3 + 3]) for i in range(9)]


def _colourfulness(rgb) -> float:
    """Saturation weighted by how far the cell is from black or white.

    Plain HLS saturation is not enough on its own: a near-black pixel can
    report a high saturation while carrying no visible colour at all, and those
    cells are exactly what the dark corners of a sleeve are full of."""
    h, l, s = colorsys.rgb_to_hls(*[c / 255 for c in rgb])
    return s * (1 - abs(2 * l - 1))


def _hue_of(path: str | None) -> float:
    cells = _grid(path) if path else []
    if not cells:
        return FALLBACK_HUE
    vivid = max(cells, key=_colourfulness)
    if _colourfulness(vivid) < 0.04:
        # A genuinely monochrome sleeve. Its "hue" is noise, so don't tint from
        # it -- a black-and-white cover should not come out faintly green.
        return FALLBACK_HUE
    return colorsys.rgb_to_hls(*[c / 255 for c in vivid])[0]


def _graded(hue: float, sat: float, lum: float) -> str:
    return _hex([c * 255 for c in colorsys.hls_to_rgb(hue, lum, sat)])


def field(image_path: str | None, mode: str = "derived") -> dict:
    """The colour field for one scene.

    Returns {"bg", "ink", "accent"} where `ink` is the text colour that belongs
    on `bg` -- callers must use it rather than assuming white, because two of
    the modes produce a light field.

    Modes:
      derived    -- the artwork's own hue, deepened.
      complement -- the hue opposite it. Sits the sleeve against its contrast
                    rather than its echo; on a warm cover this is what stops
                    every promo being one colour throughout.
      white      -- no tint at all. The clean option, and the right one when
                    the artwork is already busy.
      ink        -- near-black, for when the sleeve should be the only colour.
    """
    key = (image_path or "", mode)
    if key in _cache:
        return _cache[key]

    if mode == "white":
        out = {"bg": "#f4f4f0", "ink": "#111214", "accent": "#111214"}
    elif mode == "ink":
        out = {"bg": "#0b0b0d", "ink": "#ffffff", "accent": "#ffffff"}
    else:
        hue = _hue_of(image_path)
        if mode == "complement":
            hue = (hue + 0.5) % 1.0
        out = {
            "bg": _graded(hue, *_DEEP),
            "ink": "#ffffff",
            "accent": _graded(hue, 0.82, 0.58),
        }
        out["mid"] = _graded(hue, *_MID)

    _cache[key] = out
    return out


def is_light(mode: str) -> bool:
    """Whether a mode paints a field that needs dark type on it. Used by the
    composer to flip the scrim and the title colour together -- getting one and
    not the other is how white text ends up on a white card."""
    return mode == "white"
