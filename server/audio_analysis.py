"""Bakes an audio clip into a per-frame array of energy values.

Why bake instead of analysing live in the browser: HyperFrames renders by
seeking a paused timeline to each frame and screenshotting it. A live
AnalyserNode only produces values while audio is actually *playing* in real
time, so under a frame-seeking renderer it returns silence -- and even if it
didn't, two passes over the same frame could disagree, which breaks the
project's determinism rule.

Baking sidesteps both problems. The analysis runs once here, in ffmpeg and
numpy, and the composition reads frame N's values out of a plain JSON array.
Frame N looks identical no matter when or how often it is rendered, so the
visuals can react to the music and still be perfectly reproducible.

Output is one dict per clip:

    {
      "fps": 30, "frames": 300, "duration": 10.0,
      "bass": [...], "mid": [...], "treble": [...], "level": [...],
      "onsets": [0, 14, 29, ...],       # frame indices
      "beat_frames": [...], "bpm": 128.0
    }

Every series is normalized to 0..1 and one value long per rendered frame, so a
composition can index it directly with `Math.round(t * fps)`.
"""
import json
import os
import subprocess
from typing import Optional

import numpy as np
import static_ffmpeg

static_ffmpeg.add_paths()

SAMPLE_RATE = 22050
FFT_SIZE = 2048

# Band edges in Hz. Deliberately broad -- these drive scale/blur/colour, not a
# spectrum analyser display, so musically-meaningful splits beat narrow ones.
BANDS = {
    "bass": (20, 250),
    "mid": (250, 4000),
    "treble": (4000, 11000),
}


def _decode(audio_path: str) -> Optional[np.ndarray]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-f", "f32le", "-acodec", "pcm_f32le",
             "-ar", str(SAMPLE_RATE), "-ac", "1", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        )
        if not result.stdout:
            return None
        return np.frombuffer(result.stdout, dtype=np.float32).astype(np.float64)
    except Exception:
        return None


def _normalize(series: np.ndarray) -> np.ndarray:
    """Scale to 0..1 against the 97th percentile rather than the max, so one
    stray transient doesn't flatten the whole track into the bottom of the
    range. Quiet ambient passages still get usable dynamics this way."""
    if series.size == 0:
        return series
    ceiling = float(np.percentile(series, 97))
    if ceiling <= 1e-9:
        return np.zeros_like(series)
    return np.clip(series / ceiling, 0.0, 1.0)


def _envelope(series: np.ndarray, fps: int,
              attack: float = 0.06, release: float = 0.45) -> np.ndarray:
    """Asymmetric one-pole follower: rises quickly, falls slowly.

    Raw band energy moves 0.06-0.17 between adjacent frames (and can jump by
    0.9), so driving a transform straight off it reads as flicker rather than
    as motion. Fast attack keeps a hit feeling immediate; slow release turns it
    into a swell that decays over roughly half a second instead of snapping
    back on the next frame."""
    if series.size == 0:
        return series
    a_coef = 1.0 - np.exp(-1.0 / max(1e-6, attack * fps))
    r_coef = 1.0 - np.exp(-1.0 / max(1e-6, release * fps))

    out = np.empty_like(series)
    acc = float(series[0])
    for i, v in enumerate(series):
        coef = a_coef if v > acc else r_coef
        acc += (float(v) - acc) * coef
        out[i] = acc
    return out


