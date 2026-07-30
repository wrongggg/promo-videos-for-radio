"""The generative / audio-reactive backdrop.

Replaces the ripped music-video footage that used to sit behind each scene.
Three layers, back to front:

  1. A Hydra visual-synth canvas -- one WebGL context for the whole video,
     with the patch switching per scene.
  2. The release artwork, drifting (Ken Burns) and breathing on the bass.
  3. The existing scrim / orbs / text, untouched.

Determinism is the whole design constraint. HyperFrames renders by seeking a
paused timeline and screenshotting, so every layer must be a pure function of
timeline time:

  * Audio reactivity reads pre-baked per-frame arrays (see audio_analysis.py),
    never a live AnalyserNode -- which would return silence under a seeking
    renderer anyway.
  * Hydra runs with autoLoop:false and detectAudio:false, and is driven by
    setting `synth.time` absolutely with `synth.speed = 0`. Hydra's own
    `tick(dt)` *accumulates* time (`synth.time += dt * 0.001 * speed`), which
    would drift on a re-rendered or out-of-order frame; pinning speed to zero
    and assigning the time directly makes frame N byte-identical every pass.
    Verified by rendering the same timestamps out of order and comparing pixel
    hashes.

Cost: measured at 0.45-0.97 ms/frame at 1080x1920, i.e. under 1.5 seconds of
extra render time for a 45-second promo. No API calls, no paid assets.
"""
import json
import os
import shutil
from typing import Optional

import styles

HYDRA_VENDOR_REL = "assets/hydra-synth.js"
_HYDRA_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "static", "vendor", "hydra-synth.js")


CSS = """
/* Generative backdrop. Sits behind everything, including .bg-media artwork,
   so a scene always has motion even when no imagery resolved at all. */
#hydra-bg {
  position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
  z-index: 0; opacity: 0; will-change: opacity;
}
/* Artwork rides above the synth. transform is written every frame by the
   driver (Ken Burns + a bass-driven breath), so no CSS transition here --
   a transition would make the result depend on real elapsed time. */
.art-media {
  position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
  object-fit: cover; z-index: 1; opacity: 0;
  transform-origin: center center; will-change: transform, opacity;
}
"""


def hydra_available() -> bool:
    return os.path.exists(_HYDRA_SRC)


def install_vendor(project_dir: str) -> bool:
    """Copy the Hydra bundle into the job directory. Compositions must not
    fetch from the network, so it ships as a local asset."""
    if not hydra_available():
        return False
    dest = os.path.join(project_dir, HYDRA_VENDOR_REL)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(_HYDRA_SRC, dest)
    return True


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = (hex_color or "#8844ff").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.53, 0.27, 1.0)
    # Hydra's .color() multiplies, so pull values up a little or dark accents
    # crush the whole backdrop to black.
    boost = 1.6
    return (round(min(r * boost, 1.6), 3), round(min(g * boost, 1.6), 3), round(min(b * boost, 1.6), 3))


# A track only earns a visible pulse if its low end actually swings. Measured
# as the 10th-to-90th-percentile spread of the smoothed bass envelope, which
# separates a four-to-the-floor cut from a sparse acoustic recording. Tempo
# confidence is NOT used here -- it ranked a fingerpicked guitar above a house
# track, so it is the wrong signal for this decision.
PULSE_THRESHOLD = 0.33


def _is_reactive(analysis: Optional[dict]) -> bool:
    if not analysis:
        return False
    return (analysis.get("pulse_strength") or 0.0) >= PULSE_THRESHOLD


def canvas_html() -> str:
    """The single shared WebGL canvas. One context for the entire video --
    a per-scene canvas would blow past the browser's context limit on a
    15-track tracklist."""
    return '<canvas id="hydra-bg" width="1080" height="1920"></canvas>\n'


def art_html(index: int, start: float, duration: float, src: str) -> str:
    """Deliberately NOT a `class="clip"` timed element.

    The framework shows and hides clips by writing opacity at clip boundaries,
    which would fight the driver writing a level-reactive opacity on the same
    element every frame -- last writer wins, and which one that is varies. The
    artwork is instead a persistent element (like the synth canvas) whose
    visibility the driver owns outright, from the same baked data that drives
    everything else."""
    return f'<img id="art-{index}" class="art-media" src="{src}" />\n'


