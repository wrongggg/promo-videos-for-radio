import json
import os
import re

import anthropic

import analytics
import languages
import styles
from track import Track

# "Simple" (default, used for everyone but the admin's own advanced runs) is
# priced to be cheap at real usage volume; "Advanced" trades cost for quality.
MODEL_SIMPLE = "claude-sonnet-5"
MODEL_ADVANCED = "claude-opus-4-8"


POPLOCK_SHOW_CONTEXT = """
Extra context on this specific show: this is "Pop Lock" on KZ Radio (Tel Aviv's Radio
HaKatze), hosted by Roni Fialkov. The show's own tagline: "a place to fly far away, and
also come back -- important that along the way it's weird, big and fun, extreme, catchy
and airy. Brain-dance." Roni's taste runs almost entirely underground/leftfield electronic
-- deep house, techno, dub, breaks, braindance/IDM textures, occasionally a vocal-forward
"song" cut -- rarely anything mainstream or chart-pop. Remixes and alternate mixes are
extremely common on this show and are crate-digger picks, not radio edits -- weigh a
well-regarded remix/version exactly as "standout" as an original, never lesser just for
being a remix. Favor specific, real, obscure-but-verifiable artist/label context (a small
label's first vinyl run, a DJ's b2b history, a scene lineage) over generic "buzzy" claims
that would fit any track.
"""

CURATION_PROMPT = """You are helping curate a promo video for a radio show episode.
{show_context}
Here is the full tracklist for this episode:
{tracklist}

Rank up to {n} standout tracks to feature prominently in a short promo video, best first.
Prioritize tracks that are notable for one of these reasons: a buzzy new release,
a well-known or rising artist, a remix worth highlighting, or a track that fits a
recurring theme/genre on this show. {research_instruction}

Respond with ONLY a JSON array (no other text) of up to {n} objects, best first, each shaped like:
{{"artist": "...", "title": "...", "reason": "..."}}

The artist/title must exactly match one of the tracklist entries above. Write "reason" in
{language} -- but only if you have a genuinely interesting, specific, true fact or claim
worth showing on screen (e.g. "first new album in 20 years", a major award, a striking
festival/chart milestone). Most tracks won't have one, and that's fine -- use an empty
string for "reason" rather than inventing generic filler like "certified festival anthem"
or "rising artist". Keep it short and concrete -- one clause, no preamble. Never open with
a generic referent like "the track"/"the song" or its Hebrew equivalent ("השיר") -- the
track is already shown on screen, so state the fact itself directly.
"""

RESEARCH_INSTRUCTION_WITH_SEARCH = (
    "Use web search to check whether an artist or track has recent buzz (new release, "
    "chart movement, festival booking, etc.) before deciding. If you're unsure about a "
    "track, prefer ones you can say something specific and true about over generic picks."
)
RESEARCH_INSTRUCTION_NO_SEARCH = (
    "Rely on your own knowledge of these artists and tracks -- no web search available "
    "for this request. If you're unsure whether a claim is true, prefer a track you're "
    "confident about, or leave \"reason\" empty rather than guessing."
)


# The style menu handed to the model, generated from styles.py so a new or
# renamed style shows up in the prompt automatically.
STYLE_MENU = "\n".join(
    f'- "{k}": {v["blurb"]}' for k, v in styles.STYLES.items()
)

# Same, for the layout half of a theme.
LAYOUT_MENU = "\n".join(
    f'- "{k}": {v["blurb"]}' for k, v in styles.LAYOUTS.items()
)

