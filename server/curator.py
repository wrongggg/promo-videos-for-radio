import datetime
import json
import math
import os
import re

import anthropic

import analytics
import languages
import providers
import styles
from track import Track

# Sonnet everywhere, deliberately. Opus was the "Advanced" model and cost ~2.5x for a
# job whose hard part -- finding a true, non-obvious connection -- Sonnet already does
# well. "Advanced" now means web research, not a bigger model.
MODEL_SIMPLE = "claude-sonnet-5"

# How many web searches the story call may spend; 0 disables the tool entirely.
#
# Measured on one tracklist: 10 searches cost $0.43-1.18 and took 207-362s, 3 searches
# $0.36/196s, none $0.068/70s. The fee is not the driver -- each result lands in context
# and drags the accumulated input along behind it (2.7K tokens with no search, 88K with
# three). Quality held without it: a no-search run got Warp's WAP1 catalogue number,
# Heaven 17's formation and Trevor Horn producing ABC all correct.
#
# A story's through-line is historical and structural, which the model knows well. What
# it can't know is anything past its training cutoff -- and that's the trivia layer's
# job, not the story's. Raise this if stories start coming back thin on new releases.
STORY_MAX_SEARCHES = 0

# How many tracks a story may feature. The model picks within this range as part of
# choosing the framing -- how many records a through-line needs is a property of the
# story, not a setting. Under three there is no arc; past six a promo drags (each track
# is one 6-11s scene, so six is already about fifty seconds).
MIN_FEATURED, MAX_FEATURED = 3, 6

# Coverage: how much of the tracklist a framing has to account for, as a fraction, and
# the confidence it is allowed to claim at each level.
#
# The prompt asked for this in prose for several iterations and it did not hold -- the
# model kept returning a framing that explained seven records out of twenty-one and
# grading it "strong". Prose can describe a threshold; only code can enforce one. So the
# model now states its coverage as a number in the schema, which forces the arithmetic to
# happen, and the ladder below decides what that number is worth. A framing under the
# floor is dropped outright rather than downgraded: a promo built on a footnote is not a
# weaker promo, it is the wrong promo.
#
# Half the tracklist is the floor because that is where a claim stops being about the
# episode and starts being about a corner of it. The ceilings are deliberately generous
# above that -- the point is to stop inflation, not to make "strong" unreachable.
STORY_MIN_COVERAGE = 0.5
COVERAGE_CEILINGS = ((0.75, "strong"), (0.6, "good"), (STORY_MIN_COVERAGE, "solid"))

# Beats that talk about the running order instead of the record.
#
# Same story as coverage: the prompt says a beat must be about the music or the artist,
# and the model still returns "first Bufiman cameo", "the Oslo selector's first solo turn
# of the night", "opens his night with a fresh cut before the callback lands". These are
# facts about the tracklist -- the viewer, who sees one track on screen at a time and has
# no idea what came before, learns nothing from them.
#
# The failures are formulaic enough to match. A beat that trips one of these is blanked
# rather than shown: the slot renders empty, which is what a track with nothing to say
# about it already does. Blanking loses a line; shipping loses the viewer.
#
# Patterns are matched case-insensitively against the beat. English only -- a Hebrew beat
# passes through unchecked, which is a known gap rather than a solved problem; the Hebrew
# equivalents are idiomatic enough that guessing at them would blank good lines.
META_BEAT_PATTERNS = tuple(re.compile(p, re.I) for p in (
    # "...of the night", "in the set", "in this episode"
    r"\b(?:of|in) (?:the|this) (?:night|set|show|episode|mix|hour|lineup|list)\b",
    # "first of two", "the third appearance", "his first solo turn", "second cameo"
    r"\b(?:first|second|third|fourth|last|final)\b[^.;]{0,28}"
    r"\b(?:of (?:two|three|four)|appearance|cameo|turn|outing|credit|billing)\b",
    # "opens the night", "closes the set", "kicks us off"
    r"\b(?:opens|closes|starts|ends|kicks|caps|bookends)\b[^.;]{0,20}"
    r"\b(?:the (?:night|set|show|episode|hour)|us off|things off|it off)\b",
    # "resurfaces later", "turns up again", "appears twice"
    r"\b(?:resurfaces|reappears|returns|turns up|appears|shows up|crops up)\b"
    r"[^.;]{0,24}\b(?:later|again|below|twice|three times|here too|further down)\b",
    r"\bcall-?back\b",
    # "tonight" in a beat is always about the running order -- the record predates it.
    r"\btonight\b",
))


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
Today's date is {today}. Everything dated before it has already happened.
{show_context}{user_brief_block}
Here is the full tracklist for this episode:
{tracklist}
{track_facts_block}

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
{story_instruction}"""

# Catalogue truth, rendered into CURATION_PROMPT when MusicBrainz had confident matches.
# The "do not credit anyone who isn't listed" line is the whole point: unaided, the model
# will invent plausible feature credits, and a wrong credit on screen is the one error a
# listener who knows the record will spot instantly.
TRACK_FACTS_BLOCK = """
Verified catalogue data (MusicBrainz) for tracks it had a confident match on:
{facts}

Treat the CREDITS as authoritative -- where they disagree with your own recollection, they
win. Do not credit a featured artist, vocalist or collaborator who is not listed here; if
you believe someone else is involved and they are absent, leave the claim out. Tracks
missing from this list simply weren't matched; say nothing specific about their credits
unless you are genuinely sure.

Handle the DATES far more carefully. Each is the earliest entry the catalogue happens to
hold, which for a recent record is often a reissue, a remix or a single edit rather than
the original release -- and for anything very new the catalogue may be wrong or thin.

- Decide released-or-not by comparing the date to TODAY, never by whether you recognise
  the record. Your sense of "recent" is anchored to your training data, not to today, so
  an unfamiliar track is not evidence that it is unreleased. Any date at or before today
  has already come out, however new it looks to you.
- A date genuinely after today IS worth saying -- DJs do play advance promos, and "out
  next month" is a real hook. Just make sure the date really is in the future.