def runtime_js(scenes: list[dict], total_duration: float, fps: int = 30,
               patch: str = "haze", accent_hex: str = "#8844ff") -> str:
    """`scenes`: [{index, start, duration, analysis, has_art}] where `analysis`
    is the dict from audio_analysis.analyze (or None).

    `patch` names a Hydra patch in styles.PATCHES. It comes from the theme's
    visual style rather than from its motion preset -- what runs behind the
    text is part of the look, not a side effect of how fast the orbs drift."""
    patch_key = patch if patch in styles.PATCHES else "haze"
    r, g, b = _hex_to_rgb01(accent_hex)

    # Only the per-frame series the driver actually reads get serialized --
    # the full analysis carries onsets and a beat grid too, and a 15-track
    # tracklist would otherwise inline a lot of unused JSON.
    payload = {}
    for s in scenes:
        a = s.get("analysis")
        if not a:
            continue
        payload[str(s["index"])] = {
            "start": s["start"],
            "bass_env": a["bass_env"],
            "level_env": a["level_env"],
            "fps": a["fps"],
        }

    scene_meta = [
        {"i": s["index"], "start": round(s["start"], 3),
         "dur": round(s["duration"], 3), "art": bool(s.get("has_art")),
         "reactive": _is_reactive(s.get("analysis"))}
        for s in scenes
    ]

    return f"""
<script src="{HYDRA_VENDOR_REL}"></script>
<script>
(function () {{
  var AUDIO  = {json.dumps(payload, separators=(",", ":"))};
  var SCENES = {json.dumps(scene_meta, separators=(",", ":"))};
  var FPS    = {fps};
  var TOTAL  = {round(total_duration, 3)};
  var ACCENT = {{ r: {r}, g: {g}, b: {b} }};

  var canvas = document.getElementById("hydra-bg");
  var hydra = null, h = null;
  if (canvas && typeof Hydra !== "undefined") {{
    try {{
      // Claim the WebGL context *before* Hydra does, purely to force
      // preserveDrawingBuffer. WebGL clears the drawing buffer once the frame
      // is composited, so anything that reads the canvas outside the task that
      // drew it -- notably the renderer's per-frame screenshot -- captures an
      // empty canvas and the whole synth layer silently renders black.
      // getContext() returns the already-created context on later calls, so
      // Hydra's regl inherits this one along with the flag.
      canvas.getContext("webgl", {{
        preserveDrawingBuffer: true, alpha: true, antialias: false,
      }});

      hydra = new Hydra({{
        canvas: canvas, autoLoop: false, detectAudio: false,
        makeGlobal: false, width: 1080, height: 1920,
      }});
      h = hydra.synth;
      // The two settings that make this seek-safe: never advance time from
      // tick(), and never skip a render inside it.
      h.speed = 0;
      h.fps = 0;
      var A = ACCENT;
      {styles.PATCHES[patch_key]}
    }} catch (e) {{
      hydra = null;  // a WebGL failure must not take the whole render down
    }}
  }}

  // Sample a baked per-frame series at timeline time `t`, linearly
  // interpolated so motion stays smooth if the render fps ever differs from
  // the analysis fps.
  function sample(series, tInScene, seriesFps) {{
    if (!series || !series.length) return 0;
    var x = tInScene * seriesFps;
    var i = Math.floor(x);
    if (i < 0) return series[0];
    if (i >= series.length - 1) return series[series.length - 1];
    var frac = x - i;
    return series[i] * (1 - frac) + series[i + 1] * frac;
  }}

  function activeScene(t) {{
    for (var k = 0; k < SCENES.length; k++) {{
      var s = SCENES[k];
      if (t >= s.start && t < s.start + s.dur) return s;
    }}
    return null;
  }}

  // Camera moves, cycled per scene so consecutive tracks never repeat one.
  // This is the primary motion: a continuous function of scene progress with
  // no audio input at all, which is what keeps the video calm.
  var MOVES = [
    {{ s0: 1.04, s1: 1.17, x0:   0, x1:   0, y0:   0, y1:   0 }},  // slow push in
    {{ s0: 1.18, s1: 1.05, x0:   0, x1:   0, y0:   0, y1:   0 }},  // slow pull back
    {{ s0: 1.08, s1: 1.19, x0: -22, x1:  16, y0:   0, y1:   0 }},  // push, drift right
    {{ s0: 1.17, s1: 1.06, x0:   0, x1:   0, y0:  16, y1: -14 }},  // pull, drift up
  ];

  function smoothstep(x) {{
    x = x < 0 ? 0 : (x > 1 ? 1 : x);
    return x * x * (3 - 2 * x);
  }}

  // Short fade at each end of a scene so artwork never pops in or out.
  var FADE = 0.45;
  function edgeFade(tin, dur) {{
    if (dur <= FADE * 2) return 1;
    if (tin < FADE) return tin / FADE;
    if (tin > dur - FADE) return (dur - tin) / FADE;
    return 1;
  }}

  window.__drawVisuals = function (t) {{
    var scene = activeScene(t);
    var a = scene ? AUDIO[String(scene.i)] : null;
    var tin = scene ? (t - scene.start) : 0;

    // Only the smoothed envelopes are read here. The raw bands move up to 0.9
    // between adjacent frames, which reads as flicker rather than motion.
    var levelEnv = a ? sample(a.level_env, tin, a.fps) : 0;
    var bassEnv = a ? sample(a.bass_env, tin, a.fps) : 0;

    if (hydra && h) {{
      // Absolute time -- never accumulated. This is what makes a re-rendered
      // frame identical to the first pass.
      h.time = t;
      try {{ hydra.tick(1000 / FPS); }} catch (e) {{}}
      // The synth carries the frame only when there is no artwork. Behind
      // artwork it is texture, not subject -- much above ~0.25 and it competes
      // with the cover and pushes the title under its contrast floor. Its
      // opacity drifts on the smoothed level only, never on a raw band.
      var base = scene && scene.art ? 0.15 : 0.78;
      var span = scene && scene.art ? 0.08 : 0.18;
      canvas.style.opacity = (base + levelEnv * span).toFixed(4);
    }}

    for (var k = 0; k < SCENES.length; k++) {{
      var s = SCENES[k];
      var el = document.getElementById("art-" + s.i);
      if (!el) continue;
      var inside = t >= s.start && t < s.start + s.dur;
      if (!inside) {{
        // Reset everything, not just opacity. Leaving a stale transform on a
        // hidden element means the DOM carries a trace of whichever frame was
        // rendered last -- invisible, but it makes the output a function of
        // render history instead of purely of t, which is exactly the property
        // a frame-seeking renderer must be able to rely on.
        el.style.opacity = "0";
        el.style.transform = "scale(1)";
        continue;
      }}

      var m = MOVES[s.i % MOVES.length];
      var e = smoothstep(s.dur > 0 ? (t - s.start) / s.dur : 0);
      var scale = m.s0 + (m.s1 - m.s0) * e;
      var x = m.x0 + (m.x1 - m.x0) * e;
      var y = m.y0 + (m.y1 - m.y0) * e;

      // Reactivity is opt-in per track and deliberately tiny -- a breath on
      // top of the camera move, not a bounce. Tracks without a real low-end
      // pulse get pure camera motion (see `reactive` in runtime_js).
      if (s.reactive) scale *= 1 + bassEnv * 0.018;

      el.style.opacity = (0.88 * edgeFade(tin, s.dur)).toFixed(4);
      el.style.transform =
        "scale(" + scale.toFixed(4) + ") translate(" +
        x.toFixed(2) + "px, " + y.toFixed(2) + "px)";
      // No audio-driven filter. Treble moved ~0.17 per frame, so driving
      // saturation from it strobed the colour.
    }}
  }};

  // Hook the driver onto the main timeline so it runs for every frame the
  // renderer seeks to.
  //
  // Callbacks are the wrong tool here. GSAP's seek() takes suppressEvents and
  // it defaults to TRUE, so onUpdate -- on the timeline or on a child tween --
  // does not fire for a seek. A frame-seeking renderer would then draw every
  // frame with the values from time 0.
  //
  // A property *write* is not an event, so it always happens during render.
  // Tweening a plain object whose property is an accessor turns each render
  // into a call to the driver, seek or not, in order or not.
  function attach() {{
    var tl = window.__timelines && window.__timelines["main"];
    if (!tl || typeof gsap === "undefined") {{ return false; }}

    var driver = {{ _t: 0 }};
    Object.defineProperty(driver, "t", {{
      get: function () {{ return this._t; }},
      set: function (v) {{ this._t = v; window.__drawVisuals(v); }},
    }});

    tl.to(driver, {{ t: TOTAL, duration: TOTAL, ease: "none" }}, 0);
    window.__drawVisuals(0);
    return true;
  }}
  if (!attach()) {{
    document.addEventListener("DOMContentLoaded", attach);
  }}
}})();
</script>
"""