THEME_FROM_DESCRIPTION_PROMPT = """A user wants a custom visual theme for a 9:16 promo video
overlaid on video footage, described in their own words as:
"{description}"

Design a cohesive dark, high-contrast palette matching that description (avoid pale/light
backgrounds — text and footage need to stay legible). Propose {n} scene palettes to rotate
through, each shaped like:
{{"bg1": "#hex", "bg2": "#hex", "accent": "#hex", "accent2": "#hex", "orb1": "#hex", "orb2": "#hex"}}
bg1/bg2 form a dark radial-gradient background (bg1 center, bg2 edge — bg2 should be
near-black). accent/accent2 drive the progress bar and the generative backdrop -- NOT text
(all text is white or black), so they can be fully saturated.
orb1/orb2 are glow-blob colors, can be brighter/saturated.

Also choose, matching the description's mood:
- "motion": one of "calm", "normal", "energetic" -- how much the background glow-blobs drift
  and how strong the video/image zoom push is.
- "frame": one of "clean", "film-grain", "vignette-heavy", "glow-frame" -- an overall visual
  treatment.

- "style": one of the named looks below. This picks the typography, where the text
  sits, how it animates and what generative pattern runs behind it -- it matters more
  to the finished video than the palette does, so choose it on the music:
{style_menu}

- "layout": where the release artwork sits in the frame. This matters as much as the
  style -- it decides whether the promo looks like a poster, a magazine page or a
  screen. Note that four of these stand the artwork on a flat colour field sampled
  from the sleeve, so the palette above is doing less work in those:
{layout_menu}

- "transition": how the artwork changes between tracks -- one of "fade", "slide",
  "zoom", "swap" (a hard cut), "spin", "dissolve" (the two sleeves blur through each
  other) or "pixelate" (one breaks into blocks as the next resolves out of them).

Respond with ONLY a JSON object shaped like:
{{"palettes": [...{n} palette objects...], "motion": "...", "frame": "...",
  "style": "...", "layout": "...", "transition": "..."}}
No other text.
"""

DEFAULT_PALETTE = [
    {"bg1": "#1e0b36", "bg2": "#07020d", "accent": "#ff007f", "accent2": "#00f0ff", "orb1": "#ff007f", "orb2": "#00f0ff"},
    {"bg1": "#2c0a1c", "bg2": "#0c0208", "accent": "#fb8500", "accent2": "#ffb703", "orb1": "#9d4edd", "orb2": "#fb8500"},
    {"bg1": "#0a242c", "bg2": "#01080a", "accent": "#00b4d8", "accent2": "#3a0ca3", "orb1": "#00f0ff", "orb2": "#3a0ca3"},
    {"bg1": "#22092e", "bg2": "#05010a", "accent": "#c77dff", "accent2": "#ff477e", "orb1": "#c77dff", "orb2": "#ff477e"},
]

MOTION_KEYS = ("calm", "normal", "energetic")
FRAME_KEYS = ("clean", "film-grain", "vignette-heavy", "glow-frame")

DEFAULT_THEME = {"palettes": DEFAULT_PALETTE, "motion": "normal", "frame": "clean",
                 "style": styles.DEFAULT_STYLE}
# Fallback when a saved/custom theme can't be resolved. A real preset rather
# than DEFAULT_THEME so the result is a look someone actually designed.
DEFAULT_PRESET = "classic"

