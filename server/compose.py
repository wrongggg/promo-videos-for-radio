import html
import json
import math
import os
import re
import subprocess

import languages
import palette as palette_mod
import styles
import visuals

OUTRO_DURATION = 5
# The tool's mark, carried on the closing card of every promo. Written WITH the
# suffix here -- the page header drops it because the name is unambiguous in
# context there, but on someone else's feed this is the whole attribution and
# ".mov" is what makes it read as a product rather than a word. Always dir=ltr:
# a Hebrew promo still spells the mark left to right.
WORDMARK = "onrepeat.mov"
# The operator's own mark. Only used when the requesting session is the
# operator; every other job either uses an uploaded logo or shows none.
DEFAULT_LOGO_PATH = "server/static/kz-logo.png"

# The composition is written to PROJECT_DIR/index.html and loaded from there,
# so media has to be referenced relative to that directory -- the same way
# DEFAULT_LOGO_PATH already is. Absolute filesystem paths in a src attribute resolve
# against the document's origin and 404.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pinned_hyperframes_version() -> str:
    """The renderer version, read from package.json rather than duplicated here.

    The pin exists so this project re-renders identically over time, and
    `npx hyperframes@latest upgrade --project .` is how it moves -- but that
    command only rewrites package.json. A second copy of the version in this
    file is a copy the upgrade silently leaves behind, which is how the server
    ends up rendering on a version nobody chose. There is no fallback default
    on purpose: a pin we cannot read is a bug to fix at startup, not to paper
    over with a stale guess."""
    with open(os.path.join(PROJECT_DIR, "package.json")) as f:
        scripts = json.load(f).get("scripts") or {}
    match = re.search(r"hyperframes@(\d+\.\d+\.\d+)", scripts.get("render", ""))
    if not match:
        raise RuntimeError(
            "No pinned hyperframes version in package.json's render script. "
            "Expected something like: npx --yes hyperframes@0.7.88 render"
        )
    return match.group(1)


HYPERFRAMES_VERSION = _pinned_hyperframes_version()


def _rel(path: str) -> str:
    """Project-relative form of an absolute media path, left untouched if it
    already is relative or lives outside the project."""
    if not path:
        return path
    if not os.path.isabs(path):
        return path
    try:
        rel = os.path.relpath(path, PROJECT_DIR)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel


def _abs(path: str | None) -> str | None:
    """The filesystem path for a media src that has already been made
    composition-relative. palette.field has to shell out to ffmpeg against a
    real file, and by the time the composer runs, every src is relative to
    PROJECT_DIR rather than to the process's cwd -- which differs between local
    dev and the container, where gunicorn runs with --chdir server."""
    if not path or os.path.isabs(path):
        return path
    return os.path.join(PROJECT_DIR, path)

BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body {
  margin: 0; width: 1080px; height: 1920px; overflow: hidden;
  background: #000; color: #fff; font-family: 'Inter', system-ui, -apple-system, sans-serif;
}
#root { position: relative; width: 1080px; height: 1920px; overflow: hidden; background: #050508; }
.scene {
  position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
  display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 60px;
  overflow: hidden; z-index: 5;
}
.bg-media {
  position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
  object-fit: cover; z-index: 0; opacity: 0.55; transform-origin: center center;
}

/* Transforms don't apply to inline boxes, so per-character spans have to be
   inline-block. will-change keeps the staggered transforms on the compositor. */
.artist-name .ch { display: inline-block; will-change: transform, opacity; }
/* Adjacent inline-blocks are a break opportunity, so without this the headline
   breaks between letters rather than between words -- see _split_chars. An
   unsplit headline needs no equivalent: plain text already breaks only at
   spaces, and forcing word-break here would stop CJK wrapping at all. */
.artist-name .word { display: inline-block; white-space: nowrap; }
.scrim { position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; z-index: 1; }
.orb { position: absolute; border-radius: 50%; filter: blur(120px); opacity: 0; z-index: 2; }
.orb-a { width: 620px; height: 620px; top: 10%; left: 6%; }
.orb-b { width: 520px; height: 520px; bottom: 10%; right: 6%; }
/* 250px, not the 110px this started at, because these promos are posted to
   Instagram Stories and Instagram draws its own chrome over the top of the
   frame -- the progress bar, then the avatar/username/timestamp row, which
   together reach roughly 130px into a 1920px-tall video. At 110px the show
   name sat directly under the username and was half unreadable. 250px is
   Instagram's own documented top safe area, and it matches .outro-brand's
   13% (~250px), so the header and the closing card now agree. */
.header {
  position: absolute; top: 0; left: 0; width: 1080px; z-index: 30;
  padding: 250px 60px 0;
  /* Own stacking context, so the ::before below can sit behind the header text
     with z-index: -1 and still stay in front of the artwork underneath. */
  isolation: isolate;
}
.header-inner {
  display: flex; align-items: flex-start; justify-content: space-between; width: 100%;
  opacity: 0; position: relative;
}
/* The header's own scrim. At the old 110px the scene scrim (0.35 alpha at the
   top edge, ramping to 0.10 by 38%) was doing this job; at 250px it is down to
   ~0.27 and white type over a bright album cover stopped clearing the 4.5:1
   contrast floor. This band is sized to the header rather than the frame, and
   it hangs off #header-inner so it fades in and out on the same tween the text
   does -- painted on .header instead, it would still be sitting over the top
   of the closing card after the header has gone.

   0.64 at the top rather than 0.42, because 0.42 was sized against a mid-tone
   cover and the worst case is a white one: 0.42 black over white leaves the
   band at ~#949494, and white type on that measures 3.0:1 against the same
   4.5:1 floor. 0.64 brings it to ~#5c5c5c and 4.6:1, which holds for any
   sleeve at all. It costs nothing on the covers this was tuned for -- a dark
   band over a dark cover was already invisible, and the only layouts that
   draw it are the ones whose header genuinely sits over artwork. */
.header-inner::before {
  content: ""; position: absolute; z-index: -1; pointer-events: none;
  left: -60px; right: -60px; top: -250px; bottom: -46px;
  background: linear-gradient(180deg, rgba(0,0,0,0.64) 0%, rgba(0,0,0,0.52) 55%,
              rgba(0,0,0,0.30) 84%, rgba(0,0,0,0) 100%);
}
/* Layouts whose header does not sit over artwork drop the band entirely --
   over a flat field or a dark backdrop wash it reads as a smear across the
   top of a clean layout. styles.LAYOUTS decides via header_band. */
