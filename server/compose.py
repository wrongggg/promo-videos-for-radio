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
.artist-name .ch.sp { white-space: pre; }
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
   of the closing card after the header has gone. */
.header-inner::before {
  content: ""; position: absolute; z-index: -1; pointer-events: none;
  left: -60px; right: -60px; top: -250px; bottom: -46px;
  background: linear-gradient(180deg, rgba(0,0,0,0.58) 0%, rgba(0,0,0,0.52) 62%,
              rgba(0,0,0,0.45) 88%, rgba(0,0,0,0) 100%);
}
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
.hero-container {
  position: absolute; top: 19%; left: 0; width: 1080px; z-index: 25;
  text-align: center; display: flex; flex-direction: column; align-items: center;
}
.hero-show {
  font-size: 104px; font-weight: 900; text-transform: uppercase; letter-spacing: 4px;
  text-shadow: 0 6px 30px rgba(0,0,0,0.85); opacity: 0; transform-origin: center center;
}
.hero-episode {
  font-size: 32px; font-weight: 700; letter-spacing: 6px; text-transform: uppercase;
  opacity: 0; margin-top: 16px; text-shadow: 0 4px 18px rgba(0,0,0,0.8); transform-origin: center center;
}




.progress-container { width: 320px; height: 8px; background: rgba(255,255,255,0.12); border-radius: 4px; overflow: hidden; margin-top: 30px; }
.progress-bar { width: 100%; height: 100%; transform-origin: left center; transform: scaleX(0); }
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
    """Wrap each character in a span so GSAP can stagger them.

    Spaces become their own span holding a non-breaking space, so word gaps
    survive and the browser can still line-break between them."""
    out = []
    for ch in text:
        if ch == " ":
            out.append('<span class="ch sp">&nbsp;</span>')
        else:
            out.append(f'<span class="ch">{html.escape(ch, quote=True)}</span>')
    return "".join(out)


def _loop_repeat(span: float, cycle: float) -> int:
    """Finite repeat count so a yoyo tween of length `cycle` visibly fills `span`
    seconds. The deterministic frame-seeking renderer forbids repeat: -1."""
    return max(1, math.ceil(span / cycle))




def _scene_html(index: int, start: float, duration: float, track: dict, media: dict, palette: dict, audio_duration: float | None = None, hero: tuple[str, str] | None = None, motion: dict | None = None, language: str = "en", style: dict | None = None, lay: dict | None = None, field: dict | None = None) -> tuple[str, str, str]:
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

    hero_html = hero[0] if hero else ""
    display_title = track["title"] + (f" ({track['album']})" if track.get("album") else "")
    entrance = styles.ENTRANCES[style["entrance"]]
    headline = track["artist"]
    headline_size = styles.headline_size(style, headline)
    # The per-character effects apply to the headline, which is the artist.
    split_title = bool(entrance.get("chars")) and _can_split_chars(headline)
    headline_markup = _split_chars(headline) if split_title else _esc(headline)
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
    scene_html = f"""
      <div id="{scene_id}" class="clip scene" style="{bg_style}" data-start="{start}" data-duration="{duration}" data-track-index="0">
        {scrim_html}
        {orbs_html}
        {hero_html}
        <div class="meta-container"{rtl}>
          <div id="meta-{index}" class="meta-inner">
            <p id="artist-{index}" class="artist-name" style="font-size: {headline_size}px;">{headline_markup}</p>
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
    if split_title:
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
            f'Object.assign({entrance["to"]}), {start + 0.25})\n'
            f'        .fromTo("#title-{index}", {{ opacity: 0, y: 20 }}, '
            f'{{ opacity: 1, y: 0, duration: 0.7, ease: "power3.out" }}, {start + 0.55})'
            + (f'\n        .fromTo("#trivia-{index}", {{ opacity: 0, y: 14 }}, '
               f'{{ opacity: 1, y: 0, duration: 0.7, ease: "power3.out" }}, {start + 0.7})' if trivia else '')
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
            f'tl.fromTo("#artist-{index}", {entrance["from"]}, Object.assign({entrance["to"]}), {start + 0.25})\n'
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
    scene_js = f"""
      {entrance_js}{orb_js}
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
    if hero:
        scene_js += hero[1]
    return scene_html, media_html, scene_js