# The themes the user picks between -- one list, one decision. Each pairs a
# typographic style with a layout (where the sleeve sits) and a palette tuned
# to both, so picking "Catalogue" changes the whole frame and not just the
# colours.
#
# Style and layout stay separate vocabularies inside styles.py because that is
# what stops them multiplying: eleven type treatments and seven layouts compose
# into as many themes as are worth having, without eleven near-copies of each
# geometry. But that is an authoring convenience and nothing the user should
# ever be asked to assemble -- they choose a look, not a pair of dropdowns.
#
# Layouts are deliberately mixed across the list rather than grouped, so
# scanning the picker reads as a set of different products.
PRESET_THEMES = {
    "classic": {
        "label": "Classic", "style": "classic", "layout": "bleed",
        "motion": "normal", "frame": "clean",
        "palettes": [
            {"bg1": "#141a2e", "bg2": "#04060d", "accent": "#7aa2ff", "accent2": "#a8c0ff", "orb1": "#3d5a99", "orb2": "#6d7fa8"},
            {"bg1": "#1a1725", "bg2": "#05040a", "accent": "#9d8cff", "accent2": "#c3b8ff", "orb1": "#4a3f7a", "orb2": "#7a6da8"},
            {"bg1": "#101e22", "bg2": "#030809", "accent": "#6fd3c7", "accent2": "#a5e5dd", "orb1": "#2f6b64", "orb2": "#5c9a93"},
        ],
    },
    "halo": {
        "label": "Halo", "style": "classic", "layout": "canvas", "transition": "dissolve",
        "motion": "calm", "frame": "clean",
        "palettes": [
            {"bg1": "#12141c", "bg2": "#04050a", "accent": "#9db4ff", "accent2": "#d5deff", "orb1": "#3a4470", "orb2": "#6b76a8"},
            {"bg1": "#101c1e", "bg2": "#030708", "accent": "#7fd8cd", "accent2": "#c4ece7", "orb1": "#2c6159", "orb2": "#569089"},
        ],
    },
    "bulletin": {
        "label": "Bulletin", "style": "swiss", "layout": "press",
        "motion": "calm", "frame": "clean",
        "palettes": [
            {"bg1": "#191b1e", "bg2": "#040506", "accent": "#c6ccd2", "accent2": "#8b9299", "orb1": "#343a40", "orb2": "#5b6268"},
            {"bg1": "#141a1c", "bg2": "#030506", "accent": "#a8c4c9", "accent2": "#d5e2e4", "orb1": "#2c4448", "orb2": "#527076"},
        ],
    },
    "gallery": {
        "label": "Gallery", "style": "plate", "layout": "gallery",
        "motion": "calm", "frame": "clean",
        "palettes": [
            {"bg1": "#2a1c1c", "bg2": "#080505", "accent": "#e8c4b8", "accent2": "#f5e6de", "orb1": "#7a4a42", "orb2": "#b08278"},
            {"bg1": "#1c2027", "bg2": "#050607", "accent": "#cdd6e0", "accent2": "#eef2f6", "orb1": "#3d4756", "orb2": "#6d7a8c"},
        ],
    },
    "xl": {
        "label": "XL", "style": "xl", "layout": "bleed",
        "motion": "energetic", "frame": "vignette-heavy",
        "palettes": [
            {"bg1": "#1a1a1a", "bg2": "#000000", "accent": "#ffffff", "accent2": "#bdbdbd", "orb1": "#4a4a4a", "orb2": "#8a8a8a"},
            {"bg1": "#2a0d0d", "bg2": "#080202", "accent": "#ff3b30", "accent2": "#ffffff", "orb1": "#a01c16", "orb2": "#5c5c5c"},
            {"bg1": "#0d1a2a", "bg2": "#020508", "accent": "#4dabf7", "accent2": "#ffffff", "orb1": "#1c5aa0", "orb2": "#5c5c5c"},
        ],
    },
    "slab": {
        "label": "Slab", "style": "stack", "layout": "press",
        "motion": "energetic", "frame": "clean",
        "palettes": [
            {"bg1": "#2d0616", "bg2": "#0a0105", "accent": "#ff2d55", "accent2": "#ffd60a", "orb1": "#c9184a", "orb2": "#ffb703"},
            {"bg1": "#12002e", "bg2": "#04000a", "accent": "#7b2cff", "accent2": "#00f5d4", "orb1": "#5a189a", "orb2": "#06d6a0"},
        ],
    },
    "coverline": {
        "label": "Cover Line", "style": "masthead", "layout": "split",
        "motion": "normal", "frame": "clean",
        "palettes": [
            {"bg1": "#1b1b1b", "bg2": "#000000", "accent": "#e5301c", "accent2": "#ffffff", "orb1": "#7a1a10", "orb2": "#4a4a4a"},
            {"bg1": "#141618", "bg2": "#000000", "accent": "#ffffff", "accent2": "#e5301c", "orb1": "#3a3d40", "orb2": "#7a1a10"},
        ],
    },
    "terminal": {
        "label": "Terminal", "style": "terminal", "layout": "bleed", "transition": "pixelate",
        "motion": "normal", "frame": "film-grain",
        "palettes": [
            {"bg1": "#0a1a0f", "bg2": "#010402", "accent": "#4ade80", "accent2": "#a7f3d0", "orb1": "#166534", "orb2": "#3f8f5f"},
            {"bg1": "#0f1a1a", "bg2": "#010404", "accent": "#5eead4", "accent2": "#ccfbf1", "orb1": "#115e59", "orb2": "#3f8f8a"},
        ],
    },
    "catalogue": {
        "label": "Catalogue", "style": "index", "layout": "gallery", "transition": "pixelate",
        "motion": "normal", "frame": "clean",
        "palettes": [
            {"bg1": "#16181a", "bg2": "#020303", "accent": "#f2f2ef", "accent2": "#9aa0a6", "orb1": "#2e3235", "orb2": "#565c61"},
            {"bg1": "#1a1710", "bg2": "#040302", "accent": "#e8b923", "accent2": "#f2f2ef", "orb1": "#5c4a12", "orb2": "#8a7a3f"},
        ],
    },
    "pop": {
        "label": "Pop", "style": "poppy", "layout": "canvas",
        "motion": "energetic", "frame": "clean",
        "palettes": [
            {"bg1": "#2b0a3d", "bg2": "#0a0210", "accent": "#ff2e88", "accent2": "#ffd166", "orb1": "#ff2e88", "orb2": "#ffd166"},
            {"bg1": "#062a3d", "bg2": "#01080d", "accent": "#00e5ff", "accent2": "#ff6b9d", "orb1": "#00e5ff", "orb2": "#ff6b9d"},
            {"bg1": "#3d1400", "bg2": "#0d0400", "accent": "#ff9f1c", "accent2": "#ffe066", "orb1": "#ff9f1c", "orb2": "#ff5e5b"},
        ],
    },
    "kinetic": {
        "label": "Kinetic", "style": "kinetic", "layout": "bleed",
        "motion": "energetic", "frame": "glow-frame",
        "palettes": [
            {"bg1": "#2a0a2a", "bg2": "#08020a", "accent": "#f0abfc", "accent2": "#fde047", "orb1": "#c026d3", "orb2": "#eab308"},
            {"bg1": "#0a2a2a", "bg2": "#02080a", "accent": "#22d3ee", "accent2": "#fb923c", "orb1": "#0e7490", "orb2": "#ea580c"},
        ],
    },
    "tide": {
        "label": "Tide", "style": "tidal", "layout": "strip", "transition": "dissolve",
        "motion": "calm", "frame": "vignette-heavy",
        "palettes": [
            {"bg1": "#0a1e2e", "bg2": "#010508", "accent": "#7dd3fc", "accent2": "#e0f2fe", "orb1": "#0c4a6e", "orb2": "#3d7f9e"},
            {"bg1": "#0f1a2e", "bg2": "#020408", "accent": "#93c5fd", "accent2": "#dbeafe", "orb1": "#1e3a8a", "orb2": "#4f6fa8"},
        ],
    },
    "offcut": {
        "label": "Off Cut", "style": "stack", "layout": "offset",
        "motion": "energetic", "frame": "clean",
        "palettes": [
            {"bg1": "#231005", "bg2": "#080301", "accent": "#ff7a18", "accent2": "#ffd166", "orb1": "#a33d06", "orb2": "#d98324"},
            {"bg1": "#04121f", "bg2": "#010507", "accent": "#00c2ff", "accent2": "#b8ecff", "orb1": "#0a3f61", "orb2": "#2b7fa8"},
        ],
    },
    "plate": {
        "label": "Plate", "style": "plate", "layout": "press", "transition": "dissolve",
        "motion": "calm", "frame": "film-grain",
        "palettes": [
            {"bg1": "#241a1a", "bg2": "#070505", "accent": "#e0bcae", "accent2": "#f6ebe4", "orb1": "#6f4239", "orb2": "#a67a6d"},
            {"bg1": "#1a1d24", "bg2": "#040506", "accent": "#c3ccd8", "accent2": "#e9eef4", "orb1": "#38414f", "orb2": "#667287"},
        ],
    },
    "swiss": {
        "label": "Swiss", "style": "swiss", "layout": "gallery",
        "motion": "calm", "frame": "clean",
        "palettes": [
            {"bg1": "#1a1c1f", "bg2": "#040506", "accent": "#b9c0c7", "accent2": "#e3e7ea", "orb1": "#2f353b", "orb2": "#585f66"},
            {"bg1": "#171a1a", "bg2": "#030404", "accent": "#9fbdb8", "accent2": "#d8e8e5", "orb1": "#2a4340", "orb2": "#4f716c"},
        ],
    },
    "marquee": {
        "label": "Marquee", "style": "masthead", "layout": "strip", "transition": "pixelate",
        "motion": "normal", "frame": "vignette-heavy",
        "palettes": [
            {"bg1": "#191007", "bg2": "#050301", "accent": "#ffc300", "accent2": "#fff3c4", "orb1": "#7a5c00", "orb2": "#b08b1a"},
            {"bg1": "#0d1117", "bg2": "#020304", "accent": "#e6edf3", "accent2": "#9aa7b4", "orb1": "#2b3440", "orb2": "#525f6d"},
        ],
    },
    "split": {
        "label": "Split", "style": "xl", "layout": "split",
        "motion": "energetic", "frame": "clean",
        "palettes": [
            {"bg1": "#101010", "bg2": "#000000", "accent": "#ffffff", "accent2": "#ff3b30", "orb1": "#3d3d3d", "orb2": "#7a7a7a"},
            {"bg1": "#0a1420", "bg2": "#010203", "accent": "#7ee8fa", "accent2": "#ffffff", "orb1": "#154b63", "orb2": "#3f8296"},
        ],
    },
    "nightshift": {
        "label": "Night Shift", "style": "terminal", "layout": "canvas", "transition": "dissolve",
        "motion": "calm", "frame": "film-grain",
        "palettes": [
            {"bg1": "#0b1412", "bg2": "#010302", "accent": "#5eead4", "accent2": "#a7f3d0", "orb1": "#10453f", "orb2": "#2f7a71"},
            {"bg1": "#0f1118", "bg2": "#020203", "accent": "#a5b4fc", "accent2": "#e0e7ff", "orb1": "#2a3157", "orb2": "#525c8f"},
        ],
    },
}


HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PALETTE_KEYS = ("bg1", "bg2", "accent", "accent2", "orb1", "orb2")


def _valid_palette(entries) -> list[dict] | None:
    if not isinstance(entries, list) or not entries:
        return None
    cleaned = []
    for e in entries:
        if not isinstance(e, dict):
            return None
        if not all(k in e and isinstance(e[k], str) and HEX_RE.match(e[k]) for k in PALETTE_KEYS):
            return None
        cleaned.append({k: e[k] for k in PALETTE_KEYS})
    return cleaned or None


def _valid_theme(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    palettes = _valid_palette(data.get("palettes"))
    if not palettes:
        return None
    motion = data.get("motion") if data.get("motion") in MOTION_KEYS else "normal"
    frame = data.get("frame") if data.get("frame") in FRAME_KEYS else "clean"
    style = data.get("style") if data.get("style") in styles.STYLE_KEYS else styles.DEFAULT_STYLE
    # A theme carries its own layout. Saved themes written before layouts
    # existed have no such key, and a described theme may not name one, so both
    # land on the default rather than on nothing -- compose.styles.layout()
    # would fall back anyway, but an explicit value keeps saved JSON readable.
    lay = data.get("layout") if data.get("layout") in styles.LAYOUT_KEYS else styles.DEFAULT_LAYOUT
    trans = data.get("transition") if data.get("transition") in styles.TRANSITIONS else None
    theme = {"palettes": palettes, "motion": motion, "frame": frame,
             "style": style, "layout": lay}
    # Left off entirely when unset, so the style's own choice still applies --
    # storing a null here would look like a deliberate "no transition".
    if trans:
        theme["transition"] = trans
    return theme


def curate(tracks: list[Track], n: int = 5, previous_shows: list | None = None) -> list[dict]:
    """Pick standout tracks from a tracklist using web search for context.

    previous_shows is an unused extension point for future recurring-artist/theme
    detection against the show's back catalog.
    """
    return curate_ranked(tracks, n=n, previous_shows=previous_shows)[:n]


RANKED_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                    "reason": {"type": "string", "description": "one short punchy phrase, not a full sentence"},
                },
                "required": ["artist", "title", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}