- A cluster of recent dates means the show plays new music. That is what the show IS, not
  a discovery about it -- most shows play new music, so "these are all new" is never a
  story. Look for what connects them BESIDES being new: a scene, a city, a label, a
  producer, a shared collaborator, a sound.
- Only build on a date when it carries real weight -- a decades-old record, a gap between
  albums, a reunion, an anniversary -- and never when it just says "this came out lately".
"""

# Rendered into CURATION_PROMPT only when the operator typed something. Framed as a
# third party speaking so the model treats it as a steer to weigh, not as ground truth
# to reconcile the tracklist against -- see the "does not license invention" clause.
USER_BRIEF_BLOCK = """
The person making this promo added their own brief for this episode:
"{brief}"

Take it seriously -- it may name the storyline they already have in mind, a constraint on
what to pick, or a steer on tone. It does not license invention: if what they describe
isn't actually true of this tracklist, say so plainly and give the closest reading that
IS true rather than bending the facts to fit the brief.
"""

# Appended to CURATION_PROMPT for the story path. The picks half of the prompt is
# unchanged above; this only adds the narrative layer on top of it.
STORY_INSTRUCTION = """
Beyond the ranking, find the STORY in this episode -- the one through-line that makes
these tracks belong together, and sequence them to tell it.

"These are all great electronic tracks" is not a story. A real one is specific: a period,
a city or a scene, a label or a producer, a sound that mutates across the set, an
emotional arc that runs first to last. Look for the connection someone wouldn't spot from
the tracklist alone.

JUDGE A FRAMING BY HOW MUCH OF THE EPISODE IT EXPLAINS. Three tracks out of twenty is a
coincidence, not a through-line -- however tidy it looks written down. "One producer
appears twice" is a fact about two tracks; it says nothing about the other eighteen, and
a promo built on it is a promo about a footnote.

COUNT IT. This tracklist has {total} tracks. Every framing you return -- the primary and
every alternate -- carries a "covers" number: how many of those {total} records the
framing genuinely accounts for. Not how many you feature in the video; how many the claim
is actually true of. Count before you write, not after.

Be strict about what counts. A record counts if the framing says something real about it.
It does not count because it is electronic, or from roughly the right decade, or could be
argued into the theme with a sentence. If you find yourself reasoning towards a higher
number, the number is lower than you think.

THE NUMBER IS A GATE, NOT A GRADE. A framing that covers fewer than {min_covers} of the
{total} is discarded before anyone sees it, whatever confidence you attach. It is not a
weak story, it is NOT A STORY. Do not spend output working one up -- return fewer
alternates instead, or none.

The surest sign you have one of these is that you find yourself writing the excuse into
the pitch: "...even if the rest of the set roams further", "...though the other tracks
vary", "only four of the twelve are actually...". The moment a framing needs a clause
conceding how little it covers, it has failed this gate. Delete it rather than qualify
it -- an honest disclaimer does not rescue a story, it just tells the reader you knew.

Two ways to earn a real framing:

1. GENERALISE THE COINCIDENCE. If the same SHAPE turns up several times over -- one
   artist appearing twice here, a different artist twice there, a third remixing someone
   else further down -- then the RECURRENCE is the story, not any single instance.
   "Three different artists each turn up twice, and one of them remixes a fourth" is
   about the whole episode; "Bufiman appears twice" is about two tracks. Before settling
   on your best coincidence, always ask what pattern it is an instance of.

2. FIND THE ARC. A movement running from one end of the set to the other -- austere
   techno loosening into disco edits, cold to warm, underground to pop, night to morning
   -- covers everything by construction and tells a listener what the hour will feel
   like. An arc across the whole set almost always beats a cluster inside it.

The story is about the EPISODE; the tracks you sequence are simply the ones that show it
best. So a framing must hold for the tracklist as a whole even though only a handful of
records make the video.

Then apply the hardest test: COULD THIS FRAMING PRODUCE GOOD ON-SCREEN LINES? Each
featured track gets one line under its title, and that line has to say something about
the music or the artist. If the framing only lets you write lines ABOUT THE TRACKLIST
ITSELF -- "first of two credits here", "the third appearance tonight", "out next month"
-- then it is a fact about the running order, not about the records, and the viewer
learns nothing. Discard it and use one that lets every line be about the music.

That test usually settles it: an arc across the set can describe each record on its own
terms, while a framing built on recurrence or scheduling can only point at the list.
Prefer the arc even when the coincidence is more surprising.

Rate each framing you offer. Coverage sets the ceiling and it is applied arithmetically
against your own "covers" number, so a grade you cannot support will simply be lowered:

- "strong" -- covers at least {strong_covers} of the {total}, and the connection is
  undeniable and specific.
- "good" -- covers at least {good_covers}. Real and worth telling, one or two loose ends.
- "solid" -- covers at least {min_covers}. True, but more of an observation than a
  revelation.
- below {min_covers} -- not a grade. Discarded.

Solid is the floor of the scale, not a place to park thin ideas. A thin option that has
to argue against itself is worse than no option, and nobody wants to be sold something
the seller is talking them out of.

This is never a licence to inflate. If a connection isn't real, don't reach for it and
don't dress it up to clear the bar -- find a different, true one. Never invent a fact, a
credit or a date to hold a framing together.

Also propose UP TO {n_alternates} ALTERNATE framings of the same episode, as PITCHES ONLY --
just a headline, a sentence or two of body, a confidence and a "covers" count. No
sequence, no beats, no evidence for these: they exist for the person to choose between,
and only the one they pick gets worked up in full. Make them genuinely different lenses -- a period, a genre, a
scene, a mood, a theme -- not small variations on the primary one.

The primary story must be your STRONGEST framing -- the one you would put on air. If one
of your pitches is better supported or more surprising than the primary, swap them so the
best reading is the one you work up in full. Do not save the good one for the alternates.