HERO_HANDOFF_START = 1.9
HERO_HANDOFF_DURATION = 0.85
# Offsets computed from the hero's on-screen position (centered, ~19% from top,
# 104px/32px type) to the header's actual resting position (60px from left,
# 250px from top, 32px/19px type) -- NOT arbitrary numbers. Recompute both if
# either element's CSS position/size changes. They last moved when .header's
# padding-top went 110px -> 250px to clear Instagram's story chrome, which is a
# pure vertical translation of the target, so both y values gained the same 140.
HERO_TO_HEADER_SHOW = {"x": -400, "y": -160, "scale": 0.31}
HERO_TO_HEADER_EPISODE = {"x": -400, "y": -230, "scale": 0.6}


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
        header_out = (
            f'\n      tl.to("#header-inner", {{ opacity: 0, duration: 0.35, '
            f'ease: "power2.in" }}, {max(0.0, outro_start - 0.3)});'
        )
    header_js = f"""
      tl.fromTo("#header-inner", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5, ease: "power2.out" }}, {fade_in_at});{header_out}
    """
    return header_html, header_js


def _hero_html(show_name: str, episode_label: str) -> tuple[str, str] | None:
    """Big centered show/episode reveal shown only at the very start of scene 0,
    then flies up and shrinks toward the header's corner as it fades — reads as
    the same text handing off to the persistent header rather than two unrelated
    elements swapping.

    Returns None when there is nothing to reveal (both fields are optional), so
    scene 0 simply opens on the artwork. Every tween below is gated on its own
    target existing: GSAP warns on a missing selector and `npm run check` reads
    that warning as a finding."""
    show_name = (show_name or "").strip()
    episode_label = (episode_label or "").strip()
    if not (show_name or episode_label):
        return None
    rtl = ' dir="rtl"' if _looks_hebrew(show_name) or _looks_hebrew(episode_label) else ""
    show_html = f'<div id="hero-show" class="hero-show">{_esc(show_name)}</div>' if show_name else ""
    episode_html = f'<div id="hero-episode" class="hero-episode">{_esc(episode_label)}</div>' if episode_label else ""
    hero_html = f"""
        <div class="hero-container"{rtl}>
          {show_html}
          {episode_html}
        </div>
    """
    handoff_end = HERO_HANDOFF_START + HERO_HANDOFF_DURATION
    s = HERO_TO_HEADER_SHOW
    e = HERO_TO_HEADER_EPISODE
    tweens = []
    if show_name:
        tweens.append(
            f'.fromTo("#hero-show", {{ opacity: 0, y: 50, scale: 0.85 }}, {{ opacity: 1, y: 0, scale: 1, duration: 0.9, ease: "back.out(1.6)" }}, 0.15)'
        )
    if episode_label:
        tweens.append(
            f'.fromTo("#hero-episode", {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.8, ease: "power3.out" }}, 0.4)'
        )
    if show_name:
        tweens.append(
            f'.to("#hero-show", {{ opacity: 0, x: {s["x"]}, y: {s["y"]}, scale: {s["scale"]}, duration: {HERO_HANDOFF_DURATION}, ease: "power2.inOut" }}, {HERO_HANDOFF_START})'
        )
    if episode_label:
        tweens.append(
            f'.to("#hero-episode", {{ opacity: 0, x: {e["x"]}, y: {e["y"]}, scale: {e["scale"]}, duration: {HERO_HANDOFF_DURATION}, ease: "power2.inOut" }}, {HERO_HANDOFF_START + 0.06})'
        )
    present = ", ".join(sel for sel, on in (("#hero-show", show_name), ("#hero-episode", episode_label)) if on)
    tweens.append(f'.set("{present}", {{ opacity: 0 }}, {handoff_end})')
    hero_js = "\n      tl" + "\n        ".join(tweens) + ";\n    "
    return hero_html, hero_js