def curate_ranked(tracks: list[Track], n: int = 5, previous_shows: list | None = None, language: str = "en", job_id: str | None = None, personal: bool = False, use_search: bool = True, model: str = MODEL_SIMPLE) -> list[dict]:
    """Returns a ranked list of candidate tracks (best first), longer than n when the
    tracklist allows it, so the caller can pull backups to satisfy constraints (e.g.
    ensuring enough picks have YouTube video) without a second API call. use_search
    controls whether the (billed) web_search tool is available -- callers restrict
    this to admin to keep the cost of colleague-triggered runs low."""
    pool_size = min(len(tracks), n + 6)
    if len(tracks) <= n:
        return [{"artist": t.artist, "title": t.title, "album": t.album, "reason": ""} for t in tracks]

    client = anthropic.Anthropic()
    tracklist_text = "\n".join(t.label() for t in tracks)
    language_name = languages.english_name(language)
    show_context = POPLOCK_SHOW_CONTEXT if personal else ""
    research_instruction = RESEARCH_INSTRUCTION_WITH_SEARCH if use_search else RESEARCH_INSTRUCTION_NO_SEARCH

    # output_config forces a valid JSON response no matter what the web-search tool
    # does mid-run (hits its usage cap, comes back empty, etc.) — without this the
    # model sometimes narrates around a tool hiccup instead of emitting pure JSON.
    kwargs = dict(
        model=model,
        max_tokens=8192,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": RANKED_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": CURATION_PROMPT.format(
                    tracklist=tracklist_text, n=pool_size, language=language_name,
                    show_context=show_context, research_instruction=research_instruction,
                ),
            }
        ],
    )
    if use_search:
        kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
    response = client.messages.create(**kwargs)
    analytics.record_api_call(job_id, "curate_ranked", response.usage, model=model)

    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise ValueError(f"Curator returned no output (stop_reason={response.stop_reason})")
    picks = json.loads(text)["picks"]

    by_key = {(t.artist.lower(), t.title.lower()): t for t in tracks}
    ranked = []
    for p in picks:
        key = (p.get("artist", "").lower(), p.get("title", "").lower())
        t = by_key.get(key)
        album = t.album if t else None
        ranked.append({"artist": p["artist"], "title": p["title"], "album": album, "reason": p.get("reason", "")})
    return ranked


