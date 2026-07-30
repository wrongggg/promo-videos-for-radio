# Preview covers

`cover-a.jpg` and `cover-b.jpg` are committed and are what the theme picker
tiles show. They are already downscaled (320x320, ~47KB the pair) — production
has no other way to get them, so they live in the repo rather than being
gitignored like the rest of this folder.

To change them, replace those two files with square images. Anything at or
under 400KB is served straight through; anything larger is downscaled once via
ffmpeg and cached in `thumbs/` (gitignored).

Two different images, because the hover preview animates a hand-over from one
to the other — the same file twice makes the transition invisible.

They are cropped centre to 9:16 in the tile, exactly as the renderer crops real
album art, so keep anything important away from the left and right edges.

Generation brief lives in `styles.PREVIEW_IMAGE_BRIEF`.