The primary story is shaped like this:
- "headline": the connection stated flat, eight words or fewer.
- "body": 1-3 sentences, in {language}, addressed to the person making this promo. Tell
  them what their episode turns out to be about and why that makes a promo worth watching
  -- "your set traces one city's move from guitars to machines, and the records line up in
  order" -- not an analysis of your own reasoning. Never describe what you did or how well
  it worked; they can see the result. Weave in the concrete facts that make it true
  (a date, a label, a place) rather than listing them separately.
- "evidence": 1-3 concrete, checkable facts holding it up. Not shown to anyone; it exists
  so the claim can be checked, so make each one specific and verifiable.
- "covers": the count described above -- how many of the {total} records this framing is
  actually true of. An integer, counted honestly, never rounded up to clear a bar.
- "sequence": between {min_featured} and {max_featured} tracks, in the order they should
  play to tell this framing. Use the exact artist/title of entries from "picks", which is
  a ranked pool and deliberately longer than you need -- take the ones that serve this
  framing, which need not be the top-ranked ones.
  HOW MANY is your decision and part of the framing: use the number the story actually
  needs. A tight three-record arc beats six records padded out to fill time, and a story
  that genuinely runs six beats should not be cut to four. Each track is roughly eight
  seconds on screen.
  Order for the narrative, not by quality -- the strongest track does not have to open.
- "order_note": one line on why that order.
- Each sequence entry's "beat" is the line shown on screen under that track, written in
  {language} as a MOMENT IN THE STORY rather than a standalone fact. Not "charted at #3
  in 1979" but "the one that starts it -- recorded the week he left Sheffield". Same rules
  as "reason" otherwise: short, concrete, one clause, true, and never opening with a
  generic referent like "the track"/"the song"/"השיר".
  Keep it under 90 characters -- it is one line under the track title, and a longer
  line gets cut off on screen. Aim for 60-80.
  Every beat must contain something CHECKABLE -- a date, a place, a label, a name, a
  release, an event. Narrative framing belongs on top of a fact, never instead of one.
  "the single that got the brothers signed to XL" is a beat; "Montreal's low-end swagger
  closes the global lap" is not -- that is atmosphere with nothing in it, and it puts an
  opinion on screen in the host's voice.
  A beat must say something about THE MUSIC OR THE ARTIST. A release date on its own is
  not a beat -- "out Aug 14, three days after this airs" and "not due until Oct 9" tell a
  viewer nothing about the record or who made it. Dates belong inside a beat, never as
  the whole of one.
  A beat must NEVER be about the tracklist. The viewer sees one track at a time and does
  not know what came before or after it, so "first Bufiman cameo", "the first solo turn of
  the night", "before the callback lands" and "opens the set" say nothing to them. Lines
  like these are stripped automatically before the video is built and the track ships
  with no line at all, so a framing that can only produce them is a framing that produces
  a silent promo. Write about the record, or say nothing.
  If the track is new enough that you don't know it, say something true about the ARTIST
  instead: what they are known for, their last notable record, the scene or label they
  come from, who they usually work with. "the Compton producer behind Black Moses" beats
  any amount of schedule talk. Only if you know nothing about the artist either should
  you leave the beat empty.
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
    # Bulletin (swiss style / press layout) was cut, and it is worth naming the
    # pattern it came out of. Four themes sat in a 2x2 -- {plate, swiss} styles
    # crossed with {gallery, press} layouts -- so Gallery/Swiss and
    # Plate/Bulletin were each a pair identical in crop and composition,
    # differing only serif-vs-sans, which is not a choice anyone can see at
    # tile size. Bulletin was the corner that went: the sans voice survives in
    # Swiss, the press geometry in Plate and Slab. Gallery and Swiss remain the
    # other such pair, so PRESET_ORDER keeps them apart rather than inviting
    # the comparison.
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
    # Catalogue (index/gallery) and Pop (poppy/canvas) were cut from the picker:
    # 23 tiles was more choice than the decision deserves, and these two were
    # the ones a user was least likely to miss -- Catalogue reads as a quieter
    # Gallery, and Pop's bright card is the furthest thing here from a radio
    # promo. Their STYLES entries stay in the vocabulary: nothing else costs
    # them, and STYLE_MENU still offers both to the custom-theme prompt, so the
    # looks remain reachable without occupying a tile.
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

    # --- the contemporary five ---------------------------------------------
    "chrome": {
        "label": "Chrome", "style": "chrome", "layout": "bleed",
        "motion": "energetic", "frame": "clean",
        "palettes": [
            {"bg1": "#16062e", "bg2": "#040108", "accent": "#b18cff", "accent2": "#59f3ff", "orb1": "#5a2ea6", "orb2": "#0e8fa8"},
            {"bg1": "#2e0620", "bg2": "#080105", "accent": "#ff5ea8", "accent2": "#ffd166", "orb1": "#a62e6b", "orb2": "#b8860b"},
        ],
    },
    "ink": {
        "label": "Ink", "style": "flux", "layout": "strip", "transition": "dissolve",
        "motion": "calm", "frame": "film-grain",
        "palettes": [
            {"bg1": "#1c1a17", "bg2": "#050404", "accent": "#b5a48c", "accent2": "#e6dccb", "orb1": "#4a4234", "orb2": "#7d7159"},
            {"bg1": "#171a1c", "bg2": "#040505", "accent": "#8ca6b5", "accent2": "#cbdce6", "orb1": "#34424a", "orb2": "#59707d"},
        ],
    },
    "cutout": {
        "label": "Cutout", "style": "cutout", "layout": "gallery", "transition": "swap",
        "motion": "normal", "frame": "clean",
        "palettes": [
            {"bg1": "#18181a", "bg2": "#030303", "accent": "#e8e8e4", "accent2": "#9a9aa0", "orb1": "#303034", "orb2": "#5a5a60"},
            {"bg1": "#1a1614", "bg2": "#040302", "accent": "#e0b894", "accent2": "#f2e4d4", "orb1": "#5c4430", "orb2": "#8f6f52"},
        ],
    },
    "reel": {
        "label": "Reel", "style": "reel", "layout": "bleed", "transition": "slide",
        "motion": "energetic", "frame": "clean",
        "palettes": [
            {"bg1": "#200808", "bg2": "#060101", "accent": "#ff3b30", "accent2": "#ffe8e6", "orb1": "#8f1c14", "orb2": "#c9564d"},
            {"bg1": "#081420", "bg2": "#010306", "accent": "#37b6ff", "accent2": "#e2f4ff", "orb1": "#14508f", "orb2": "#4d86c9"},
        ],
    },
    "coverstar": {
        "label": "Cover Star", "style": "coverpiece", "layout": "cover", "transition": "dissolve",
        "motion": "calm", "frame": "clean",
        "palettes": [
            {"bg1": "#1a1a1a", "bg2": "#000000", "accent": "#111214", "accent2": "#8a8a8a", "orb1": "#3a3a3a", "orb2": "#6a6a6a"},
            {"bg1": "#1a1214", "bg2": "#040202", "accent": "#c2453a", "accent2": "#2b2b2b", "orb1": "#7a2a22", "orb2": "#4a4a4a"},
        ],
    },
}

