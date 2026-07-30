import re
from dataclasses import dataclass, field
from typing import Optional


# A slash between artist names is how tracklists write a collaboration --
# "Head High/Cassy" means Head High & Cassy. Catalogs credit these with "&",
# "and", "feat." or sometimes just one of the names, so normalizing to "&"
# searches and displays far better than a raw slash.
#
# The exception is a name that genuinely contains a slash, AC/DC being the
# obvious one. Those have short parts and no spaces, so a slash only counts as
# a separator when at least one side is 4+ characters or contains a space --
# which keeps AC/DC intact while splitting Vakula/LG and Head High/Cassy.
def normalize_artist(artist: str) -> str:
    if "/" not in (artist or ""):
        return artist
    parts = [p.strip() for p in artist.split("/") if p.strip()]
    if len(parts) < 2:
        return artist
    if not any(len(p) >= 4 or " " in p for p in parts):
        return artist
    return " & ".join(parts)


@dataclass
class Track:
    artist: str
    title: str
    album: Optional[str] = None

    @classmethod
    def from_string(cls, track_string: str) -> "Track":
        track_string = track_string.strip().strip("\"'")
        # Accept hyphen, en dash, and em dash as the artist/title separator —
        # copy-paste and autocorrect often turn "-" into "–" or "—". Whitespace
        # is only required on at least one side (not exactly one space each),
        # so "Artist- Title" / "Artist  - Title" / "Artist -Title" all still
        # split correctly -- while a tight, unspaced hyphen inside a name
        # ("T-Pain", "So-Called") is left alone since it has no space on
        # either side.
        sep = re.compile(r"\s+[-–—]\s*|\s*[-–—]\s+")
        if sep.search(track_string):
            parts = sep.split(track_string)
            artist = parts[0].strip()
            remainder = " - ".join(parts[1:])
        else:
            return cls(artist=normalize_artist(track_string), title="")

        album_paren = re.search(r"\((.*?)\)", remainder)
        album_bracket = re.search(r"\[(from|on)?\s*(.*?)\]", remainder)
        album_dash = sep.split(remainder) if sep.search(remainder) else None

        if album_paren:
            title = remainder[: album_paren.start()].strip()
            album = album_paren.group(1).strip()
        elif album_bracket:
            title = remainder[: album_bracket.start()].strip()
            album = album_bracket.group(2).strip()
        elif album_dash and len(album_dash) > 1:
            title = album_dash[0].strip()
            album = album_dash[1].strip()
        else:
            title = remainder.strip()
            album = None

        return cls(artist=normalize_artist(artist), title=title, album=album)

    def label(self) -> str:
        return f"{self.artist} - {self.title}"

    def to_dict(self) -> dict:
        return {"artist": self.artist, "title": self.title, "album": self.album}


def parse_tracklist(tracklist_text: str) -> list[Track]:
    tracks = []
    for line in tracklist_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # tolerate numbered lines like "1. Artist - Title" or "1) Artist - Title"
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        tracks.append(Track.from_string(line))
    return [t for t in tracks if t.artist and t.title]
