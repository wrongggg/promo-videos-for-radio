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

THEME_FROM_TRACKLIST_PROMPT = """Here is a radio show tracklist:
{tracklist}

Based on the genre/mood of this music, invent a cohesive visual theme for a 9:16 promo
video: dark, high-contrast, neon-adjacent backgrounds work best on video footage (avoid
pale/light backgrounds — text and footage need to stay legible).

Propose {n} scene palettes to rotate through, each shaped like:
{{"bg1": "#hex", "bg2": "#hex", "accent": "#hex", "accent2": "#hex", "orb1": "#hex", "orb2": "#hex"}}
bg1/bg2 form a dark radial-gradient background (bg1 center, bg2 edge — bg2 should be
near-black). accent/accent2 drive the progress bar and the generative backdrop -- NOT text
(all text is white or black), so they can be fully saturated.
orb1/orb2 are glow-blob colors, can be brighter/saturated.

Also choose:
- "motion": one of "calm", "normal", "energetic" -- how much the background glow-blobs drift
  and how strong the video/image zoom push is. Match the music's energy (e.g. ambient/downtempo
  -> calm, high-energy dance/punk -> energetic).
- "frame": one of "clean", "film-grain", "vignette-heavy", "glow-frame" -- an overall visual
  treatment. film-grain suits lo-fi/analog/rock genres, vignette-heavy suits moody/cinematic
  genres, glow-frame suits electronic/neon genres, clean suits anything minimal or unsure.

- "style": one of the named looks below. This picks the typography, where the text
  sits, how it animates and what generative pattern runs behind it -- it matters more
  to the finished video than the palette does, so choose it on the music:
{style_menu}

Respond with ONLY a JSON object shaped like:
{{"palettes": [...{n} palette objects...], "motion": "...", "frame": "...", "style": "..."}}
No other text.
"""

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

Respond with ONLY a JSON object shaped like:
{{"palettes": [...{n} palette objects...], "motion": "...", "frame": "...", "style": "..."}}
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

# The five themes the user picks between. Each pairs a visual style (type,
# layout, animation, synth patch -- see styles.py) with a palette tuned to it,
# so choosing "XL" changes the whole look rather than just the colours. Keyed by
# style key; the human label and blurb live in styles.py so the picker and the
# renderer can never disagree about what a theme is.
PRESET_THEMES = {
    "classic": {
        "style": "classic", "motion": "normal", "frame": "clean",
        "palettes": [
            {"bg1": "#141a2e", "bg2": "#04060d", "accent": "#7aa2ff", "accent2": "#a8c0ff", "orb1": "#3d5a99", "orb2": "#6d7fa8"},
            {"bg1": "#1a1725", "bg2": "#05040a", "accent": "#9d8cff", "accent2": "#c3b8ff", "orb1": "#4a3f7a", "orb2": "#7a6da8"},
            {"bg1": "#101e22", "bg2": "#030809", "accent": "#6fd3c7", "accent2": "#a5e5dd", "orb1": "#2f6b64", "orb2": "#5c9a93"},
        ],
    },
    "poppy": {
        "style": "poppy", "motion": "energetic", "frame": "clean",
        "palettes": [
            {"bg1": "#2b0a3d", "bg2": "#0a0210", "accent": "#ff2e88", "accent2": "#ffd166", "orb1": "#ff2e88", "orb2": "#ffd166"},
            {"bg1": "#062a3d", "bg2": "#01080d", "accent": "#00e5ff", "accent2": "#ff6b9d", "orb1": "#00e5ff", "orb2": "#ff6b9d"},
            {"bg1": "#3d1400", "bg2": "#0d0400", "accent": "#ff9f1c", "accent2": "#ffe066", "orb1": "#ff9f1c", "orb2": "#ff5e5b"},
        ],
    },
    "xl": {
        "style": "xl", "motion": "energetic", "frame": "vignette-heavy",
        "palettes": [
            {"bg1": "#1a1a1a", "bg2": "#000000", "accent": "#ffffff", "accent2": "#bdbdbd", "orb1": "#4a4a4a", "orb2": "#8a8a8a"},
            {"bg1": "#2a0d0d", "bg2": "#080202", "accent": "#ff3b30", "accent2": "#ffffff", "orb1": "#a01c16", "orb2": "#5c5c5c"},
            {"bg1": "#0d1a2a", "bg2": "#020508", "accent": "#4dabf7", "accent2": "#ffffff", "orb1": "#1c5aa0", "orb2": "#5c5c5c"},
        ],
    },
    "editorial": {
        "style": "editorial", "motion": "calm", "frame": "film-grain",
        "palettes": [
            {"bg1": "#1c1a17", "bg2": "#050403", "accent": "#d8cfc0", "accent2": "#8a8378", "orb1": "#3d382f", "orb2": "#6b6459"},
            {"bg1": "#17191c", "bg2": "#030405", "accent": "#c8cdd4", "accent2": "#7c838c", "orb1": "#32373d", "orb2": "#5c636b"},
        ],
    },
    "ambient": {
        "style": "ambient", "motion": "calm", "frame": "vignette-heavy",
        "palettes": [
            {"bg1": "#0a1f2a", "bg2": "#010507", "accent": "#8fd4e8", "accent2": "#c9e9f2", "orb1": "#2a6b85", "orb2": "#5ca3bd"},
            {"bg1": "#141a2a", "bg2": "#030509", "accent": "#a8b8e0", "accent2": "#d6def0", "orb1": "#3a4a7a", "orb2": "#6b7aa8"},
            {"bg1": "#1a1420", "bg2": "#050308", "accent": "#c4a8d4", "accent2": "#e2d3ea", "orb1": "#5a3f6b", "orb2": "#8a6da0"},
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
    return {"palettes": palettes, "motion": motion, "frame": frame, "style": style}


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
    },
    "required": ["palettes", "motion", "frame", "style"],
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


def suggest_theme(tracks: list[Track], n: int = 4, job_id: str | None = None, model: str = MODEL_SIMPLE) -> dict:
    """Auto-generate a full theme (palette rotation + motion + frame) matching the
    tracklist's genre/mood."""
    try:
        tracklist_text = "\n".join(t.label() for t in tracks)
        prompt = THEME_FROM_TRACKLIST_PROMPT.format(tracklist=tracklist_text, n=n, style_menu=STYLE_MENU)
        return _generate_theme(prompt, job_id=job_id, model=model) or DEFAULT_THEME
    except Exception:
        return DEFAULT_THEME


def theme_from_description(description: str, n: int = 4, job_id: str | None = None, model: str = MODEL_SIMPLE) -> dict:
    """Generate a full theme from a free-text description."""
    prompt = THEME_FROM_DESCRIPTION_PROMPT.format(description=description, n=n, style_menu=STYLE_MENU)
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
    theme = suggest_theme(sample)
    print(json.dumps(theme, indent=2))