# The order the picker shows them in, which is a different decision from the
# order they are authored in above -- and one worth making explicitly, because
# the authored order is chronological. Themes were added in waves, so the loud
# ones (the display faces, the masked and cut-out headlines, the colour font)
# all arrived last and all sat together at the bottom of the grid. Anyone
# scanning the first row saw four quiet, largely interchangeable tiles and had
# no reason to believe the tool did anything else.
#
# So: alternate. Every row of four in the picker carries at least one quiet
# theme and at least one loud one, and the range is visible before any
# scrolling happens. Classic stays first because it is DEFAULT_PRESET and the
# safe pick belongs where the eye lands first.
#
# Adding a theme above without naming it here is a mistake, not a default --
# the assertion below is what turns it into a startup failure instead of a
# theme that silently never appears in the picker.
#
# The grid is four wide, so "next to" means horizontally AND vertically: a
# theme four places later sits directly underneath. Gallery and Swiss are the
# remaining pair that share a layout and differ only by typeface, so they are
# placed to share neither a row nor a column position -- an earlier
# arrangement stacked the pale themes in column 3 and the picker grew a stripe
# of near-identical tiles down its middle.
PRESET_ORDER = (
    # quiet         loud            quiet           loud
    "classic",      "xl",           "gallery",      "chrome",
    "halo",         "cutout",       "marquee",      "kinetic",
    "swiss",        "split",        "ink",          "coverstar",
    "tide",         "slab",         "nightshift",   "reel",
    "plate",        "offcut",       "terminal",     "coverline",
)

assert set(PRESET_ORDER) == set(PRESET_THEMES), (
    "PRESET_ORDER and PRESET_THEMES disagree: "
    f"unplaced={sorted(set(PRESET_THEMES) - set(PRESET_ORDER))} "
    f"unknown={sorted(set(PRESET_ORDER) - set(PRESET_THEMES))}"
)
PRESET_THEMES = {key: PRESET_THEMES[key] for key in PRESET_ORDER}


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

# One framing of an episode. The primary story and every alternate share this shape, so
# swapping an alternate in is a straight substitution with nothing to re-derive.
#
# "sequence" carries artist/title rather than indices into "picks" on purpose: media
# resolution can drop a track that won't resolve and pull in a backup, so any positional
# reference would go stale between here and render. Matching on artist/title degrades
# cleanly -- a dropped track just falls out of the order.
STORY_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "the connection stated flat, <= 8 words"},
        "body": {"type": "string", "description": "1-3 sentences in the target language"},
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "1-3 concrete checkable facts supporting the connection",
        },
        "confidence": {"type": "string", "enum": ["strong", "good", "solid"]},
        # Required, and required for a reason: asking for the number forces the model to
        # do the counting it was skipping when the same instruction was prose. The value
        # is then graded in code (see COVERAGE_CEILINGS) rather than trusted -- a framing
        # can claim "strong" and still be demoted or dropped on the strength of its own
        # stated arithmetic.
        "covers": {
            "type": "integer",
            "description": "how many records of the FULL tracklist this framing genuinely "
                           "accounts for -- not how many are featured in the video",
        },
        "order_note": {"type": "string", "description": "one line on why this order"},
        "sequence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                    "beat": {"type": "string", "description": "on-screen line, a moment in the story"},
                },
                "required": ["artist", "title", "beat"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "body", "evidence", "confidence", "covers", "order_note", "sequence"],
    "additionalProperties": False,
}

# An alternate framing as an unexpanded offer. Screen 1 only ever shows a headline and a
# line of body for these, and working all of them up in full is most of the call's output
# budget -- which is ~93% of its cost. So they come back as pitches and the one the user
# picks is expanded on demand by expand_story().
STORY_PITCH_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "the connection stated flat, <= 8 words"},
        "body": {"type": "string", "description": "1-2 sentences in the target language"},
        "confidence": {"type": "string", "enum": ["strong", "good", "solid"]},
        # A pitch is graded on the same ladder as the primary, so it carries the same
        # number. Cheap -- one integer -- and it is what lets a thin alternate be dropped
        # before anyone is offered it.
        "covers": {
            "type": "integer",
            "description": "how many records of the FULL tracklist this framing accounts for",
        },
    },
    "required": ["headline", "body", "confidence", "covers"],
    "additionalProperties": False,
}

RANKED_WITH_STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": RANKED_SCHEMA["properties"]["picks"],
        "story": STORY_OBJECT_SCHEMA,
        "alternates": {"type": "array", "items": STORY_PITCH_SCHEMA},
    },
    "required": ["picks", "story", "alternates"],
    "additionalProperties": False,
}


