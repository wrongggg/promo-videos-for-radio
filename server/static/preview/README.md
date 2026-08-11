# Preview covers

`cover-a.jpg` and `cover-b.jpg` are the two album sleeves every theme tile in
the picker shows. They are committed and already downscaled (768x768) —
production has no other way to get them, so they live in the repo rather than
being gitignored like the rest of this folder.

They are **not a set and must not look like one**. They stand in for two
arbitrary tracks by two unrelated artists — which is what a real tracklist is —
so they should read as if they came off different labels in different decades.
Currently: **A is a deadpan object photograph on flat saturated colour, B is a
luminous abstract colour field.** Different medium, different era, different
tradition. If they look good side by side, that is a bad sign.

The one thing they must satisfy jointly is tonal range — **one light, one
dark** — because half the layouts sit the sleeve on a cream page and half bleed
it behind white type, and two dark covers only ever preview one of those.

## Replacing them

1. Generate two square images from `styles.PREVIEW_IMAGE_BRIEF_A` and
   `PREVIEW_IMAGE_BRIEF_B`. That brief is the source of truth and carries the
   reasoning — read it before editing either prompt.
2. Downscale both to **768x768** and save as JPEG:

   ```
   ffmpeg -i in.png -vf scale=768:768 -q:v 4 cover-a.jpg
   ```

   768 is not arbitrary. A full-bleed tile paints the sleeve across the whole
   9:16 frame, so the binding dimension is the tile's *height* (~218 CSS px),
   in device pixels: 437 at DPR 2, 654 at DPR 3. The pair used to ship at
   320x320, which meant a 1.37x upscale on every bleed layout — visibly soft,
   while Gallery and Press stayed sharp because they draw the sleeve small.

3. Drop them in over the existing pair and commit. Nothing else changes —
   `_preview_sources()` prefers these two filenames, and every tile picks them
   up on the next page load.

Anything at or under 400KB is served straight through; anything larger is
downscaled once via ffmpeg and cached in `thumbs/` (gitignored). That fallback
exists for local experiments — **the committed pair must already be small**, or
production ships a multi-megabyte picker.

## What they have to survive

The brief in `styles.py` explains each of these in full; the short version:

- **118px wide is the real constraint.** Big shapes, hard value contrast, one
  idea. Grain and fine texture are invisible at that size.
- **One dominant saturated hue each.** `palette.field` samples a 3x3 downscale
  and falls back to neutral slate below 0.04 colourfulness, so a desaturated
  cover makes Off Cut and Split preview as grey.
- **Centre-safe.** The tile crops square to 9:16, so the left and right edges
  are lost. Cutout and Cover Star fill their letterforms from the centre
  horizontal band, which therefore wants contrast and incident.
- **Calm — not empty — top and bottom.** The header sits over the top ~20% and
  the artist block over the bottom third in bleed layouts, but Gallery, Press
  and Canvas show the same file whole and unobstructed.
- **No lettering, no people.** The tile lays its own type over these, and a
  figure reads as stock photography rather than as a sleeve.
