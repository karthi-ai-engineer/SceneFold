// The timing rules of the viewer, kept free of the page so they can be tested on their own.
//
// timeline.json gives every placed clip an offset and a clock drift (see src/scenefold/timeline.py):
//   t_local = (t_master - offset_s) * (1 + drift_ppm / 1e6)
// The viewer runs one master clock and keeps every video at the clip time that clock asks for:
// small errors are fixed by playing a little faster or slower, large ones by jumping.

// A jump makes the browser decode from the previous keyframe (up to 1 s of video), which stalls the
// picture for a few hundred ms, so a playing video jumps only when it is far off (the viewer aims
// ahead by the time its jumps take). Nearer errors are caught up by speed: nobody hears a silent
// video, and 10% barely shows.
export const SEEK_S = 0.35; // further off than this, jump instead of catching up
export const CATCH_UP_S = 0.5; // remove an error over about this long
export const MAX_NUDGE = 0.1; // never play more than 10% faster or slower than the clip's rate
// Chrome plays any rate within 0.1% of 1.0 as plain normal speed, and stalls a video for about a
// frame each time its rate enters that band (measured: flipping 1.001 <-> 1.06 on every frame
// played at 0.87x; 1.002 <-> 1.06 played at 1.03x). So a silent video never sits at its own rate:
// "on time" it plays 0.3% fast or slow toward the clock, turning only once it is clearly past it,
// and a real correction is at least 1%. The error is smoothed so whole-frame jitter can't flip it.
export const SMOOTHING = 0.15; // weight of each new frame in the smoothed error
export const LOCK_S = 0.005; // back to trimming once the smoothed error is under this
export const UNLOCK_S = 0.01; // start catching up once it is over this
export const MIN_NUDGE = 0.01;
export const TRIM = 0.003; // on time: this much fast or slow, outside Chrome's normal-speed band
export const TRIM_TURN_S = 0.002; // the trim turns round once the smoothed error passes this
export const FRAME_S = 1 / 30; // working copies run at 30 fps

/** Clip seconds that pass per master second. */
export const rateOf = (clip) => 1 + (clip.drift_ppm ?? 0) * 1e-6;

export const localTime = (clip, tMaster) => (tMaster - clip.offset_s) * rateOf(clip);

export const masterTime = (clip, tLocal) => clip.offset_s + tLocal / rateOf(clip);

/** True while the clip was recording at this master time. */
export function recording(clip, tMaster) {
  const t = localTime(clip, tMaster);
  return t >= 0 && t < clip.duration_s;
}

/** Master time span covered by the placed clips: [start, end]. */
export function span(clips) {
  if (!clips.length) return [0, 0];
  const ends = clips.map((c) => masterTime(c, c.duration_s));
  return [Math.min(...clips.map((c) => c.offset_s)), Math.max(...ends)];
}

/** Keeps one video on the clock, frame by frame. */
export class Steering {
  constructor() {
    this.reset();
  }

  reset() {
    this.error = 0; // smoothed error, seconds; a video starts out assumed on time
    this.correcting = false;
    this.trim = 1; // on time: +1 plays TRIM fast, -1 plays TRIM slow
  }

  /**
   * The video shows a frame `errorS` seconds ahead (positive) or behind (negative) of where the
   * clock wants it. Returns {seek: true} or {seek: false, rate}.
   */
  update(errorS, baseRate, speed = 1) {
    if (Math.abs(errorS) > SEEK_S) {
      this.reset();
      return { seek: true };
    }
    this.error += SMOOTHING * (errorS - this.error);
    const size = Math.abs(this.error);
    if (this.correcting && size < LOCK_S) this.correcting = false;
    else if (!this.correcting && size > UNLOCK_S) this.correcting = true;
    const nominal = baseRate * speed;
    if (!this.correcting) {
      if (this.error > TRIM_TURN_S) this.trim = -1; // ahead: drift back
      else if (this.error < -TRIM_TURN_S) this.trim = 1; // behind: drift forward
      return { seek: false, rate: nominal * (1 + this.trim * TRIM) };
    }
    const nudge = Math.min(MAX_NUDGE, Math.max(MIN_NUDGE, size / CATCH_UP_S));
    return { seek: false, rate: nominal * (1 - Math.sign(this.error) * nudge) };
  }
}

/** The master clock: wall time while playing, optionally pulled toward the audible clip. */
export class MasterClock {
  constructor(now = () => performance.now()) {
    this.now = now;
    this.anchorPerf = now();
    this.anchorMaster = 0;
    this.playing = false;
    this.speed = 1;
  }

  /** Master time (seconds) at a performance.now() timestamp (ms), by default now. */
  time(perf = this.now()) {
    if (!this.playing) return this.anchorMaster;
    return this.anchorMaster + ((perf - this.anchorPerf) / 1000) * this.speed;
  }

  play() {
    if (this.playing) return;
    this.anchorPerf = this.now();
    this.playing = true;
  }

  pause() {
    if (!this.playing) return;
    this.anchorMaster = this.time();
    this.playing = false;
  }

  set(tMaster) {
    this.anchorMaster = tMaster;
    this.anchorPerf = this.now();
  }

  setSpeed(speed) {
    this.anchorMaster = this.time();
    this.anchorPerf = this.now();
    this.speed = speed;
  }

  /** Move the clock a fraction of the way toward an outside reference that is `errorS` ahead. */
  pull(errorS, fraction) {
    this.anchorMaster += errorS * fraction;
  }
}

/** Rolling statistics of sync errors (milliseconds) over the last `size` frames. */
export class ErrorStats {
  constructor(size = 300) {
    this.size = size;
    this.values = [];
    this.last = null;
    this.seeks = 0;
  }

  add(ms) {
    this.last = ms;
    this.values.push(ms);
    if (this.values.length > this.size) this.values.shift();
  }

  reset() {
    this.values = [];
    this.last = null;
    this.seeks = 0;
  }

  summary() {
    const abs = this.values.map(Math.abs).sort((a, b) => a - b);
    if (!abs.length) return { frames: 0, mean: null, meanAbs: null, p95Abs: null, maxAbs: null, last: this.last, seeks: this.seeks };
    const p95 = abs[Math.min(abs.length - 1, Math.ceil(0.95 * abs.length) - 1)];
    return {
      frames: abs.length,
      mean: this.values.reduce((s, v) => s + v, 0) / abs.length, // positive: the picture runs ahead
      meanAbs: abs.reduce((s, v) => s + v, 0) / abs.length,
      p95Abs: p95,
      maxAbs: abs[abs.length - 1],
      last: this.last,
      seeks: this.seeks,
    };
  }
}

/** "1:02.345" (or "-0:01.000" before the first clip starts). */
export function formatTime(seconds, digits = 3) {
  const sign = seconds < 0 ? "-" : "";
  const s = Math.abs(seconds);
  const minutes = Math.floor(s / 60);
  const rest = (s - minutes * 60).toFixed(digits).padStart(digits ? digits + 3 : 2, "0");
  return `${sign}${minutes}:${rest}`;
}

/** Plain words for the solver's rejection codes. */
export const REJECTED = {
  short_overlap: "overlap under 5 s",
  low_confidence: "match too weak",
  partial_match: "sound agrees only in parts",
  inconsistent: "disagrees with the other pairs",
  separate_group: "not linked to the main group",
};