def graded_confidence(framing: dict, total_tracks: int) -> str | None:
    """What confidence this framing's own coverage number entitles it to.

    Returns None when it falls under STORY_MIN_COVERAGE -- meaning the framing should not
    be offered at all, at any grade. The model's own "confidence" is only ever a ceiling
    from here: a framing that claims "strong" on a third of the tracklist gets whatever
    the arithmetic allows, and a framing that modestly claims "solid" on nine tenths of it
    is not promoted (self-deprecation is not the failure mode we have).
    """
    if total_tracks <= 0:
        return framing.get("confidence") or "solid"
    covers = framing.get("covers")
    if not isinstance(covers, int):
        # Schema says required, so this only fires on a hand-built or legacy framing.
        # Treat a missing count as unproven rather than disqualifying -- dropping a story
        # over a missing field would fail jobs for a reason the operator can't see.
        return framing.get("confidence") or "solid"
    ratio = max(0, min(covers, total_tracks)) / total_tracks
    claimed = framing.get("confidence") or "solid"
    for threshold, grade in COVERAGE_CEILINGS:
        if ratio >= threshold:
            order = [g for _, g in COVERAGE_CEILINGS]
            return claimed if order.index(claimed) >= order.index(grade) else grade
    return None


def is_meta_beat(beat: str) -> bool:
    """True when a beat is about the running order rather than the record."""
    text = (beat or "").strip()
    return bool(text) and any(p.search(text) for p in META_BEAT_PATTERNS)


def scrub_beats(story: dict) -> list[str]:
    """Blank every beat that talks about the tracklist instead of the music, in place.

    Returns the beats it removed, so the caller can log what happened -- silently
    emptying a line the operator was about to review would be worse than the line."""
    removed = []
    for step in story.get("sequence") or []:
        beat = (step.get("beat") or "").strip()
        if is_meta_beat(beat):
            removed.append(beat)
            step["beat"] = ""
    return removed


def filter_pitches(pitches: list[dict], total_tracks: int) -> tuple[list[dict], list[dict]]:
    """Split alternate pitches into (kept, dropped), clamping the confidence of the kept.

    Nobody should be offered a framing we would reject if it were the primary, so the
    floor applies here too -- and an alternate is cheaper to drop than the primary,
    because there is nothing to replace it with."""
    kept, dropped = [], []
    for pitch in pitches or []:
        grade = graded_confidence(pitch, total_tracks)
        if grade is None:
            dropped.append(pitch)
        else:
            pitch["confidence"] = grade
            kept.append(pitch)
    return kept, dropped


def enforce_story_quality(bundle: dict, total_tracks: int) -> dict:
    """Apply the coverage gate and the beat filter to a fresh curation bundle.

    Alternates under the floor are dropped -- nobody should be offered a framing we would
    reject. The primary is graded but never dropped here: the caller decides what to do
    when it fails, because the only honest replacement is one of the surviving alternates
    and that costs an API call the caller may not want to spend.

    Mutates and returns the bundle, plus a "quality" report for logging.
    """
    story = bundle["story"]
    primary_grade = graded_confidence(story, total_tracks)
    demoted = primary_grade is not None and primary_grade != story.get("confidence")
    if primary_grade:
        story["confidence"] = primary_grade

    kept, dropped = filter_pitches(bundle.get("alternates"), total_tracks)
    bundle["alternates"] = kept

    bundle["quality"] = {
        "primary_failed": primary_grade is None,
        "primary_demoted": demoted,
        "dropped": [p.get("headline", "") for p in dropped],
        "blanked": scrub_beats(story),
    }
    return bundle


def regrade_story(story: dict, total_tracks: int) -> list[str]:
    """Grade and scrub a framing the operator asked for by name. Returns blanked beats.

    Used for restory and expand_story. Deliberately does NOT drop: they picked this
    telling, and refusing to produce what was asked for is not a quality control, it is a
    broken button. A thin framing gets an honest confidence chip instead, and the beats
    are still scrubbed because a bad line renders either way.
    """
    # Below the floor there is no grade to award, but the framing still ships -- "solid"
    # is the floor of the scale shown to the operator, so that is what a sub-floor
    # requested framing is labelled.
    story["confidence"] = graded_confidence(story, total_tracks) or "solid"
    return scrub_beats(story)


def _format_track_facts(facts: dict | None) -> str:
    """One line per matched recording -- a few hundred tokens for the whole tracklist,
    against ~$0.24 of input tokens for the web search that would establish the same
    things less reliably."""
    if not facts:
        return ""
    lines = []
    for f in facts.values():
        bits = [f["title"]]
        if f.get("credited"):
            bits.append("credited to " + " & ".join(f["credited"]))
        if f.get("first_release"):
            # "earliest known" rather than "released": it's the earliest date across the
            # pressings MusicBrainz has, which can lag the true original when the catalogue
            # is thin on a small-label first run.
            bits.append(f"earliest known release {f['first_release']}")
        lines.append("- " + " | ".join(bits))
    return TRACK_FACTS_BLOCK.format(facts="\n".join(lines))


