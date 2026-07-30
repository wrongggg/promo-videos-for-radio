"""On-screen languages for the promo.

Replaces the original English/Hebrew toggle. Each entry carries the two strings
that appear in the rendered video, the language's own name for the picker, the
name to hand the model when asking for trivia, and whether it is right-to-left.

Script coverage was verified by rendering, not assumed. The renderer's
auto-resolved font list only contains two families with broad coverage (Noto
Sans and Noto Sans JP -- every other Noto/IBM Plex/CJK family was rejected by
the linter), but a test render of all ten scripts below came back correct with
no tofu, so the render environment does supply system fonts for the rest. Both
resolvable families are still named explicitly in every stack in styles.py, so
the renderer supplies what it can rather than relying purely on the host.

One honest limitation: the display faces (Bebas Neue, Archivo Black, Playfair
Display) are Latin-only. Non-Latin text falls through to Noto Sans, so the XL
and Poppy themes lose their condensed/heavy character in, say, Japanese or
Arabic, and read closer to Classic. Colour, layout, motion and the synth patch
still differ, so the themes remain distinguishable -- just less so.
"""

RTL_CODES = {"he", "ar"}

# code -> (endonym for the picker, English name for the model, also_featuring, cta)
LANGUAGES = {
    "en": ("English", "English", "Also in this episode", "Listen Now"),
    "es": ("Español", "Spanish", "También en este episodio", "Escuchar ahora"),
    "fr": ("Français", "French", "Aussi dans cet épisode", "Écouter maintenant"),
    "de": ("Deutsch", "German", "Außerdem in dieser Folge", "Jetzt anhören"),
    "pt": ("Português", "Portuguese", "Também neste episódio", "Ouça agora"),
    "it": ("Italiano", "Italian", "Anche in questo episodio", "Ascolta ora"),
    "nl": ("Nederlands", "Dutch", "Ook in deze aflevering", "Nu luisteren"),
    "pl": ("Polski", "Polish", "Także w tym odcinku", "Słuchaj teraz"),
    "sv": ("Svenska", "Swedish", "Också i detta avsnitt", "Lyssna nu"),
    "tr": ("Türkçe", "Turkish", "Bu bölümde ayrıca", "Şimdi dinle"),
    "ru": ("Русский", "Russian", "Также в этом выпуске", "Слушать сейчас"),
    "uk": ("Українська", "Ukrainian", "Також у цьому випуску", "Слухати зараз"),
    "el": ("Ελληνικά", "Greek", "Επίσης σε αυτό το επεισόδιο", "Άκου τώρα"),
    "he": ("עברית", "Hebrew", "עוד בפרק הזה", "האזינו עכשיו"),
    "ar": ("العربية", "Arabic", "أيضًا في هذه الحلقة", "استمع الآن"),
    "ja": ("日本語", "Japanese", "このエピソードの他の曲", "今すぐ聴く"),
    "ko": ("한국어", "Korean", "이번 에피소드에서", "지금 듣기"),
    "zh": ("中文", "Chinese", "本期还有", "立即收听"),
    "hi": ("हिन्दी", "Hindi", "इस एपिसोड में और", "अभी सुनें"),
    "id": ("Bahasa Indonesia", "Indonesian", "Juga di episode ini", "Dengarkan sekarang"),
}

DEFAULT = "en"
CODES = tuple(LANGUAGES.keys())


def is_valid(code: str) -> bool:
    return code in LANGUAGES


def normalize(code: str | None) -> str:
    return code if code in LANGUAGES else DEFAULT


def strings(code: str | None) -> dict:
    """The two strings baked into the rendered video."""
    _endonym, _english, also, cta = LANGUAGES[normalize(code)]
    return {"also_featuring": also, "cta": cta}


def english_name(code: str | None) -> str:
    """What to call the language when asking the model for trivia."""
    return LANGUAGES[normalize(code)][1]


def is_rtl(code: str | None) -> bool:
    return normalize(code) in RTL_CODES


def choices() -> list[dict]:
    """For the picker. Sorted by the language's own name, English pinned first
    so the default is where people expect it."""
    rest = sorted(
        ({"code": c, "label": v[0]} for c, v in LANGUAGES.items() if c != DEFAULT),
        key=lambda x: x["label"],
    )
    return [{"code": DEFAULT, "label": LANGUAGES[DEFAULT][0]}] + rest
