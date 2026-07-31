import html
import math
import os
import subprocess

import languages
import styles
import visuals

HYPERFRAMES_VERSION = "0.7.70"

OUTRO_DURATION = 5
# The operator's own mark. Only used when the requesting session is the
# operator; every other job either uses an uploaded logo or shows none.
DEFAULT_LOGO_PATH = "server/static/kz-logo.png"

# The composition is written to PROJECT_DIR/index.html and loaded from there,
# so media has to be referenced relative to that directory -- the same way
# DEFAULT_LOGO_PATH already is. Absolute filesystem paths in a src attribute resolve
# against the document's origin and 404.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
.header {
  position: absolute; top: 0; left: 0; width: 1080px; z-index: 30;
  padding: 110px 60px 0;
}
.header-inner {
  display: flex; align-items: flex-start; justify-content: space-between; width: 100%;
  opacity: 0;
}
.header-show {
  font-size: 32px; font-weight: 900; letter-spacing: 2px; text-transform: uppercase;
  text-shadow: 0 3px 14px rgba(0,0,0,0.85);
}
.header-episode {
  font-size: 19px; font-weight: 600; letter-spacing: 3px; text-transform: uppercase;
  opacity: 0.7; margin-top: 4px; text-shadow: 0 3px 14px rgba(0,0,0,0.85);
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




def _scene_html(index: int, start: float, duration: float, track: dict, media: dict, palette: dict, audio_duration: float | None = None, hero: tuple[str, str] | None = None, motion: dict | None = None, language: str = "en", style: dict | None = None) -> tuple[str, str, str]:
    """Returns (scene_div_html, media_tags_html, scene_js). Media tags must be direct
    children of the stage (siblings of .scene divs) — the framework cannot manage
    playback of <video>/<audio> nested inside another timed element."""
    scene_id = f"scene-{index}"
    pal = palette
    style = style or styles.get(None)
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
    bg_style = (
        "" if has_visual
        else f"background: radial-gradient(circle at center, {pal['bg1']} 0%, {pal['bg2']} 100%);"
    )

    media_html = ""
    if media.get("video"):
        media_html += (
            f'<video id="media-{index}" class="clip bg-media" muted playsinline preload="auto" '
            f'data-start="{start}" data-duration="{duration}" data-track-index="1" '
            f'src="{_esc(_rel(media["video"]))}"></video>\n'
        )
    elif media.get("image"):
        # Artwork is animated every frame by the visuals runtime (Ken Burns +
        # bass breath), so it uses .art-media and carries no CSS transition --
        # a transition would key off real elapsed time and break determinism.
        media_html += visuals.art_html(index, start, duration, _esc(_rel(media["image"])))

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
    scene_html = f"""
      <div id="{scene_id}" class="clip scene" style="{bg_style}" data-start="{start}" data-duration="{duration}" data-track-index="0">
        <div class="scrim"></div>
        <div id="orb-a-{index}" class="orb orb-a" style="background: {pal['orb1']};"></div>
        <div id="orb-b-{index}" class="orb orb-b" style="background: {pal['orb2']};"></div>
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

    scene_js = f"""
      {entrance_js}
        .fromTo("#orb-a-{index}, #orb-b-{index}", {{ opacity: 0 }}, {{ opacity: 0.35, duration: 1.2 }}, {start})
        .to("#orb-a-{index}", {{ x: {orb_ax}, y: {orb_ay}, duration: {orb_a_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_a_cycle)}, ease: "sine.inOut" }}, {start})
        .to("#orb-b-{index}", {{ x: {orb_bx}, y: {orb_by}, duration: {orb_b_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_b_cycle)}, ease: "sine.inOut" }}, {start})
        .to("#progress-{index}", {{ scaleX: 1, duration: {max(duration - 0.6, 0.5)}, ease: "linear" }}, {start + 0.3})
        .to({text_sel}, {entrance['exit']}, {exit_start})
        .set({text_sel}, {{ opacity: 0 }}, {start + duration})
        .set("#orb-a-{index}, #orb-b-{index}", {{ opacity: 0 }}, {start + duration});
    """
    # Artwork gets no GSAP tween here. Its Ken Burns push, pan and bass-driven
    # breath are all written per frame by the visuals runtime, which owns the
    # #art-N elements outright -- a tween on the same transform would fight it,
    # and would target a #media-N element that no longer exists for stills.
    if media.get("video"):
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
# ~110px from top, 32px/19px type) -- NOT arbitrary numbers. Recompute both if
# either element's CSS position/size changes.
HERO_TO_HEADER_SHOW = {"x": -400, "y": -300, "scale": 0.31}
HERO_TO_HEADER_EPISODE = {"x": -400, "y": -370, "scale": 0.6}


def _header_html(show_name: str, episode_label: str, total_duration: float, fade_in_at: float, outro_start: float | None = None, logo_path: str | None = None) -> tuple[str, str]:
    # Alignment follows the show/episode name's own script, not the on-screen
    # language toggle -- an English show name shouldn't right-align just
    # because Hebrew is selected for the UI strings elsewhere.
    rtl = ' dir="rtl"' if _looks_hebrew(show_name) or _looks_hebrew(episode_label) else ""
    logo_html = f'<img class="header-logo" src="{_esc(_rel(logo_path))}" alt="" />' if logo_path else ""
    header_html = f"""
      <div id="header" class="clip header" data-start="0" data-duration="{total_duration}" data-track-index="20">
        <div id="header-inner" class="header-inner">
          <div{rtl}>
            <div id="header-show" class="header-show">{_esc(show_name)}</div>
            <div id="header-episode" class="header-episode">{_esc(episode_label)}</div>
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