def _curate_call(tracks: list[Track], *, n: int, num_featured: int, language: str,
                 job_id: str | None, personal: bool, use_search: bool, model: str,
                 want_story: bool, n_alternates: int, user_brief: str,
                 max_searches: int | None = None, track_facts: dict | None = None) -> dict:
    """The single Anthropic call behind both curate_ranked and curate_with_story.

    The story is folded into the curation call rather than run separately because it is a
    property of the same reasoning that picks the tracks -- a second call would re-send
    the tracklist and re-run the same searches to reach the same conclusions."""
    client = anthropic.Anthropic()
    tracklist_text = "\n".join(t.label() for t in tracks)
    language_name = languages.english_name(language)
    show_context = POPLOCK_SHOW_CONTEXT if personal else ""
    # Keyed off the budget, not use_search: a zero budget means no tool is attached, and
    # telling the model to search when it has nothing to search with invites it to
    # apologise for tool failures instead of answering from what it knows.
    budget = max_searches if max_searches is not None else (STORY_MAX_SEARCHES if want_story else 8)
    searching = use_search and budget > 0
    research_instruction = RESEARCH_INSTRUCTION_WITH_SEARCH if searching else RESEARCH_INSTRUCTION_NO_SEARCH
    brief = (user_brief or "").strip()
    user_brief_block = USER_BRIEF_BLOCK.format(brief=brief) if brief else ""
    track_facts_block = _format_track_facts(track_facts)
    today = datetime.date.today().isoformat()
    # The coverage thresholds go into the prompt as track counts rather than fractions:
    # the model is being asked to compare its own integer against them, and "at least 11
    # of 21" is a comparison it can make reliably where "at least 50%" invites arithmetic
    # it will skip. The same fractions are re-applied in code afterwards, so the prompt is
    # telling it the truth about how it will be judged.
    total = len(tracks)
    story_instruction = STORY_INSTRUCTION.format(
        n_alternates=n_alternates, language=language_name,
        min_featured=MIN_FEATURED,
        max_featured=min(MAX_FEATURED, num_featured),
        total=total,
        min_covers=math.ceil(STORY_MIN_COVERAGE * total),
        good_covers=math.ceil(COVERAGE_CEILINGS[1][0] * total),
        strong_covers=math.ceil(COVERAGE_CEILINGS[0][0] * total),
    ) if want_story else ""

    # output_config forces a valid JSON response no matter what the web-search tool
    # does mid-run (hits its usage cap, comes back empty, etc.) — without this the
    # model sometimes narrates around a tool hiccup instead of emitting pure JSON.
    kwargs = dict(
        model=model,
        max_tokens=8192 if not want_story else 16384,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema",
                                  "schema": RANKED_WITH_STORY_SCHEMA if want_story else RANKED_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": CURATION_PROMPT.format(
                    tracklist=tracklist_text, n=n, language=language_name,
                    show_context=show_context, research_instruction=research_instruction,
                    user_brief_block=user_brief_block, story_instruction=story_instruction,
                    track_facts_block=track_facts_block, today=today,
                ),
            }
        ],
    )
    if searching:
        # web_fetch is declared alongside web_search to switch on dynamic filtering:
        # the model writes code to sift results before they reach the context window,
        # instead of every result page landing in context whole. Search results are the
        # single biggest cost in this call, so the filtering is the point -- fetching is
        # just what it does once it has decided which result is worth reading.
        kwargs["tools"] = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": budget},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": budget},
        ]
    # Streamed rather than a plain create(): with adaptive thinking and up to ten web
    # searches this call regularly runs for minutes, which is long enough to hit the
    # SDK's request timeout -- and a timeout is retried twice, so the failure mode is a
    # half-hour hang rather than an error. Streaming keeps the connection alive; the
    # final message is identical either way.
    with client.messages.stream(**kwargs) as stream:
        response = stream.get_final_message()
    analytics.record_api_call(
        job_id, "curate_ranked_story" if want_story else "curate_ranked",
        response.usage, model=model)

    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise ValueError(f"Curator returned no output (stop_reason={response.stop_reason})")
    return json.loads(text)


def _rank_picks(picks: list[dict], tracks: list[Track]) -> list[dict]:
    """Attach each pick back to its tracklist entry, for the album field."""
    by_key = {(t.artist.lower(), t.title.lower()): t for t in tracks}
    ranked = []
    for p in picks:
        key = (p.get("artist", "").lower(), p.get("title", "").lower())
        t = by_key.get(key)
        album = t.album if t else None
        ranked.append({"artist": p["artist"], "title": p["title"], "album": album, "reason": p.get("reason", "")})
    return ranked


def curate_ranked(tracks: list[Track], n: int = 5, previous_shows: list | None = None, language: str = "en", job_id: str | None = None, personal: bool = False, use_search: bool = True, model: str = MODEL_SIMPLE, user_brief: str = "") -> list[dict]:
    """Returns a ranked list of candidate tracks (best first), longer than n when the
    tracklist allows it, so the caller can pull backups to satisfy constraints (e.g.
    ensuring enough picks have YouTube video) without a second API call. use_search
    controls whether the (billed) web_search tool is available -- callers restrict
    this to admin to keep the cost of colleague-triggered runs low."""
    if len(tracks) <= n:
        return [{"artist": t.artist, "title": t.title, "album": t.album, "reason": ""} for t in tracks]

    data = _curate_call(
        tracks, n=min(len(tracks), n + 6), num_featured=n, language=language,
        job_id=job_id, personal=personal, use_search=use_search, model=model,
        want_story=False, n_alternates=0, user_brief=user_brief,
    )
    return _rank_picks(data["picks"], tracks)


def curate_with_story(tracks: list[Track], n: int = 5, language: str = "en",
                      job_id: str | None = None, personal: bool = False,
                      use_search: bool = True, model: str = MODEL_SIMPLE,
                      n_alternates: int = 3, user_brief: str = "",
                      max_searches: int | None = None,
                      track_facts: dict | None = None) -> tuple[list[dict], dict]:
    """Curate and find the through-line in one call.

    Returns (ranked, bundle) where ranked matches curate_ranked's contract (best first,
    longer than n so the caller can pull backups) and bundle is
    {"story": {...}, "alternates": [{...}]} -- each framing shaped like STORY_OBJECT_SCHEMA.

    Unlike curate_ranked there is no short-tracklist shortcut: even when every track will
    be featured there is still an order to choose and a story to tell."""
    data = _curate_call(
        tracks, n=min(len(tracks), n + 6), num_featured=min(len(tracks), n),
        language=language, job_id=job_id, personal=personal, use_search=use_search,
        model=model, want_story=True, n_alternates=n_alternates, user_brief=user_brief,
        max_searches=max_searches,
        track_facts=track_facts if track_facts is not None else providers.facts_for_tracks(tracks),
    )
    bundle = {"story": data["story"], "alternates": data.get("alternates", [])}
    return _rank_picks(data["picks"], tracks), bundle


RESTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "story": STORY_OBJECT_SCHEMA,
        "alternates": {"type": "array", "items": STORY_PITCH_SCHEMA},
    },
    "required": ["story", "alternates"],
    "additionalProperties": False,
}