def _stft_frames(pcm: np.ndarray, hop: int) -> tuple[np.ndarray, np.ndarray]:
    """Magnitude spectrogram, one column per rendered frame."""
    n_frames = max(1, int(np.ceil(len(pcm) / hop)))
    padded = np.pad(pcm, (FFT_SIZE // 2, FFT_SIZE), mode="constant")
    window = np.hanning(FFT_SIZE)

    cols = []
    for i in range(n_frames):
        start = i * hop
        seg = padded[start:start + FFT_SIZE]
        if len(seg) < FFT_SIZE:
            seg = np.pad(seg, (0, FFT_SIZE - len(seg)), mode="constant")
        cols.append(np.abs(np.fft.rfft(seg * window)))

    mags = np.array(cols).T  # (freq_bins, frames)
    freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / SAMPLE_RATE)
    return mags, freqs


def _spectral_flux(mags: np.ndarray) -> np.ndarray:
    """Half-wave-rectified spectral flux: how much *new* energy each frame
    brings. Shared by onset picking and tempo estimation."""
    if mags.shape[1] < 3:
        return np.zeros(mags.shape[1])
    flux = np.sqrt(np.maximum(0.0, np.diff(mags, axis=1))).sum(axis=0)
    flux = np.concatenate([[0.0], flux])
    peak = flux.max()
    return flux / peak if peak > 1e-9 else flux


def _detect_onsets(flux: np.ndarray, fps: int) -> list[int]:
    """Peak-picks the flux against an adaptive local-median threshold. These
    are the frames where a kick, snare or chord stab lands -- what a cut or a
    flash should sit on."""
    if flux.size < 3 or flux.max() <= 1e-9:
        return []

    # Local median over ~0.4s, plus a small floor so near-silence stays quiet.
    win = max(3, int(fps * 0.4) | 1)
    pad = win // 2
    padded = np.pad(flux, (pad, pad), mode="edge")
    local = np.array([np.median(padded[i:i + win]) for i in range(len(flux))])
    threshold = local * 1.4 + 0.04

    # A peak must beat its neighbours and the threshold, and onsets can't sit
    # closer than ~100ms or a busy hi-hat pattern becomes a strobe.
    min_gap = max(1, int(fps * 0.1))
    onsets: list[int] = []
    for i in range(1, len(flux) - 1):
        if flux[i] <= threshold[i]:
            continue
        if flux[i] < flux[i - 1] or flux[i] < flux[i + 1]:
            continue
        if onsets and i - onsets[-1] < min_gap:
            if flux[i] > flux[onsets[-1]]:
                onsets[-1] = i
            continue
        onsets.append(i)
    return onsets


MIN_BPM, MAX_BPM = 70.0, 180.0
# Tempo prior centre. Most programmed music sits near here, so it is the right
# tie-breaker when the autocorrelation cannot tell a beat from its subdivision.
PREFERRED_BPM = 120.0


def _estimate_bpm(flux: np.ndarray, onsets: list[int], fps: int,
                  n_frames: int) -> tuple[Optional[float], list[int], float]:
    """Tempo by autocorrelating the onset-strength envelope.

    The obvious approach -- histogram the gaps between onsets and take the mode
    -- locks onto whatever subdivision is densest, so a house track with busy
    hi-hats reports 200 BPM instead of 130. Autocorrelation looks at the whole
    envelope's periodicity instead, which finds the beat even when most onsets
    fall between beats."""
    if len(onsets) < 4 or flux.size < fps:
        return None, [], 0.0

    env = flux - flux.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    if ac.size < 2 or ac[0] <= 1e-9:
        return None, [], 0.0
    ac = ac / ac[0]

    min_lag = max(1, int(round(60.0 * fps / MAX_BPM)))
    max_lag = min(len(ac) - 1, int(round(60.0 * fps / MIN_BPM)))
    if max_lag <= min_lag:
        return None, [], 0.0

    lags = np.arange(min_lag, max_lag + 1)
    window = ac[min_lag:max_lag + 1]

    # Every subdivision and multiple of the true beat shows a peak, so raw
    # argmax lands on double- or half-time about as often as on the beat.
    # Weighting by a log-normal prior centred on 120 BPM breaks the tie the way
    # a listener would -- it's the standard fix (Ellis' tempo estimator) and it
    # keeps a 128 BPM house track off both 64 and 256.
    cand_bpm = 60.0 * fps / lags
    prior = np.exp(-0.5 * (np.log2(cand_bpm / PREFERRED_BPM) / 0.9) ** 2)
    best = int(np.argmax(window * prior))
    period = int(lags[best])
    if period <= 0:
        return None, [], 0.0

    # How periodic the envelope actually is at the chosen lag. Steady
    # programmed music scores high; rubato piano or free-time ambient scores
    # low, and a composition should fall back to raw onsets when it does.
    confidence = float(np.clip(window[best], 0.0, 1.0))
    bpm = 60.0 * fps / period

    # Anchor the grid on a real onset near the start so the first beat lands on
    # actual audio rather than an arbitrary offset.
    anchor = onsets[0]
    best_anchor, best_score = anchor, -1
    onset_set = set(onsets)
    for cand in onsets[:8]:
        score = sum(1 for k in range(n_frames // period + 1)
                    if (cand + k * period) in onset_set)
        if score > best_score:
            best_anchor, best_score = cand, score

    grid = list(range(best_anchor, n_frames, period))
    return round(bpm, 1), grid, round(confidence, 3)


def analyze(audio_path: str, fps: int = 30, duration: Optional[float] = None) -> Optional[dict]:
    """Returns the baked analysis for one clip, or None if the audio can't be
    decoded (a missing clip must never take a render down)."""
    pcm = _decode(audio_path)
    if pcm is None or len(pcm) == 0:
        return None

    if duration:
        wanted = int(duration * SAMPLE_RATE)
        pcm = pcm[:wanted] if len(pcm) >= wanted else np.pad(pcm, (0, wanted - len(pcm)))

    hop = max(1, SAMPLE_RATE // fps)
    mags, freqs = _stft_frames(pcm, hop)
    n_frames = mags.shape[1]

    out = {
        "fps": fps,
        "frames": n_frames,
        "duration": round(n_frames / fps, 3),
    }

    for name, (lo, hi) in BANDS.items():
        sel = (freqs >= lo) & (freqs < hi)
        band = mags[sel].mean(axis=0) if sel.any() else np.zeros(n_frames)
        norm = _normalize(band)
        out[name] = [round(float(v), 4) for v in norm]
        # Smoothed twin of each band. Visual transforms should read these, not
        # the raw series -- see _envelope.
        out[name + "_env"] = [round(float(v), 4) for v in _envelope(norm, fps)]

    # Overall loudness, per frame, from the raw samples rather than the
    # spectrogram -- cheaper and less smeared.
    usable = (len(pcm) // hop) * hop
    if usable > 0:
        rms = np.sqrt((pcm[:usable].reshape(-1, hop) ** 2).mean(axis=1) + 1e-12)
        if len(rms) < n_frames:
            rms = np.pad(rms, (0, n_frames - len(rms)), mode="edge")
        lvl = _normalize(rms[:n_frames])
        out["level"] = [round(float(v), 4) for v in lvl]
        out["level_env"] = [round(float(v), 4) for v in _envelope(lvl, fps)]
    else:
        out["level"] = [0.0] * n_frames
        out["level_env"] = [0.0] * n_frames

    flux = _spectral_flux(mags)
    onsets = _detect_onsets(flux, fps)
    bpm, grid, confidence = _estimate_bpm(flux, onsets, fps, n_frames)
    out["onsets"] = onsets
    out["bpm"] = bpm
    out["beat_frames"] = [f for f in grid if f < n_frames]
    # Tempo on a 10s excerpt of arbitrary music is genuinely unreliable -- and
    # for rubato classical it is barely even defined. The per-frame bands and
    # the detected onsets are exact; the grid is advisory, so it ships with a
    # confidence value rather than pretending otherwise.
    out["beat_confidence"] = confidence

    # How much the low end actually swings over the clip. This -- not tempo
    # confidence -- is what decides whether a track should visibly pulse: a
    # four-to-the-floor cut has a big spread, a sparse folk recording barely
    # moves. Tempo confidence turned out to rank a fingerpicked guitar above a
    # house track, so it is the wrong signal for this decision.
    be = np.array(out["bass_env"]) if out.get("bass_env") else np.zeros(1)
    out["pulse_strength"] = round(float(np.percentile(be, 90) - np.percentile(be, 10)), 3)
    return out


# Time resolution for probe(). A beat at 120 BPM is 500ms and a cut reads as
# early or late somewhere around 20-30ms, so 10ms is comfortably below the
# threshold that matters and still cheap on a 30s preview.
PROBE_FPS = 100


def probe(audio_path: str) -> Optional[dict]:
    """Where the interesting moments are in an *untrimmed* source, in seconds.

    analyze() bakes per-frame arrays for a scene that has already been cut;
    this answers the question that comes before it -- which ten seconds of a
    30-second preview to keep, and where inside them the music actually lands.
    Same flux/onset/tempo code, run at PROBE_FPS and reported in seconds so a
    caller can hand the numbers straight to ffmpeg."""
    pcm = _decode(audio_path)
    if pcm is None or len(pcm) == 0:
        return None

    hop = max(1, SAMPLE_RATE // PROBE_FPS)
    mags, _freqs = _stft_frames(pcm, hop)
    n_frames = mags.shape[1]
    if n_frames < 3:
        return None

    flux = _spectral_flux(mags)
    onsets = _detect_onsets(flux, PROBE_FPS)
    bpm, grid, confidence = _estimate_bpm(flux, onsets, PROBE_FPS, n_frames)

    usable = (len(pcm) // hop) * hop
    if usable > 0:
        rms = np.sqrt((pcm[:usable].reshape(-1, hop) ** 2).mean(axis=1) + 1e-12)
        if len(rms) < n_frames:
            rms = np.pad(rms, (0, n_frames - len(rms)), mode="edge")
        energy = _normalize(rms[:n_frames])
    else:
        energy = np.zeros(n_frames)

    return {
        "fps": PROBE_FPS,
        "frames": n_frames,
        "duration": len(pcm) / SAMPLE_RATE,
        # Normalized 0..1 per probe frame. Kept as arrays, not lists: the only
        # consumer is the trimmer, and it does arithmetic on them.
        "energy": energy,
        "onsets": [f / PROBE_FPS for f in onsets],
        "beats": [f / PROBE_FPS for f in grid],
        "bpm": bpm,
        "beat_confidence": confidence,
    }


def analyze_to_file(audio_path: str, dest: str, fps: int = 30,
                    duration: Optional[float] = None) -> Optional[dict]:
    data = analyze(audio_path, fps=fps, duration=duration)
    if data is None:
        return None
    with open(dest, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    return data


if __name__ == "__main__":
    import sys

    path = sys.argv[1]
    d = analyze(path, fps=30)
    if not d:
        print("could not analyze", path)
        raise SystemExit(1)
    print(f"{os.path.basename(path)}: {d['frames']} frames @ {d['fps']}fps "
          f"({d['duration']}s), bpm={d['bpm']}, {len(d['onsets'])} onsets")
    for band in ("bass", "mid", "treble", "level"):
        s = d[band]
        print(f"  {band:7s} min={min(s):.3f} max={max(s):.3f} mean={sum(s)/len(s):.3f}")
    print(f"  first onsets: {d['onsets'][:12]}")
    print(f"  beat grid:    {d['beat_frames'][:12]}")
