# Preview covers

Drop two square (1:1) images here and the theme picker tiles use them instead
of the generated stand-in. No code change needed.

    cover-a.jpg   (or .png / .webp / .jpeg)
    cover-b.jpg

Two different images, because the hover preview animates a hand-over from one
to the other — using the same file twice makes the transition invisible.

They are cropped centre to 9:16 in the tile, exactly as the renderer crops real
album art, so keep anything important away from the left and right edges.

Generation brief lives in `styles.PREVIEW_IMAGE_BRIEF`.