EXPAND_PROMPT = """Today's date is {today}. Everything dated before it has already happened.

These tracks are candidates for a radio show promo, best first:
{pool}
{track_facts_block}

Someone has chosen this framing for the episode:
Headline: {headline}
{body}

Work it up in full: pick the {num_featured} tracks from the list above that serve this
framing best, put them in the order that tells it, and write the on-screen line for each.

Stay honest. If, working it through, the framing turns out to be thinner than it looked,
say so in "confidence" and in "body" rather than forcing it -- the person can still pick a
different one. Do not invent facts to hold it up.

Respond with: "headline" (you may sharpen the wording, but keep the same idea), "body"
(1-3 sentences in {language}), "evidence" (1-3 concrete checkable facts), "confidence",
"covers", "order_note" (one line on why this order), and "sequence" -- exactly
{num_featured} entries with the exact artist/title from the list above, each with a
"beat".

"covers" is how many records of the full {total} in the episode this framing is genuinely
true of -- not how many you feature. Count honestly; the grade is checked against it, so
an inflated number only produces a confidence you cannot support.

Each "beat" is the line shown on screen under that track, in {language}, written as a
moment in the story rather than a standalone fact -- short, concrete, one clause, true,
and never opening with a generic referent like "the track"/"the song"/"השיר".

A beat must never be about the tracklist. The viewer sees one track at a time and has no
idea what came before it, so "first cameo of the night", "the second appearance" and
"before the callback lands" tell them nothing. Lines like these are stripped
automatically and the track ships with no line at all. Write about the record.

Keep every beat under 90 characters -- it is one line under the track title, and a
longer line is cut off on screen. Aim for 60-80.
Every beat must contain something CHECKABLE -- a date, a place, a label, a name, a
release, an event -- AND must say something about the music or the artist. A release
date on its own is not a beat: "not due until Oct 9" tells a viewer nothing about the
record or who made it. If the track is too new for you to know it, say something true
about the artist instead -- what they are known for, their last notable record, the
scene or label they come from. Narrative framing is welcome on top of that fact, but not instead of
it. "the single that got the brothers signed to XL" is a beat. "Montreal's low-end
swagger closes the global lap" is not: it is atmosphere with nothing in it, and it puts
an opinion on screen in the host's voice. If you have no fact for a track, say something
plainer rather than reaching for mood.
"""


def expand_story(picks: list[dict], pitch: dict, num_featured: int = 4,
                 language: str = "en", job_id: str | None = None,
                 model: str = MODEL_SIMPLE, track_facts: dict | None = None,
                 total_tracks: int = 0) -> dict:
    """Turn an alternate's pitch into a full framing, with sequence and beats.

    Called only when the operator actually switches to an alternate, which is what makes
    it worth shipping pitches instead of four fully-worked framings on every job.
    Gets the whole ranked pool, not the primary's selection, so a different framing can
    feature different tracks."""
    client = anthropic.Anthropic()
    language_name = languages.english_name(language)
    pool_text = "\n".join(f"- {p.get('artist', '')} - {p.get('title', '')}" for p in picks)
    with client.messages.stream(
        model=model,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": STORY_OBJECT_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": EXPAND_PROMPT.format(
                    today=datetime.date.today().isoformat(),
                    pool=pool_text, headline=pitch.get("headline", ""),
                    body=pitch.get("body", ""), num_featured=num_featured,
                    language=language_name,
                    # The pool is shorter than the episode, so the model can't count the
                    # tracklist itself here -- it has to be told how long it was.
                    total=total_tracks or len(picks),
                    track_facts_block=_format_track_facts(track_facts),
                ),
            }
        ],
    ) as stream:
        response = stream.get_final_message()
    analytics.record_api_call(job_id, "expand_story", response.usage, model=model)
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise ValueError(f"Expand returned no output (stop_reason={response.stop_reason})")
    return json.loads(text)

RESTORY_PROMPT = """Today's date is {today}. Everything dated before it has already happened.

A promo video is being made from these tracks, in this order:
{sequence}

The story currently being told about them:
Headline: {headline}
Body: {body}
Why: {evidence}

The person making the promo wants it told differently:
"{instruction}"

Re-frame the story to match. Keep the same tracks -- you are changing the telling, not the
selection. You may re-order them if the new framing calls for a different sequence.

You may re-frame but you may not invent. Every claim still has to be true of these tracks
and supported by the evidence already established or by something you're confident about.
If what they've asked for simply isn't true of this tracklist -- a place these records
aren't from, a period they don't belong to -- say so plainly in "body" and give the
closest framing that IS true. Do not bend the facts to fit the request.

Also propose {n_alternates} alternate framings as PITCHES ONLY -- headline, a sentence or
two of body, and a confidence. No sequence or beats for those; only the framing being
asked for gets worked up in full.

The full framing is shaped like the current one: "headline" (<= 8 words), "body" (1-3
sentences in {language}), "evidence" (1-3 concrete checkable facts), "confidence"
("strong"/"good"/"solid" -- be honest, a re-framing can be weaker than what it replaces),
"covers", "order_note", and "sequence" with a "beat" per track.

"covers" is how many records of the full {total} in the episode this framing is genuinely
true of -- not how many are featured. Count honestly. It sets the confidence you are
allowed to claim, so inflating it just produces a grade that gets lowered.

Each "beat" is the on-screen line for that track, in {language}, written as a moment in
the story rather than a standalone fact -- short, concrete, one clause, never opening
with a generic referent like "the track"/"the song"/"השיר".

A beat must never be about the running order. The viewer sees one track at a time and has
no idea what came before it, so "first cameo of the night", "the second appearance" and
"before the callback lands" tell them nothing. Lines like these are stripped
automatically and the track ships with no line at all. Write about the record.
"""