def _outro_html(start: float, duration: float, show_name: str, episode_label: str, remaining: list[dict], pal: dict, language: str = "en", motion: dict | None = None, logo_path: str | None = None) -> tuple[str, str]:
    mo = motion or MOTION_STYLES["normal"]
    strings = languages.strings(language)
    rtl = ' dir="rtl"' if languages.is_rtl(language) else ""
    bg_style = f"background: radial-gradient(circle at center, {pal['bg1']} 0%, {pal['bg2']} 100%);"
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
        '.fromTo("#outro-logo", { opacity: 0, y: -20, scale: 0.7 }, '
        '{ opacity: 0.9, y: 0, scale: 1, duration: 0.7, ease: "back.out(1.7)" }, '
        f'{start + 0.15})'
    ) if logo_path else ""
    # Same reason the logo tween is conditional: an absent target is a GSAP
    # warning and a `npm run check` finding, not a silent no-op.
    show_tween = (
        f'\n        .fromTo("#outro-show", {{ opacity: 0, y: 24 }}, '
        f'{{ opacity: 1, y: 0, duration: 0.8, ease: "power3.out" }}, {start + 0.3})'
    ) if show_name else ""
    episode_tween = (
        f'\n        .fromTo("#outro-episode", {{ opacity: 0, y: 16 }}, '
        f'{{ opacity: 1, y: 0, duration: 0.7, ease: "power3.out" }}, {start + 0.45})'
    ) if episode_label else ""
    # #outro-title and #also-featuring only exist when there are other artists
    # to list -- also_html is empty otherwise.
    also_tweens = (
        f'\n        .fromTo("#outro-title", {{ opacity: 0, y: 40 }}, '
        f'{{ opacity: 1, y: 0, duration: 1, ease: "power3.out" }}, {start + 0.65})'
        f'\n        .fromTo("#also-featuring", {{ opacity: 0, y: 30 }}, '
        f'{{ opacity: 1, y: 0, duration: 1.1, ease: "power3.out" }}, {start + 0.95})'
    ) if also_html else ""
    scene_js = f"""
      tl.fromTo("#outro-orb-a, #outro-orb-b", {{ opacity: 0 }}, {{ opacity: 0.35, duration: 1.4 }}, {start})
        .to("#outro-orb-a", {{ x: {orb_ax}, y: {orb_ay}, duration: {orb_a_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_a_cycle)}, ease: "sine.inOut" }}, {start})
        .to("#outro-orb-b", {{ x: {orb_bx}, y: {orb_by}, duration: {orb_b_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_b_cycle)}, ease: "sine.inOut" }}, {start})
        {logo_tween}{show_tween}{episode_tween}{also_tweens};
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
        audio_duration = scene_duration + OUTRO_DURATION if is_last else None
        hero = _hero_html(show_name, episode_label) if i == 0 else None
        # Sampled per scene, so the field tracks each sleeve rather than one
        # colour being chosen for the whole promo from whatever happened to be
        # track one. Cached inside palette.field by (path, mode).
        scene_field = palette_mod.field(_abs(item["media"].get("image")), field_mode) if field_mode else None
        sh, mh, sj = _scene_html(i, cursor, scene_duration, item["track"], item["media"], pal, audio_duration=audio_duration, hero=hero, motion=motion, language=language, style=style, lay=lay, field=scene_field)
        scenes_html.append(sh)
        media_tags_html.append(mh)
        scenes_js.append(sj)
        visual_scenes.append({
            "index": i,
            "start": cursor,
            "duration": scene_duration,
            "analysis": item.get("analysis"),
            # Video keeps its own footage; only still artwork gets the
            # Ken Burns / breathing treatment from the visuals runtime.
            "has_art": bool(item["media"].get("image")) and not item["media"].get("video"),
        })
        cursor += scene_duration

    outro_pal = palette[len(standout) % len(palette)]
    oh, oj = _outro_html(cursor, OUTRO_DURATION, show_name, episode_label, remaining, outro_pal, language=language, motion=motion, logo_path=logo_path)
    scenes_html.append(oh)
    scenes_js.append(oj)
    cursor += OUTRO_DURATION

    total_duration = cursor
    # The header normally waits for the hero to finish flying into it. With no
    # show name and no episode label there is no hero to wait for, so a
    # logo-only header just fades in near the top instead of hanging back for
    # nearly three seconds of nothing.
    has_hero = bool((show_name or "").strip() or (episode_label or "").strip())
    header_fade_in_at = HERO_HANDOFF_START + HERO_HANDOFF_DURATION - 0.25 if has_hero else 0.3
    header_html, header_js = _header_html(show_name, episode_label, total_duration, header_fade_in_at,
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
    root_classes = " ".join(filter(None, [
        "framed" if lay.get("art") else "",
        "light-frame" if field_is_light else "",
    ]))
    framed_css = ".framed .bg-media { opacity: 1; }" if lay.get("art") else ""

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <title>{_esc(show_name.strip() + " Promo") if (show_name or "").strip() else "Promo"}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>{BASE_CSS}{visuals.CSS}{styles.text_css(style, lay, field_is_light)}{framed_css}</style>
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


def render_video(project_dir: str, output_rel_path: str, quality: str = "standard") -> str:
    """Runs the pinned hyperframes CLI against project_dir/index.html, returns the absolute output path."""
    cmd = [
        "npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render",
        "-q", quality, "-o", output_rel_path,
    ]
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



def _has_audio(path: str) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())
