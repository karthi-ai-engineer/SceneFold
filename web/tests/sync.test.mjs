// Run with: node --test web/tests/
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CATCH_UP_S,
  ErrorStats,
  LOCK_S,
  MAX_NUDGE,
  MIN_NUDGE,
  MasterClock,
  PICTURES,
  SEEK_S,
  SOUND,
  Steering,
  TRIM,
  UNLOCK_S,
  formatTime,
  heardLate,
  localTime,
  masterTime,
  rateOf,
  recording,
  span,
  startTime,
} from "../sync.js";

const clip = { offset_s: 12.5, drift_ppm: 40, duration_s: 60 };
const close = (actual, expected, tolerance = 1e-9) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} is not within ${tolerance} of ${expected}`);

test("clip and master time convert both ways, with drift", () => {
  close(localTime(clip, 12.5), 0);
  close(localTime(clip, 22.5), 10 * (1 + 40e-6));
  for (const t of [0, 3.25, 59.9]) close(localTime(clip, masterTime(clip, t)), t);
  close(rateOf({ offset_s: 0 }), 1); // no drift measured counts as 0
});

test("a clip records from its first frame until its last", () => {
  assert.equal(recording(clip, 12.49), false);
  assert.equal(recording(clip, 12.5), true);
  assert.equal(recording(clip, masterTime(clip, 59.999)), true);
  assert.equal(recording(clip, masterTime(clip, 60.001)), false);
});

test("the covered span runs from the earliest start to the latest end", () => {
  const [start, end] = span([clip, { offset_s: 0, drift_ppm: 0, duration_s: 20 }]);
  close(start, 0);
  close(end, masterTime(clip, 60));
  assert.deepEqual(span([]), [0, 0]);
});

// A phone 94 m further from the speakers heard the event 273 ms later (sound: 2.9 ms a metre).
const far = { offset_s: 20, drift_ppm: 0, duration_s: 30, heard_late_s: 0.273 };
const nearest = { offset_s: 5, drift_ppm: 0, duration_s: 30, heard_late_s: 0 };
const untold = { offset_s: 5, drift_ppm: 40, duration_s: 30, heard_late_s: null }; // pictures couldn't tell

test("lining up the pictures holds a far phone's clip back by what it heard late", () => {
  close(heardLate(far, PICTURES), 0.273);
  close(heardLate(far, SOUND), 0);
  close(localTime(far, 20), 0); // no alignment asked for: the sound, which is what offset_s means
  close(localTime(far, 20, SOUND), 0);
  close(localTime(far, 20.273, PICTURES), 0); // its first frame now belongs 273 ms later
  close(localTime(far, 40, PICTURES), localTime(far, 40, SOUND) - 0.273);
  close(startTime(far, PICTURES) - startTime(far, SOUND), 0.273);
  close(startTime(nearest, PICTURES), 5); // the clip nearest the sound never moves
});

test("clip and master time round-trip under both alignments", () => {
  for (const align of [SOUND, PICTURES]) {
    for (const c of [far, nearest, untold]) {
      for (const t of [0, 3.25, 29.9]) close(localTime(c, masterTime(c, t, align), align), t);
    }
  }
});

test("a clip whose pictures never matched stays where its sound put it", () => {
  close(heardLate(untold, PICTURES), 0);
  close(heardLate({ offset_s: 0 }, PICTURES), 0); // an older timeline has no such field at all
  close(localTime(untold, 17, PICTURES), localTime(untold, 17, SOUND));
  close(masterTime(untold, 12, PICTURES), masterTime(untold, 12, SOUND));
  assert.equal(recording(untold, 4.99, PICTURES), false);
  assert.equal(recording(untold, 5, PICTURES), true);
});

test("under the picture alignment a far clip records, and covers the timeline, later", () => {
  assert.equal(recording(far, 20.1, SOUND), true);
  assert.equal(recording(far, 20.1, PICTURES), false); // held back: its first frame is still to come
  assert.equal(recording(far, 20.273, PICTURES), true);
  assert.equal(recording(far, 50.2729, PICTURES), true);
  assert.equal(recording(far, 50.274, PICTURES), false); // past its last frame
  const [start, end] = span([far, nearest], PICTURES);
  close(start, 5); // the nearest clip is where it always was
  close(end, 50.273); // the far clip's end moved with it
  close(span([far, nearest], SOUND)[1], 50);
  assert.deepEqual(span([], PICTURES), [0, 0]);
});

test("big errors jump; a video on time trims gently toward the clock", () => {
  const steer = new Steering();
  assert.deepEqual(steer.update(SEEK_S + 0.01, 1), { seek: true });
  assert.deepEqual(steer.update(-(SEEK_S + 0.01), 1), { seek: true });
  let rate;
  for (let i = 0; i < 50; i++) rate = steer.update(0.005, 1.00004).rate; // a little ahead
  close(rate, 1.00004 * (1 - TRIM));
  for (let i = 0; i < 50; i++) rate = steer.update(-0.005, 1.00004).rate; // a little behind
  close(rate, 1.00004 * (1 + TRIM));
  close(new Steering().update(0, 1, 0.5).rate, 0.5 * (1 + TRIM)); // slow motion scales it all
});

test("a silent video never plays inside Chrome's normal-speed band, where rate changes stall", () => {
  const steer = new Steering();
  let x = 0.1234;
  for (let i = 0; i < 5000; i++) {
    x = (x * 9301 + 49297) % 233280; // deterministic pseudo-random errors, -40..+40 ms
    const fix = steer.update(((x / 233280) - 0.5) * 0.08, 1.00005);
    assert.ok(Math.abs(fix.rate - 1) > 0.002, `rate ${fix.rate}`);
  }
});

const settle = (steer, errorS, frames = 60) => {
  let fix;
  for (let i = 0; i < frames; i++) fix = steer.update(errorS, 1);
  return fix.rate;
};

test("a clear error is corrected by at least 1%, and in the right direction", () => {
  const rate = settle(new Steering(), -0.02); // behind: play faster
  assert.ok(rate >= 1 + MIN_NUDGE - 1e-12, `rate ${rate}`);
  close(settle(new Steering(), 0.2), 1 - MAX_NUDGE); // ahead: slower, never past the limit
  close(settle(new Steering(), 0.03, 200), 1 - 0.03 / CATCH_UP_S, 1e-6);
});

test("the frame-by-frame jitter of whole frames does not flip the speed", () => {
  // a video exactly on time still shows whole frames: its error saw-tooths within half a frame
  const steer = new Steering();
  const rates = new Set();
  for (let i = 0; i < 300; i++) rates.add(steer.update(i % 2 ? 0.0165 : -0.0165, 1).rate);
  assert.deepEqual([...rates], [1 + TRIM]);
});

test("once correcting, it keeps going until the error is well inside the band", () => {
  const steer = new Steering();
  settle(steer, -0.03); // clearly behind
  let rate = 1;
  for (let i = 0; i < 3; i++) rate = steer.update(-0.012, 1).rate; // between LOCK_S and UNLOCK_S
  assert.ok(rate > 1, "still catching up inside the hysteresis band");
  for (let i = 0; i < 40; i++) rate = steer.update(0, 1).rate;
  assert.ok(Math.abs(rate - 1) <= TRIM + 1e-12, "back to gentle trimming");
  assert.ok(LOCK_S < UNLOCK_S);
});

test("the master clock runs, pauses, jumps, changes speed, and can be pulled", () => {
  let now = 1000;
  const clock = new MasterClock(() => now);
  clock.set(5);
  now += 2000;
  close(clock.time(), 5); // paused
  clock.play();
  now += 1500;
  close(clock.time(), 6.5);
  clock.setSpeed(0.5);
  now += 1000;
  close(clock.time(), 7);
  clock.pull(0.1, 0.5);
  close(clock.time(), 7.05);
  clock.pause();
  now += 5000;
  close(clock.time(), 7.05);
  close(clock.time(now + 9999), 7.05);
});

test("error statistics keep a rolling window", () => {
  const stats = new ErrorStats(4);
  for (const ms of [1, -2, 3, -4, 10]) stats.add(ms);
  const s = stats.summary();
  assert.equal(s.frames, 4);
  assert.equal(s.maxAbs, 10);
  assert.equal(s.last, 10);
  close(s.meanAbs, (2 + 3 + 4 + 10) / 4);
  stats.reset();
  assert.equal(stats.summary().frames, 0);
});

test("times read as minutes and seconds", () => {
  assert.equal(formatTime(62.3456), "1:02.346");
  assert.equal(formatTime(5), "0:05.000");
  assert.equal(formatTime(-1), "-0:01.000");
  assert.equal(formatTime(75.4, 0), "1:15");
});