def _hero_html(show_name: str, episode_label: str) -> tuple[str, str]:
    """Big centered show/episode reveal shown only at the very start of scene 0,
    then flies up and shrinks toward the header's corner as it fades — reads as
    the same text handing off to the persistent header rather than two unrelated
    elements swapping."""
    rtl = ' dir="rtl"' if _looks_hebrew(show_name) or _looks_hebrew(episode_label) else ""
    hero_html = f"""
        <div class="hero-container"{rtl}>
          <div id="hero-show" class="hero-show">{_esc(show_name)}</div>
          <div id="hero-episode" class="hero-episode">{_esc(episode_label)}</div>
        </div>
    """
    handoff_end = HERO_HANDOFF_START + HERO_HANDOFF_DURATION
    s = HERO_TO_HEADER_SHOW
    e = HERO_TO_HEADER_EPISODE
    hero_js = f"""
      tl.fromTo("#hero-show", {{ opacity: 0, y: 50, scale: 0.85 }}, {{ opacity: 1, y: 0, scale: 1, duration: 0.9, ease: "back.out(1.6)" }}, 0.15)
        .fromTo("#hero-episode", {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.8, ease: "power3.out" }}, 0.4)
        .to("#hero-show", {{ opacity: 0, x: {s['x']}, y: {s['y']}, scale: {s['scale']}, duration: {HERO_HANDOFF_DURATION}, ease: "power2.inOut" }}, {HERO_HANDOFF_START})
        .to("#hero-episode", {{ opacity: 0, x: {e['x']}, y: {e['y']}, scale: {e['scale']}, duration: {HERO_HANDOFF_DURATION}, ease: "power2.inOut" }}, {HERO_HANDOFF_START + 0.06})
        .set("#hero-show, #hero-episode", {{ opacity: 0 }}, {handoff_end});
    """
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
    scene_html = f"""
      <div id="outro" class="clip scene" style="{bg_style}" data-start="{start}" data-duration="{duration}" data-track-index="0">
        <div id="outro-orb-a" class="orb orb-a" style="background: {pal['orb1']};"></div>
        <div id="outro-orb-b" class="orb orb-b" style="background: {pal['orb2']};"></div>
        <div class="outro-brand">
          {logo_html}
          <div id="outro-show" class="outro-show">{_esc(show_name)}</div>
          <div id="outro-episode" class="outro-episode">{_esc(episode_label)}</div>
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
    scene_js = f"""
      tl.fromTo("#outro-orb-a, #outro-orb-b", {{ opacity: 0 }}, {{ opacity: 0.35, duration: 1.4 }}, {start})
        .to("#outro-orb-a", {{ x: {orb_ax}, y: {orb_ay}, duration: {orb_a_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_a_cycle)}, ease: "sine.inOut" }}, {start})
        .to("#outro-orb-b", {{ x: {orb_bx}, y: {orb_by}, duration: {orb_b_cycle}, yoyo: true, repeat: {_loop_repeat(duration, orb_b_cycle)}, ease: "sine.inOut" }}, {start})
        {logo_tween}
        .fromTo("#outro-show", {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.8, ease: "power3.out" }}, {start + 0.3})
        .fromTo("#outro-episode", {{ opacity: 0, y: 16 }}, {{ opacity: 1, y: 0, duration: 0.7, ease: "power3.out" }}, {start + 0.45})
        .fromTo("#outro-title", {{ opacity: 0, y: 40 }}, {{ opacity: 1, y: 0, duration: 1, ease: "power3.out" }}, {start + 0.65})
        .fromTo("#also-featuring", {{ opacity: 0, y: 30 }}, {{ opacity: 1, y: 0, duration: 1.1, ease: "power3.out" }}, {start + 0.95});
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
        sh, mh, sj = _scene_html(i, cursor, scene_duration, item["track"], item["media"], pal, audio_duration=audio_duration, hero=hero, motion=motion, language=language, style=style)
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
    header_fade_in_at = HERO_HANDOFF_START + HERO_HANDOFF_DURATION - 0.25
    header_html, header_js = _header_html(show_name, episode_label, total_duration, header_fade_in_at,
                                         outro_start=total_duration - OUTRO_DURATION,
                                         logo_path=logo_path)
    scenes_js.append(header_js)
    frame_html = _frame_overlay_html(frame, total_duration, palette[0]["accent"])
    visuals_js = visuals.runtime_js(
        visual_scenes, total_duration,
        patch=style["patch"],
        accent_hex=palette[0]["accent"],
        transition=style.get("transition", "fade"),
    )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <title>{_esc(show_name)} Promo</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>{BASE_CSS}{visuals.CSS}{styles.text_css(style)}</style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total_duration}" data-width="1080" data-height="1920">
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


def render_video(project_dir: str, output_rel_path: str, quality: str = "standard") -> str:
    """Runs the pinned hyperframes CLI against project_dir/index.html, returns the absolute output path."""
    cmd = [
        "npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render",
        "-q", quality, "-o", output_rel_path,
    ]
    result = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"hyperframes render failed:\n{result.stdout}\n{result.stderr}")
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