TRIVIA_PROMPT = """Here is a list of specific tracks a user has already chosen to feature in
a radio show promo video:
{show_context}
{tracklist}

For each track, check whether there's a genuinely interesting, specific, true fact worth
showing on screen -- e.g. "first new album in 20 years", a major award, a notable
collaboration, a striking chart/festival milestone. This is NOT required for every track --
most won't have one, and that's fine. {research_instruction}

Respond with ONLY a JSON array (no other text) of exactly {n} objects, one per track in the
same order, each shaped like:
{{"artist": "...", "title": "...", "reason": "..."}}

Write each non-empty "reason" in {language}. Use an empty string for "reason" when there is
nothing genuinely notable -- never invent generic filler. Keep it short and concrete -- one
clause, no preamble. Never open with a generic referent like "the track"/"the song" or its
Hebrew equivalent ("השיר") -- the track is already shown on screen, so state the fact itself
directly.
"""

TRIVIA_RESEARCH_INSTRUCTION_WITH_SEARCH = "Use web search to verify anything before claiming it."
TRIVIA_RESEARCH_INSTRUCTION_NO_SEARCH = (
    "No web search available for this request -- rely on your own knowledge, and only "
    "include a fact you're confident is true. Leave \"reason\" empty rather than guessing."
)

TRIVIA_SCHEMA = {
    "type": "object",
    "properties": {
        "trivia": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["artist", "title", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["trivia"],
    "additionalProperties": False,
}


def trivia_for_tracks(tracks: list[Track], language: str = "en", job_id: str | None = None, personal: bool = False, use_search: bool = True, model: str = MODEL_SIMPLE) -> dict:
    """Given tracks the user picked manually (no ranking/selection needed), find any
    genuinely notable trivia per track. Best-effort: returns {} on any failure rather
    than blocking the pipeline over a nice-to-have. Keyed by (artist_lower, title_lower).
    use_search restricts the (billed) web_search tool to admin-triggered runs."""
    if not tracks:
        return {}
    try:
        client = anthropic.Anthropic()
        tracklist_text = "\n".join(t.label() for t in tracks)
        language_name = languages.english_name(language)
        show_context = POPLOCK_SHOW_CONTEXT if personal else ""
        research_instruction = TRIVIA_RESEARCH_INSTRUCTION_WITH_SEARCH if use_search else TRIVIA_RESEARCH_INSTRUCTION_NO_SEARCH
        kwargs = dict(
            model=model,
            max_tokens=6144,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": TRIVIA_SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": TRIVIA_PROMPT.format(
                        tracklist=tracklist_text, n=len(tracks), language=language_name,
                        show_context=show_context, research_instruction=research_instruction,
                    ),
                }
            ],
        )
        if use_search:
            kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}]
        response = client.messages.create(**kwargs)
        analytics.record_api_call(job_id, "trivia_for_tracks", response.usage, model=model)
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            return {}
        items = json.loads(text)["trivia"]
        return {
            (i.get("artist", "").lower(), i.get("title", "").lower()): i.get("reason", "")
            for i in items
        }
    except Exception:
        return {}