.no-headband .header-inner::before { display: none; }
.header-show {
  font-size: 32px; font-weight: 900; letter-spacing: 2px; text-transform: uppercase;
  text-shadow: 0 3px 14px rgba(0,0,0,0.85);
}
/* The 0.7 opacity this used to carry was already failing the contrast check
   (3.5:1 against a 4.5:1 floor) over busy artwork, and dropping the header to
   250px made it marginally worse -- lower down the scrim is lighter, since it
   ramps from 0.35 at the top edge to 0.10 at 38%. 0.9 plus a tighter, darker
   shadow keeps the label subordinate to the show name without relying on
   transparency to do it. */
.header-episode {
  font-size: 19px; font-weight: 600; letter-spacing: 3px; text-transform: uppercase;
  opacity: 0.9; margin-top: 4px;
  text-shadow: 0 2px 8px rgba(0,0,0,0.95), 0 3px 16px rgba(0,0,0,0.8);
}
.header-logo { height: 32px; opacity: 0.9; filter: brightness(0) invert(1) drop-shadow(0 3px 10px rgba(0,0,0,0.7)); }




/* Thinner than the original 8px -- a hairline reads as chrome, a bar reads
   as a widget -- with a faint glow so the accent colour registers at 6px. */
.progress-container { width: 320px; height: 6px; background: rgba(255,255,255,0.16); border-radius: 3px; overflow: hidden; margin-top: 30px; }
.progress-bar { width: 100%; height: 100%; transform-origin: left center; transform: scaleX(0); box-shadow: 0 0 14px rgba(255,255,255,0.35); }
.outro-brand {
  position: absolute; top: 13%; left: 0; width: 1080px; z-index: 10;
  text-align: center; display: flex; flex-direction: column; align-items: center;
}
.outro-logo {
  height: 80px; opacity: 0; filter: brightness(0) invert(1) drop-shadow(0 4px 16px rgba(0,0,0,0.7));
  margin-bottom: 22px;
}
.outro-show {
  font-size: 68px; font-weight: 900; text-transform: uppercase; letter-spacing: 3px;
  opacity: 0; text-shadow: 0 4px 24px rgba(0,0,0,0.8);
}
.outro-episode {
  font-size: 26px; font-weight: 600; letter-spacing: 4px; text-transform: uppercase;
  opacity: 0; margin-top: 10px; color: rgba(255,255,255,0.7); text-shadow: 0 3px 14px rgba(0,0,0,0.8);
}
.outro-meta {
  position: relative; z-index: 10; width: 100%;
  display: flex; flex-direction: column; align-items: center; text-align: center;
  margin-top: auto; margin-bottom: 130px;
}
.outro-title {
  font-size: 72px; font-weight: 900; text-transform: uppercase; letter-spacing: 4px;
  text-align: center; margin-bottom: 32px; color: #fff;
  text-shadow: 0 4px 24px rgba(0,0,0,0.7);
}
.also-featuring-list {
  font-size: 30px; font-weight: 600; line-height: 1.9; text-align: center; color: rgba(255,255,255,0.9);
  max-width: 880px; margin-bottom: 44px;
}
.cta-text {
  font-size: 30px; font-weight: 600; letter-spacing: 6px; text-transform: uppercase;
  color: #fff; padding-bottom: 12px;
  border-bottom: 1px solid rgba(255,255,255,0.45);
  text-shadow: 0 2px 12px rgba(0,0,0,0.55);
}
/* The tool's own mark, on the closing card only. It sits in the lowest slot of
   .outro-meta, which is a flex column with `margin-top: auto` -- so adding it
   pushes the CTA and the credits UP rather than moving anything down toward
   the frame edge, and the card's existing balance survives.
   Deliberately never uppercased: the mark is lowercase, and a theme whose
   secondary case is uppercase would otherwise shout it. 26px against the CTA's
   30px, at 0.7 opacity -- present and legible at a glance, junior to
   everything above it. */
.outro-wordmark {
  font-size: 26px; font-weight: 500; letter-spacing: 1px; text-transform: none;
  color: #fff; opacity: 0; margin-top: 38px;
  text-shadow: 0 2px 10px rgba(0,0,0,0.5);
}
/* Below the text (.scene is z-index 5), above the artwork and synth. A
   vignette or grain is a lens effect on the imagery; sitting it on top of the
   type darkened the very thing it needs to keep legible, and the layout check
   correctly flagged the titles as occluded. */