def restory(picks: list[dict], previous_story: dict, instruction: str,
            language: str = "en", job_id: str | None = None,
            model: str = MODEL_SIMPLE, n_alternates: int = 3,
            total_tracks: int = 0) -> dict:
    """Re-frame an existing story from the operator's free-text instruction.

    No web search: the facts were gathered on the curation call, and re-framing is a
    writing task over what's already established. That keeps a regeneration cheap enough
    to do several times while steering."""
    client = anthropic.Anthropic()
    language_name = languages.english_name(language)
    sequence_text = "\n".join(f"{i + 1}. {p.get('artist', '')} - {p.get('title', '')}"
                              for i, p in enumerate(picks))
    with client.messages.stream(
        model=model,
        max_tokens=8192,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": RESTORY_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": RESTORY_PROMPT.format(
                    sequence=sequence_text,
                    headline=previous_story.get("headline", ""),
                    body=previous_story.get("body", ""),
                    evidence="; ".join(previous_story.get("evidence", []) or []),
                    instruction=(instruction or "").strip(),
                    n_alternates=n_alternates,
                    language=language_name,
                    # Restory only sees the featured sequence, not the episode, so the
                    # tracklist length has to come from the caller.
                    total=total_tracks or len(picks),
                ),
            }
        ],
    ) as stream:
        response = stream.get_final_message()
    analytics.record_api_call(job_id, "restory", response.usage, model=model)
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise ValueError(f"Restory returned no output (stop_reason={response.stop_reason})")
    data = json.loads(text)
    return {"story": data["story"], "alternates": data.get("alternates", [])}


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


# Test sets for the __main__ harness below, chosen to probe different failure modes:
# "electronic" has a real but non-obvious through-line to find, "scattered" spans eras and
# genres with no honest connection (the model should say so rather than invent one), and
# "sheffield" exists to be told it's about Detroit -- a brief that is flatly untrue of the
# set, which must be declined rather than confabulated around.
SAMPLE_SETS = {
    "electronic": [
        Track("Four Tet", "Baby"),
        Track("Bicep", "Glue"),
        Track("Overmono", "So U Kno"),
        Track("Fred again..", "Delilah"),
        Track("Jamie xx", "Life"),
        Track("Peggy Gou", "It Makes You Forget"),
        Track("DJ Koze", "Pick Up"),
        Track("Kaytranada", "10%"),
    ],
    "scattered": [
        Track("Dolly Parton", "Jolene"),
        Track("Autechre", "Gantz Graf"),
        Track("Whitney Houston", "I Wanna Dance with Somebody"),
        Track("Slayer", "Raining Blood"),
        Track("Yo-Yo Ma", "Bach Cello Suite No. 1"),
        Track("Aphex Twin", "Windowlicker"),
        Track("Fela Kuti", "Zombie"),
        Track("Taylor Swift", "Anti-Hero"),
    ],
    "sheffield": [
        Track("The Human League", "Being Boiled"),
        Track("Cabaret Voltaire", "Nag Nag Nag"),
        Track("Heaven 17", "(We Don't Need This) Fascist Groove Thang"),
        Track("ABC", "The Look of Love"),
        Track("Warp", "Testone"),
        Track("LFO", "LFO"),
        Track("Autechre", "Basscadet"),
        Track("Forgemasters", "Track with No Name"),
    ],
}


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()  # app.py does this at import; the harness runs without it

    args = sys.argv[1:]

    def _opt(flag: str, default: str = "") -> str:
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default

    which = _opt("--set", "electronic")
    brief = _opt("--brief")
    instruction = _opt("--restory")
    tracks = SAMPLE_SETS[which]

    if "--no-story" in args:
        print(json.dumps(curate_ranked(tracks, n=4, user_brief=brief), indent=2, ensure_ascii=False))
        raise SystemExit

    ranked, bundle = curate_with_story(
        tracks, n=4, personal="--personal" in args, user_brief=brief,
        use_search="--no-search" not in args,
        n_alternates=int(_opt("--alts", "3")),
        max_searches=int(_opt("--searches")) if _opt("--searches") else None,
    )

    total = len(tracks)

    def _cov(f: dict) -> str:
        """Coverage as the gate sees it, so a headless run shows what app.py would do."""
        n = f.get("covers")
        if not isinstance(n, int):
            return "covers ?"
        graded = graded_confidence(f, total)
        verdict = "DROPPED" if graded is None else (
            f"-> {graded}" if graded != f.get("confidence") else "ok")
        return f"covers {n}/{total} {verdict}"

    def show(label: str, story: dict) -> None:
        print(f"\n=== {label}: {story['headline']}  [{story['confidence']}] ({_cov(story)})")
        print(story["body"])
        for e in story.get("evidence", []):
            print(f"  · {e}")
        print(f"  order: {story.get('order_note', '')}")
        for i, s in enumerate(story.get("sequence", []), 1):
            print(f"  {i}. {s['artist']} - {s['title']}\n     {s['beat']}")

    def pitch(label: str, p: dict) -> None:
        print(f"\n--- {label}: {p['headline']}  [{p['confidence']}] ({_cov(p)})\n    {p['body']}")

    print(json.dumps(ranked, indent=2, ensure_ascii=False))
    print("\n########## AS RETURNED ##########")
    show("STORY", bundle["story"])
    for i, alt in enumerate(bundle["alternates"], 1):
        pitch(f"ALT {i}", alt)

    # Then the same bundle after the coverage gate and the beat filter -- this is what
    # the operator would actually be shown.
    import copy
    gated = enforce_story_quality(copy.deepcopy(bundle), total)
    print("\n########## AFTER THE GATE ##########")
    q = gated["quality"]
    if q["primary_failed"]:
        print("!! primary FAILED the coverage floor -- app.py would promote an alternate")
    for h in q["dropped"]:
        print(f"!! dropped alternate: {h}")
    for b in q["blanked"]:
        print(f"!! blanked beat: {b}")
    show("STORY", gated["story"])
    for i, alt in enumerate(gated["alternates"], 1):
        pitch(f"ALT {i}", alt)

    if "--expand" in args and bundle["alternates"]:
        which_alt = int(_opt("--expand", "1")) - 1
        show(f"EXPANDED ALT {which_alt + 1}",
             expand_story(ranked, bundle["alternates"][which_alt], num_featured=4,
                          track_facts=providers.facts_for_tracks(tracks),
                          total_tracks=total))

    if instruction:
        again = restory(bundle["story"]["sequence"], bundle["story"], instruction,
                        total_tracks=total)
        show("RETOLD", again["story"])
        for i, alt in enumerate(again["alternates"], 1):
            pitch(f"RETOLD ALT {i}", alt)