THEME_SCHEMA = {
    "type": "object",
    "properties": {
        "palettes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "bg1": {"type": "string", "description": "hex color, e.g. #1a0f2e"},
                    "bg2": {"type": "string", "description": "hex color, e.g. #050208"},
                    "accent": {"type": "string", "description": "hex color, e.g. #ff4d94"},
                    "accent2": {"type": "string", "description": "hex color, e.g. #00e5ff"},
                    "orb1": {"type": "string", "description": "hex color, e.g. #c026d3"},
                    "orb2": {"type": "string", "description": "hex color, e.g. #2563eb"},
                },
                "required": ["bg1", "bg2", "accent", "accent2", "orb1", "orb2"],
                "additionalProperties": False,
            },
        },
        "motion": {"type": "string", "enum": list(MOTION_KEYS)},
        "frame": {"type": "string", "enum": list(FRAME_KEYS)},
        "style": {"type": "string", "enum": list(styles.STYLE_KEYS)},
        "layout": {"type": "string", "enum": list(styles.LAYOUT_KEYS)},
        "transition": {"type": "string", "enum": list(styles.TRANSITIONS)},
    },
    "required": ["palettes", "motion", "frame", "style", "layout", "transition"],
    "additionalProperties": False,
}


def _generate_theme(prompt: str, job_id: str | None = None, model: str = MODEL_SIMPLE) -> dict | None:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": THEME_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    analytics.record_api_call(job_id, "generate_theme", response.usage, model=model)
    text = "".join(block.text for block in response.content if block.type == "text")
    return _valid_theme(json.loads(text))




def theme_from_description(description: str, n: int = 4, job_id: str | None = None, model: str = MODEL_SIMPLE) -> dict:
    """Generate a full theme from a free-text description."""
    prompt = THEME_FROM_DESCRIPTION_PROMPT.format(
        description=description, n=n, style_menu=STYLE_MENU, layout_menu=LAYOUT_MENU)
    theme = _generate_theme(prompt, job_id=job_id, model=model)
    if not theme:
        raise ValueError("Could not derive a valid theme from the model's response")
    return theme


if __name__ == "__main__":
    sample = [
        Track("Four Tet", "Baby"),
        Track("Bicep", "Glue"),
        Track("Overmono", "So U Kno"),
        Track("Fred again..", "Delilah"),
        Track("Jamie xx", "Life"),
        Track("Peggy Gou", "It Makes You Forget"),
        Track("DJ Koze", "Pick Up"),
        Track("Kaytranada", "10%"),
    ]
    picks = curate_ranked(sample, n=4)
    print(json.dumps(picks, indent=2))
    print(json.dumps(PRESET_THEMES[DEFAULT_PRESET], indent=2))