.frame-overlay { position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; z-index: 4; pointer-events: none; }
.frame-grain {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  opacity: 0.05; mix-blend-mode: overlay;
}
.frame-vignette-heavy { background: radial-gradient(ellipse at center, rgba(0,0,0,0) 40%, rgba(0,0,0,0.65) 100%); }
.frame-glow-frame { box-shadow: inset 0 0 0 14px var(--frame-accent, #fff), inset 0 0 90px 20px var(--frame-accent, #fff); opacity: 0.4; }

/* --- layout layers ------------------------------------------------------
   The flat colour a layout sits its sleeve on. Below the artwork and above
   the synth canvas, so a tinted layout reads as a printed ground rather than
   as a wash over a generative backdrop. */
.art-field { position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; z-index: 1; }
/* A blown-up, heavily blurred copy of the same sleeve, so colour reaches the
   frame edges without the sleeve itself being cropped to get there. Blurred
   hard on purpose: at a gentler radius a cover with a pale border -- which is
   most of them -- reads as a second rectangle floating behind the first
   instead of as colour. */
.art-backdrop {
  position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
  object-fit: cover; z-index: 1; transform: scale(1.6);
  filter: blur(130px) saturate(1.4) brightness(0.78);
}
/* Darkens the backdrop and nothing else. The ordinary .scrim lives inside
   .scene, which sits above every media layer, so using it here would dim the
   sharp sleeve as well -- the one thing this layout exists to show. Same
   z-index as the backdrop and emitted after it, so it paints over the blur but
   under the .art-box at z-index 2. Without it the track title lands on
   whatever the blurred cover happens to be doing and measures 2.9:1. */
.art-wash {
  position: absolute; top: 0; left: 0; width: 1080px; height: 1920px; z-index: 1;
  background: linear-gradient(180deg, rgba(0,0,0,0.32) 0%, rgba(0,0,0,0.12) 38%,
              rgba(0,0,0,0.74) 100%);
}
/* The box a layout draws the sleeve into. Clips the Ken Burns push, which is
   written on the <img> inside by the visuals runtime every frame. */
.art-box { position: absolute; overflow: hidden; z-index: 2; }
/* On a light field the header's white type and its dark scrim band are both
   wrong -- the band reads as a smear across the top of a clean layout. */
.light-frame .header-inner::before { display: none; }
.light-frame .header-show, .light-frame .header-episode { color: #111214; text-shadow: none; }
.light-frame .header-logo { filter: none; opacity: 0.85; }
"""

# Motion presets control how much the orbs drift and how strong the Ken
# Burns/video zoom push is -- the visible "energy" of a theme.
MOTION_STYLES = {
    "calm": {"speed_mult": 1.7, "translate_mult": 0.55, "zoom": 0.045},
    "normal": {"speed_mult": 1.0, "translate_mult": 1.0, "zoom": 0.09},
    "energetic": {"speed_mult": 0.6, "translate_mult": 1.5, "zoom": 0.16},
}
FRAME_STYLES = ("clean", "film-grain", "vignette-heavy", "glow-frame")



def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _looks_hebrew(text: str) -> bool:
    return any("֐" <= ch <= "׿" for ch in (text or ""))


# Scripts whose glyphs join or reorder. Wrapping each character in its own
# <span> breaks Arabic cursive joining outright and mangles Devanagari and Thai
# clusters, so per-character animations fall back to animating the whole title
# for these -- a spinning letter is not worth illegible type.
_NO_SPLIT_RANGES = (
    ("\u0600", "\u06ff"),  # Arabic
    ("\u0750", "\u077f"),  # Arabic Supplement
    ("\u0900", "\u097f"),  # Devanagari
    ("\u0e00", "\u0e7f"),  # Thai
)


def _can_split_chars(text: str) -> bool:
    return not any(lo <= ch <= hi for ch in (text or "") for lo, hi in _NO_SPLIT_RANGES)


def _split_chars(text: str) -> str:
    """Wrap each character in a span so GSAP can stagger them, and each word in
    a span that refuses to break.

    Every `.ch` is an inline-block, because transforms don't apply to inline
    boxes -- and adjacent inline-blocks are a line-break opportunity. So the
    browser will break a headline between any two letters. The previous version
    of this made it worse by writing the word gap as `&nbsp;`: that is the one
    position a line may *not* break, which left every position except the right
    one available and produced "FLEETWOO / D MAC".

    Words are therefore atomic (`white-space: nowrap`) and separated by an
    ordinary space, so the only break opportunity in the headline is a real word
    gap. A single word too wide for the frame is handled before it gets here --
    styles.headline_size shrinks the type until it fits on one line."""
    out = []
    for part in re.split(r"(\s+)", text):
        if not part:
            continue
        if part.isspace():
            out.append(" ")
            continue
        chars = "".join(f'<span class="ch">{html.escape(c, quote=True)}</span>' for c in part)
        out.append(f'<span class="word">{chars}</span>')
    return "".join(out)


def _loop_repeat(span: float, cycle: float) -> int:
    """Finite repeat count so a yoyo tween of length `cycle` visibly fills `span`
    seconds. The deterministic frame-seeking renderer forbids repeat: -1."""
    return max(1, math.ceil(span / cycle))




def _scene_html(index: int, start: float, duration: float, track: dict, media: dict, palette: dict, audio_duration: float | None = None, motion: dict | None = None, language: str = "en", style: dict | None = None, lay: dict | None = None, field: dict | None = None) -> tuple[str, str, str]:
    """Returns (scene_div_html, media_tags_html, scene_js). Media tags must be direct
    children of the stage (siblings of .scene divs) — the framework cannot manage
    playback of <video>/<audio> nested inside another timed element."""
    scene_id = f"scene-{index}"
    pal = palette
    style = style or styles.get(None)
    lay = lay or styles.layout(None)
    mo = motion or MOTION_STYLES["normal"]
    audio_duration = audio_duration or duration
    # Base paragraph direction matters even for a single Hebrew string: without
    # it, the browser's bidi algorithm mis-nests any embedded Latin words/names
    # (artist, album, award names) and punctuation ends up in the wrong place.
    rtl = ' dir="rtl"' if languages.is_rtl(language) else ""
    # The .scene wrapper sits at a higher z-index than .bg-media (it holds the scrim/
    # orbs/text). A solid background here would paint over the video underneath, so
    # only fall back to the gradient when there's no video/image to show through.
    # The generative backdrop is always present, so a scene is never blank --
    # the flat gradient is only needed when artwork would otherwise paint over
    # a video underneath.
    has_visual = bool(media.get("video") or media.get("image"))
    # A layout with its own colour field paints the whole frame, so the per-
    # scene gradient underneath it would never be seen.
    bg_style = (
        "" if (has_visual or field)
        else f"background: radial-gradient(circle at center, {pal['bg1']} 0%, {pal['bg2']} 100%);"
    )

    media_html = ""
    # The flat field and the blurred backdrop are ordinary clips: unlike the
    # artwork itself, nothing writes their opacity per frame, so letting the
    # framework time them costs nothing and keeps them out of the driver.
    if field:
        media_html += (
            f'<div class="clip art-field" style="background: {field["bg"]};" '
            f'data-start="{start}" data-duration="{duration}" data-track-index="3"></div>\n'
        )
    if lay.get("backdrop") and media.get("image"):
        media_html += (
            f'<img class="clip art-backdrop" data-start="{start}" data-duration="{duration}" '
            f'data-track-index="4" src="{_esc(_rel(media["image"]))}" />\n'
            f'<div class="clip art-wash" data-start="{start}" data-duration="{duration}" '
            f'data-track-index="5"></div>\n'
        )

    if media.get("video"):
        # Video cannot go inside the .art-box wrapper -- the framework manages
        # playback only for media that are direct children of the stage -- so
        # the layout's geometry goes straight onto the element. object-fit does
        # the clipping the wrapper would otherwise have done.
        box = f' style="{lay["art"]}"' if lay.get("art") else ""
        media_html += (
            f'<video id="media-{index}" class="clip bg-media" muted playsinline preload="auto" '
            f'data-start="{start}" data-duration="{duration}" data-track-index="1"{box} '
            f'src="{_esc(_rel(media["video"]))}"></video>\n'
        )
    elif media.get("image"):
        # Artwork is animated every frame by the visuals runtime (Ken Burns +
        # bass breath), so it uses .art-media and carries no CSS transition --
        # a transition would key off real elapsed time and break determinism.
        media_html += visuals.art_html(index, start, duration, _esc(_rel(media["image"])),
                                       box_css=lay.get("art"))

    if media.get("audio"):
        media_html += (
            f'<audio id="audio-{index}" class="clip" data-start="{start}" data-duration="{audio_duration}" '
            f'data-track-index="2" src="{_esc(_rel(media["audio"]))}"></audio>\n'
        )

    display_title = track["title"] + (f" ({track['album']})" if track.get("album") else "")
    entrance = styles.ENTRANCES[style["entrance"]]
    headline = track["artist"]
    headline_size, break_mid_word = styles.headline_fit(style, headline)
    # Style capabilities that change what the headline IS, not just how it
    # moves. A ticker scrolls one long track, an art-fill masks the letters
    # with the sleeve -- both are incompatible with per-character spans.
    is_ticker = bool(style.get("ticker"))
    art_fill = (bool(style.get("art_text")) and bool(media.get("image"))
                and not media.get("video"))
    # The per-character effects apply to the headline, which is the artist.
    split_title = (bool(entrance.get("chars")) and _can_split_chars(headline)
                   and not is_ticker and not art_fill)
    headline_markup = _split_chars(headline) if split_title else _esc(headline)

    artist_style = f"font-size: {headline_size}px;"
    if break_mid_word:
        # Per-scene, not per-style: headline_fit only breaks inside a word
        # when the break is deliberate (see its docstring); most names either
        # fit one line or break at their spaces.
        artist_style += " word-break: break-all;"
    if art_fill:
        # The letterforms clip this image (background-clip: text in the style's
        # CSS); it is the same file the scene draws, so type and artwork read
        # as one printed object.
        artist_style += f" background-image: url('{_esc(_rel(media['image']))}');"
    if is_ticker:
        # Two identical halves, each the name three times over. xPercent: -50
        # then lands exactly on the seam between them, so the scroll needs no
        # measured width to stay seamless -- the only deterministic way to
        # scroll text whose rendered width the composer cannot know.
        unit = _esc(headline) + '<span class="tick-sep">&bull;</span>'
        half = f'<span class="tick-half">{unit * 3}</span>'
        artist_p = (f'<div class="ticker-wrap"><p id="artist-{index}" '
                    f'class="artist-name ticker-track" style="{artist_style}">'
                    f'{half}{half}</p></div>')
    else:
        fill_cls = " art-fill" if art_fill else ""
        # data-text feeds the ::before halo duplicate behind art-filled glyphs.
        fill_data = f' data-text="{_esc(headline)}"' if art_fill else ""
        artist_p = (f'<p id="artist-{index}" class="artist-name{fill_cls}"{fill_data} '
                    f'style="{artist_style}">{headline_markup}</p>')
    trivia = track.get("reason", "").strip()
    trivia_html = f'<p id="trivia-{index}" class="trivia-tag">{_esc(trivia)}</p>' if trivia else ""
    # The scrim exists to hold type apart from unpredictable imagery. Over a
    # flat colour field there is nothing to hold it apart from, and the
    # gradient only dirties the colour -- so layouts that own their ground
    # drop it, along with the orbs, which read as smudges on a printed field.
    scrim_html = '<div class="scrim"></div>' if lay.get("scrim") else ""
    orbs_html = (
        f'<div id="orb-a-{index}" class="orb orb-a" style="background: {pal["orb1"]};"></div>'
        f'<div id="orb-b-{index}" class="orb orb-b" style="background: {pal["orb2"]};"></div>'
    ) if not field else ""
    # The magazine-cover overlap: the headline moves out of the scene onto its
    # own layer positioned at the top of the frame, running out over the
    # sleeve (stroke and shadow in text_css keep it readable on both grounds).
    # It becomes a sibling clip the framework times like any other; every
    # #artist-N tween works unchanged.
    overlap = bool(lay.get("headline_overlap"))
    if overlap:
        media_html += (
            f'<div class="clip headline-overlap"{rtl} data-start="{start}" '
            f'data-duration="{duration}" data-track-index="6">{artist_p}</div>\n'
        )
    meta_artist = "" if overlap else artist_p
    scene_html = f"""
      <div id="{scene_id}" class="clip scene" style="{bg_style}" data-start="{start}" data-duration="{duration}" data-track-index="0">
        {scrim_html}
        {orbs_html}
        <div class="meta-container"{rtl}>
          <div id="meta-{index}" class="meta-inner">
            {meta_artist}
            <h1 id="title-{index}" class="track-title">{_esc(display_title)}</h1>
            {trivia_html}
            <div class="progress-container">
              <div id="progress-{index}" class="progress-bar" style="background: linear-gradient(90deg, {pal['accent']}, {pal['accent2']});"></div>
            </div>
          </div>
        </div>
      </div>
    """

    exit_start = start + duration - 0.55
    orb_a_cycle = max(duration / 2, 1.5) * mo["speed_mult"]
    orb_b_cycle = max(duration / 2.3, 1.5) * mo["speed_mult"]
    orb_ax, orb_ay = round(70 * mo["translate_mult"]), round(-40 * mo["translate_mult"])
    orb_bx, orb_by = round(-60 * mo["translate_mult"]), round(45 * mo["translate_mult"])
    # A style with a card animates the card as one object. Staggering the lines
    # individually inside it leaves the card sitting on screen empty for the
    # first fraction of a second, which reads as a bug -- the box has to arrive
    # with its contents. Styles without a card keep the line-by-line stagger.
    has_panel = bool(style.get("panel"))
    # Image first, then the name: when the headline runs over the sleeve, let
    # the artwork land alone for a beat before the big type arrives on top of
    # it -- the overlap only reads as intentional if the image is already
    # there to be overlapped.
    headline_at = start + (0.7 if overlap else 0.25)
    if is_ticker:
        # The scroll IS the entrance: the track fades up already moving and
        # travels for the whole scene. xPercent and the exit's transforms
        # compose independently, so nothing fights.
        text_sel = f'"#title-{index}, #artist-{index}' + (f', #trivia-{index}"' if trivia else '"')
        entrance_js = (
            f'tl.fromTo("#artist-{index}", {{ opacity: 0 }}, '
            f'{{ opacity: 1, duration: 0.6, ease: "power1.out" }}, {headline_at})\n'
            f'        .fromTo("#artist-{index}", {{ xPercent: 0 }}, '
            f'{{ xPercent: -50, duration: {duration}, ease: "none" }}, {start})\n'
            f'        .fromTo("#title-{index}", {{ opacity: 0, y: 18, filter: "blur(6px)" }}, '
            f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.8, ease: "expo.out" }}, {start + 0.5})'
            + (f'\n        .fromTo("#trivia-{index}", {{ opacity: 0, y: 14, filter: "blur(6px)" }}, '
               f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.8, ease: "expo.out" }}, {start + 0.64})' if trivia else '')
        )
    elif split_title:
        # Characters animate; the rest of the block follows normally. The panel
        # (if any) still comes in as one object so it never shows up empty.
        text_sel = (f'"#meta-{index}"' if has_panel
                    else f'"#title-{index}, #artist-{index}'
                         + (f', #trivia-{index}"' if trivia else '"'))
        panel_in = (f'tl.fromTo("#meta-{index}", {{ opacity: 0 }}, '
                    f'{{ opacity: 1, duration: 0.3 }}, {start + 0.2})\n        .'
                    if has_panel else 'tl.')
        entrance_js = (
            f'{panel_in}fromTo("#artist-{index} .ch", {entrance["from"]}, '
            f'Object.assign({entrance["to"]}), {headline_at})\n'
            f'        .fromTo("#title-{index}", {{ opacity: 0, y: 18, filter: "blur(6px)" }}, '
            f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.8, ease: "expo.out" }}, {start + 0.5})'
            + (f'\n        .fromTo("#trivia-{index}", {{ opacity: 0, y: 14, filter: "blur(6px)" }}, '
               f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.8, ease: "expo.out" }}, {start + 0.64})' if trivia else '')
        )
    elif has_panel:
        text_sel = f'"#meta-{index}"'
        entrance_js = (
            f'tl.fromTo("#meta-{index}", {entrance["from"]}, '
            f'Object.assign({entrance["to"]}), {start + 0.25})'
        )
    else:
        text_sel = f'"#title-{index}, #artist-{index}' + (f', #trivia-{index}"' if trivia else '"')
        entrance_js = (
            f'tl.fromTo("#artist-{index}", {entrance["from"]}, Object.assign({entrance["to"]}), {headline_at})\n'
            f'        .fromTo("#title-{index}", {entrance["from"]}, Object.assign({{}}, {entrance["to"]}, {{ duration: 1.1 }}), {start + 0.45})'
            + (f'\n        .fromTo("#trivia-{index}", {entrance["from"]}, Object.assign({{}}, {entrance["to"]}, {{ duration: 1.1 }}), {start + 0.6})' if trivia else '')
        )

    # Gated on the elements existing at all: GSAP warns on a missing target and
    # `npm run check` reads that warning as a finding.
    orb_js = (
        f'\n        .fromTo("#orb-a-{index}, #orb-b-{index}", {{ opacity: 0 }}, {{ opacity: 0.35, duration: 1.2 }}, {start})'
        f'\n        .to("#orb-a-{index}", {{ x: {orb_ax}, y: {orb_ay}, duration: {orb_a_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_a_cycle)}, ease: "sine.inOut" }}, {start})'
        f'\n        .to("#orb-b-{index}", {{ x: {orb_bx}, y: {orb_by}, duration: {orb_b_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_b_cycle)}, ease: "sine.inOut" }}, {start})'
    ) if not field else ""
    orb_off_js = (
        f'\n        .set("#orb-a-{index}, #orb-b-{index}", {{ opacity: 0 }}, {start + duration})'
    ) if not field else ""
    # Ambient micro-motion, the "alive" layer under the entrances: the text
    # block breathes a few pixels over the scene and an inset sleeve box
    # drifts against it in slow opposition. Both target elements nothing else
    # tweens -- the breath rides .meta-container (a parent the entrances and
    # exits never touch), the drift rides the layout's art box wrapper -- so
    # no property is ever written by two tweens at once.
    breath_cycle = round(max(duration / 2, 2.5), 3)
    breath_js = (
        f'\n        .fromTo("#scene-{index} .meta-container", {{ y: 4 }}, {{ y: -4, duration: {breath_cycle}, '
        f'yoyo: true, repeat: {_loop_repeat(duration, breath_cycle)}, ease: "sine.inOut" }}, {start})'
    )
    # One slow directional pass rather than a bob: the box starts a little low
    # and finishes a little high, easing through the middle -- the sleeve
    # travels through the layout over the scene instead of oscillating.
    drift = lay.get("drift_px", 10)
    float_js = (
        f'\n        .fromTo("#artbox-{index}", {{ y: {drift} }}, {{ y: {-drift}, '
        f'duration: {duration}, ease: "sine.inOut" }}, {start})'
    ) if (lay.get("float_art") and media.get("image") and not media.get("video")) else ""
    scene_js = f"""
      {entrance_js}{orb_js}{breath_js}{float_js}
        .to("#progress-{index}", {{ scaleX: 1, duration: {max(duration - 0.6, 0.5)}, ease: "linear" }}, {start + 0.3})
        .to({text_sel}, {entrance['exit']}, {exit_start})
        .set({text_sel}, {{ opacity: 0 }}, {start + duration}){orb_off_js};
    """
    # Artwork gets no GSAP tween here. Its Ken Burns push, pan and bass-driven
    # breath are all written per frame by the visuals runtime, which owns the
    # #art-N elements outright -- a tween on the same transform would fight it,
    # and would target a #media-N element that no longer exists for stills.
    # Only full-bleed video gets the creep. A framed layout sets the video's
    # box directly on the element (it cannot be wrapped -- see above), so there
    # is nothing to clip a scale against and the footage would grow straight
    # out over the field beside it.
    if media.get("video") and not lay.get("art"):
        # Subtler zoom-only creep on video so it doesn't fight the footage's own motion.
        video_zoom = 1 + mo["zoom"]
        scene_js += f"""
      tl.fromTo("#media-{index}", {{ scale: 1 }}, {{ scale: {video_zoom}, duration: {duration}, ease: "none" }}, {start});
    """
    return scene_html, media_html, scene_js


# The header is the only place the show name and episode label appear over the
# scenes. There used to be a "hero" as well: the same two lines set large and
# centered at the top of scene 0, flying up into the header as they faded. It
# read well over a dark sleeve and badly over everything else -- centered at 19%
# is exactly where a face or a logo tends to sit, and the promo opened by
# covering the one image it exists to show. The header alone says the same thing
# and never lands on the artwork.
HEADER_FADE_IN_AT = 0.3


def _header_html(show_name: str, episode_label: str, total_duration: float, fade_in_at: float, outro_start: float | None = None, logo_path: str | None = None) -> tuple[str, str]:
    # Show name and episode label are both optional, and an empty one renders
    # nothing at all rather than an empty box -- an empty .header-show still
    # occupies a line, which pushed the episode label down to where the show
    # name should be. With all three gone there is no header to draw.
    show_name = (show_name or "").strip()
    episode_label = (episode_label or "").strip()
    if not (show_name or episode_label or logo_path):
        return "", ""
    # Alignment follows the show/episode name's own script, not the on-screen
    # language toggle -- an English show name shouldn't right-align just
    # because Hebrew is selected for the UI strings elsewhere.
    rtl = ' dir="rtl"' if _looks_hebrew(show_name) or _looks_hebrew(episode_label) else ""
    logo_html = f'<img class="header-logo" src="{_esc(_rel(logo_path))}" alt="" />' if logo_path else ""
    show_html = f'<div id="header-show" class="header-show">{_esc(show_name)}</div>' if show_name else ""
    episode_html = f'<div id="header-episode" class="header-episode">{_esc(episode_label)}</div>' if episode_label else ""
    header_html = f"""
      <div id="header" class="clip header" data-start="0" data-duration="{total_duration}" data-track-index="20">
        <div id="header-inner" class="header-inner">
          <div{rtl}>
            {show_html}
            {episode_html}
          </div>
          {logo_html}
        </div>
      </div>
    """
    # The closing card shows the logo and show name full size, so the small
    # header versions are redundant there -- and having both on screen reads as
    # a duplicate. Fade the header out just before the outro brand animates in.
    header_out = ""
    if outro_start is not None:
        # The fade lands exactly on the outro boundary and the set right on it
        # is the seek-safety hard kill: a non-linear seek that lands past the
        # tween must still find the header hidden.
        fade_out_at = max(0.0, outro_start - 0.35)
        header_out = (
            f'\n      tl.to("#header-inner", {{ opacity: 0, duration: 0.35, '
            f'ease: "power2.in" }}, {fade_out_at});'
            f'\n      tl.set("#header-inner", {{ opacity: 0 }}, {round(outro_start, 3)});'
        )
    header_js = f"""
      tl.fromTo("#header-inner", {{ opacity: 0, y: -14 }}, {{ opacity: 1, y: 0, duration: 0.6, ease: "expo.out" }}, {fade_in_at});{header_out}
    """
    return header_html, header_js


def _outro_html(start: float, duration: float, show_name: str, episode_label: str, remaining: list[dict], pal: dict, language: str = "en", motion: dict | None = None, logo_path: str | None = None, field: dict | None = None) -> tuple[str, str]:
    mo = motion or MOTION_STYLES["normal"]
    strings = languages.strings(language)
    rtl = ' dir="rtl"' if languages.is_rtl(language) else ""
    # The ground follows the layout's field when it has one, so a theme that
    # ran on a cream page or a sampled colour closes on the same surface
    # instead of cutting to the palette's unrelated gradient. See
    # styles.outro_ground; the typography half is styles.outro_css.
    ground, orb_mult = styles.outro_ground(field, pal)
    bg_style = f"background: {ground};"
    also_html = ""
    if remaining:
        seen = set()
        artists = []
        for t in remaining:
            key = t["artist"].lower()
            if key not in seen:
                seen.add(key)
                artists.append(t["artist"])
        items = " &nbsp;•&nbsp; ".join(_esc(a) for a in artists[:8])
        also_html = f"""
          <h1 id="outro-title" class="outro-title"{rtl}>{_esc(strings['also_featuring'])}</h1>
          <p id="also-featuring" class="also-featuring-list">{items}</p>
        """
    logo_html = f'<img id="outro-logo" class="outro-logo" src="{_esc(_rel(logo_path))}" alt="" />' if logo_path else ""
    # Both optional, same rule as the header: nothing typed, nothing drawn.
    show_name = (show_name or "").strip()
    episode_label = (episode_label or "").strip()
    show_html = f'<div id="outro-show" class="outro-show">{_esc(show_name)}</div>' if show_name else ""
    episode_html = f'<div id="outro-episode" class="outro-episode">{_esc(episode_label)}</div>' if episode_label else ""
    scene_html = f"""
      <div id="outro" class="clip scene" style="{bg_style}" data-start="{start}" data-duration="{duration}" data-track-index="0">
        <div id="outro-orb-a" class="orb orb-a" style="background: {pal['orb1']};"></div>
        <div id="outro-orb-b" class="orb orb-b" style="background: {pal['orb2']};"></div>
        <div class="outro-brand">
          {logo_html}
          {show_html}
          {episode_html}
        </div>
        <div class="outro-meta">
          {also_html}
          <div id="outro-cta" class="cta-text"{rtl}>{_esc(strings['cta'])}</div>
          <div id="outro-wordmark" class="outro-wordmark" dir="ltr">{WORDMARK}</div>
        </div>
      </div>
    """
    orb_a_cycle = max(duration / 2, 1.5) * mo["speed_mult"]
    orb_b_cycle = max(duration / 2.2, 1.5) * mo["speed_mult"]
    orb_ax, orb_ay = round(50 * mo["translate_mult"]), round(-30 * mo["translate_mult"])
    orb_bx, orb_by = round(-45 * mo["translate_mult"]), round(30 * mo["translate_mult"])
    # Skip the tween entirely when there is no logo -- GSAP warns on a missing
    # target and the composition check treats that as a finding.
    logo_tween = (
        '.fromTo("#outro-logo", { opacity: 0, y: -14, scale: 0.82, filter: "blur(8px)" }, '
        '{ opacity: 0.9, y: 0, scale: 1, filter: "blur(0px)", duration: 0.7, ease: "back.out(1.6)" }, '
        f'{start + 0.15})'
    ) if logo_path else ""
    # Same reason the logo tween is conditional: an absent target is a GSAP
    # warning and a `npm run check` finding, not a silent no-op.
    show_tween = (
        f'\n        .fromTo("#outro-show", {{ opacity: 0, y: 22, filter: "blur(10px)" }}, '
        f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.85, ease: "expo.out" }}, {start + 0.3})'
    ) if show_name else ""
    episode_tween = (
        f'\n        .fromTo("#outro-episode", {{ opacity: 0, y: 14, filter: "blur(6px)" }}, '
        f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.75, ease: "expo.out" }}, {start + 0.42})'
    ) if episode_label else ""
    # #outro-title and #also-featuring only exist when there are other artists
    # to list -- also_html is empty otherwise.
    also_tweens = (
        f'\n        .fromTo("#outro-title", {{ opacity: 0, y: 34, filter: "blur(10px)" }}, '
        f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.95, ease: "expo.out" }}, {start + 0.6})'
        f'\n        .fromTo("#also-featuring", {{ opacity: 0, y: 26, filter: "blur(8px)" }}, '
        f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 1.05, ease: "expo.out" }}, {start + 0.85})'
    ) if also_html else ""
    # The CTA used to just be there when the scene cut in; it now lands last,
    # after the credits, which is also the order the eye is meant to read.
    cta_tween = (
        f'\n        .fromTo("#outro-cta", {{ opacity: 0, y: 14 }}, '
        f'{{ opacity: 1, y: 0, duration: 0.7, ease: "expo.out" }}, {start + 1.15})'
        # Last in, and to 0.7 rather than 1 -- the mark should arrive after the
        # eye has finished reading everything that matters.
        f'\n        .fromTo("#outro-wordmark", {{ opacity: 0, y: 10 }}, '
        f'{{ opacity: 0.7, y: 0, duration: 0.6, ease: "expo.out" }}, {start + 1.45})'
    )
    scene_js = f"""
      tl.fromTo("#outro-orb-a, #outro-orb-b", {{ opacity: 0 }}, {{ opacity: {round(0.35 * orb_mult, 3)}, duration: 1.4 }}, {start})
        .to("#outro-orb-a", {{ x: {orb_ax}, y: {orb_ay}, duration: {orb_a_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_a_cycle)}, ease: "sine.inOut" }}, {start})
        .to("#outro-orb-b", {{ x: {orb_bx}, y: {orb_by}, duration: {orb_b_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_b_cycle)}, ease: "sine.inOut" }}, {start})
        {logo_tween}{show_tween}{episode_tween}{also_tweens}{cta_tween};
    """
    return scene_html, scene_js


def _frame_overlay_html(frame: str, total_duration: float, accent_hex: str) -> str:
    if frame not in FRAME_STYLES or frame == "clean":
        return ""
    style = f"--frame-accent: {accent_hex};" if frame == "glow-frame" else ""
    return (
        f'<div id="frame-overlay" class="clip frame-overlay frame-{frame}" style="{style}" '
        f'data-start="0" data-duration="{total_duration}" data-track-index="45"></div>'
    )


def build_composition_html(
    show_name: str, episode_label: str, standout: list[dict], remaining: list[dict],
    theme: dict, scene_duration: float, language: str = "en",
    logo_path: str | None = None,
) -> str:
    """standout: list of {track: dict(artist,title), media: dict(video/image/audio)}.
    The video always opens directly on the first track (no title-card intro); the
    closing card is kept. theme = {"palettes": [...], "motion": "...", "frame": "..."}."""
    palette = theme["palettes"]
    motion = MOTION_STYLES.get(theme.get("motion", "normal"), MOTION_STYLES["normal"])
    frame = theme.get("frame", "clean")
    style = styles.get(theme.get("style"))
    lay = styles.layout(theme.get("layout"))
    # How the artwork changes between tracks belongs to the theme, not to the
    # typographic style: two themes can share a style and still want one to
    # cut and the other to dissolve. Falls back to the style's own choice.
    transition = theme.get("transition") or style.get("transition", "fade")
    if transition not in styles.TRANSITIONS:
        transition = "fade"
    field_mode = lay.get("field")
    field_is_light = palette_mod.is_light(field_mode) if field_mode else False

    cursor = 0.0
    scenes_html = []
    media_tags_html = []
    scenes_js = []
    visual_scenes = []

    for i, item in enumerate(standout):
        pal = palette[i % len(palette)]
        is_last = i == len(standout) - 1
        # Each scene runs for a whole number of bars of its own track, so the
        # cut lands where that music was going to change anyway. media_finder
        # picked the length when it cut the audio; `scene_duration` is only the
        # pace the user asked for, and the fallback when a track had no usable
        # tempo. A scene laid out at any other length would drift against its
        # own audio, so this must be the value the trim actually produced.
        this_duration = item["media"].get("scene_seconds") or scene_duration
        audio_duration = this_duration + OUTRO_DURATION if is_last else None
        # Sampled per scene, so the field tracks each sleeve rather than one
        # colour being chosen for the whole promo from whatever happened to be
        # track one. Cached inside palette.field by (path, mode).
        scene_field = palette_mod.field(_abs(item["media"].get("image")), field_mode) if field_mode else None
        sh, mh, sj = _scene_html(i, cursor, this_duration, item["track"], item["media"], pal, audio_duration=audio_duration, motion=motion, language=language, style=style, lay=lay, field=scene_field)
        scenes_html.append(sh)
        media_tags_html.append(mh)
        scenes_js.append(sj)
        visual_scenes.append({
            "index": i,
            "start": cursor,
            "duration": this_duration,
            "analysis": item.get("analysis"),
            # Video keeps its own footage; only still artwork gets the
            # Ken Burns / breathing treatment from the visuals runtime.
            "has_art": bool(item["media"].get("image")) and not item["media"].get("video"),
        })
        cursor += this_duration

    outro_pal = palette[len(standout) % len(palette)]
    # Sampled from the LAST sleeve rather than the first, so the card carries
    # the colour the viewer is looking at when it cuts in. Same cache as the
    # scenes' own fields, so this costs no extra ffmpeg call for a layout that
    # already sampled that image.
    last_image = _abs(standout[-1]["media"].get("image")) if standout else None
    outro_field = palette_mod.field(last_image, field_mode) if field_mode else None
    # The two strings on the card that are set in the theme's display face and
    # so can overflow it. outro_css sizes them to fit rather than letting a
    # wide face run off the frame -- it needs the actual text to do that.
    outro_strings = {"show": show_name or "",
                     "also_featuring": languages.strings(language)["also_featuring"]}
    oh, oj = _outro_html(cursor, OUTRO_DURATION, show_name, episode_label, remaining, outro_pal, language=language, motion=motion, logo_path=logo_path, field=outro_field)
    scenes_html.append(oh)
    scenes_js.append(oj)
    cursor += OUTRO_DURATION

    total_duration = cursor
    header_html, header_js = _header_html(show_name, episode_label, total_duration, HEADER_FADE_IN_AT,
                                         outro_start=total_duration - OUTRO_DURATION,
                                         logo_path=logo_path)
    scenes_js.append(header_js)
    frame_html = _frame_overlay_html(frame, total_duration, palette[0]["accent"])
    visuals_js = visuals.runtime_js(
        visual_scenes, total_duration,
        patch=style["patch"],
        accent_hex=palette[0]["accent"],
        transition=transition,
        art_opacity=lay.get("art_opacity", 0.88),
    )

    # A framed layout shows the sleeve as the subject, so the video layer's
    # backdrop dimming comes off; a light field flips the header to ink.
    # The ink header only makes sense when the header actually sits ON the
    # light field. A layout whose art runs to the top edge (press, split) has
    # a white field *below* but a sleeve *behind the header* -- ink type over
    # arbitrary artwork fails contrast, so those keep the white header and its
    # band. header_band doubles as "the header sits over artwork".
    root_classes = " ".join(filter(None, [
        "framed" if lay.get("art") else "",
        "light-frame" if (field_is_light and not lay.get("header_band", True)) else "",
        "" if lay.get("header_band", True) else "no-headband",
    ]))
    framed_css = ".framed .bg-media { opacity: 1; }" if lay.get("art") else ""
    if lay.get("float_art"):
        # Rounded corners only on inset boxes -- a flush band with radii shows
        # the field through its own corners.
        framed_css += " .art-box { border-radius: 22px; }"
    if lay.get("art_fade"):
        # The sleeve dissolves into the field below this point, so a text block
        # that grows up into it lands on flat colour rather than on artwork.
        # See the art_fade note in styles.LAYOUTS. Masked rather than overlaid
        # with a gradient: an overlay would need the field's exact colour and
        # would sit above the sleeve's own Ken Burns push, which is written on
        # the <img> inside the box every frame.
        stop = round(lay["art_fade"] * 100)
        fade = (f"linear-gradient(180deg, #000 0%, #000 {stop}%, transparent 100%)")
        framed_css += (f" .art-box {{ -webkit-mask-image: {fade}; mask-image: {fade}; }}")

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <title>{_esc(show_name.strip() + " Promo") if (show_name or "").strip() else "Promo"}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>{styles.font_face_css(style)}{BASE_CSS}{visuals.CSS}{styles.text_css(style, lay, field_is_light)}{styles.outro_css(style, lay, outro_field, outro_strings)}{framed_css}</style>
  </head>
  <body>
    <div id="root" class="{root_classes}" data-composition-id="main" data-start="0" data-duration="{total_duration}" data-width="1080" data-height="1920">
      {visuals.canvas_html()}
      {''.join(media_tags_html)}
      {''.join(scenes_html)}
      {header_html}
      {frame_html}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      {''.join(scenes_js)}
      window.__timelines["main"] = tl;
    </script>
    {visuals_js}
  </body>
</html>
"""


class RenderError(RuntimeError):
    """A render that failed, carrying only the part worth showing the user.
    The full CLI log goes to the server log."""


# The renderer aborts a capture that makes no frame progress for this long. Its
# 60s default is sized for a workstation; a shared-vCPU container doing software
# WebGL can need longer than that just to produce frame 0, and dying there
# throws away the whole job. Raising it costs a healthy render nothing -- the
# guard only fires when nothing is moving at all.
RENDER_STALL_TIMEOUT_MS = os.environ.get("HF_DE_STALL_MS") or "300000"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _render_failure_reason(log: str) -> str:
    """One line out of the CLI's several hundred. The renderer prints its own
    cause on the line after "Render failed"; fall back to the last thing it
    said rather than inventing a reason."""
    lines = [_ANSI_RE.sub("", ln).strip() for ln in log.splitlines()]
    lines = [ln for ln in lines if ln]
    for i, line in enumerate(lines):
        if "Render failed" in line:
            cause = lines[i + 1] if i + 1 < len(lines) else ""
            return cause or line
    return lines[-1] if lines else "the renderer exited without saying why"


def render_video(project_dir: str, output_rel_path: str, quality: str = "standard",
                 composition: str | None = None) -> str:
    """Runs the pinned hyperframes CLI, returns the absolute output path.

    composition names a file to render instead of index.html, relative to project_dir.
    Concurrent jobs each pass their own, so two renders can't overwrite one another's
    composition mid-flight. It must live at the project root: media inside it is
    addressed relative to project_dir (see app._composition_path), and a file in a
    subdirectory would resolve those one level down."""
    cmd = [
        "npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render",
        "-q", quality, "-o", output_rel_path,
    ]
    if composition:
        cmd += ["-c", composition]
    env = {**os.environ, "HF_DE_STALL_MS": RENDER_STALL_TIMEOUT_MS}
    result = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        # The whole log is what an operator needs and what nobody else can
        # read -- a beta tester should not get 200 lines of render trace in an
        # alert box, which is exactly what used to happen.
        print(f"hyperframes render failed (exit {result.returncode}) for {output_rel_path}:\n"
              f"{result.stdout}\n{result.stderr}", flush=True)
        raise RenderError(_render_failure_reason(f"{result.stdout}\n{result.stderr}"))
    return os.path.join(project_dir, output_rel_path)


# The wordmark burned into free-tier videos. A committed PNG rather than an
# ffmpeg `drawtext` call: drawtext needs libfreetype, which this project's
# ffmpeg builds don't consistently have, and a raster asset looks identical in
# dev and in the container. Regenerate with tools/make_watermark.py.
WATERMARK_PATH = os.path.join(PROJECT_DIR, "server", "static", "brand", "watermark.png")


def watermark_video(src_path: str, dst_path: str) -> str:
    """Writes a watermarked copy of src_path to dst_path, bottom-centre.

    Deliberately a second pass over a finished render rather than an element in
    the composition: rendering twice would cost a second full ~50s capture,
    whereas this re-encode is a few seconds. It also means the clean master
    already exists the moment someone pays -- upgrading hands over a file that
    is already on disk instead of triggering a re-render.

    Audio is stream-copied; only video is re-encoded.
    """
    if not os.path.exists(WATERMARK_PATH):
        raise RuntimeError(f"watermark asset missing: {WATERMARK_PATH} (run tools/make_watermark.py)")

    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-i", src_path,
        "-i", WATERMARK_PATH,
        # The asset is a full-frame 1080x1920 overlay of tiled diagonal marks,
        # so it composites at the origin -- no positioning arithmetic here. A
        # corner mark was the first attempt and was simply croppable.
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        # Copy audio untouched -- re-encoding it would cost quality for nothing.
        # `-c:a copy` on a file with no audio track fails, so only map/copy
        # audio when the source actually has some.
        *(["-c:a", "copy"] if _has_audio(src_path) else ["-an"]),
        dst_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"watermarking failed:\n{result.stdout}\n{result.stderr}")
    return dst_path


def _has_audio(path: str) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())
