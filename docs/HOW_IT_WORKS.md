# How Scenefold works

The [README](../README.md) shows what to run. This page explains what each step does, how it was
measured, and where it fails. Sections in the order the pipeline runs.

---

## What ingest does

`scenefold ingest <event> <videos or folders>` prepares phone videos of one event for the later stages:

- Keeps an untouched, read-only copy of every original.
- Makes a working copy of each video: 720p, a steady 30 fps, upright, HDR converted to normal colors,
  plus a mono 48 kHz WAV of its sound. Picture and sound start at exactly the same instant and stay
  together, even on phones whose sound clock disagrees with the file's timestamps.
- Records each clip in `manifest.json` as `ok`, `warning` (for example no audio, very short, or a
  cut-off file) or `failed`, always with the reason.
- Skips files that are not videos and videos it has already processed. One bad file never stops the rest.

```
data/<event>/
├─ originals/      your videos, untouched and read-only
├─ proxies/        working copies (<clip>.mp4) and their sound (<clip>.wav)
├─ manifest.json   what each clip is, where its files are, and any problems
├─ timeline.json   where each clip sits on the shared clock          (sync)
├─ observations/   what each clip shows, one file per clip           (observe)
└─ cut.json, cut.mp4   the shot list, with a reason each, and the film  (cut)
```

## What sync does

`scenefold sync <event>` puts the event's clips on one master timeline by comparing their sound. No
speech recognition is involved: music, claps, cheering, and background talk all help.

- Compares every pair of clips that have sound, and scores how clearly each match beats the next-best one.
- Measures and cancels clock drift: phone audio clocks run a few to a few hundred parts per million
  fast or slow, which adds up to tens of milliseconds over a few minutes.
- Solves all pairs together and drops pairs that disagree with the rest, so one bad match can't
  move the other clips. Clips that never overlap are placed through the clips between them.
- Matches the placed clips again by their pictures, which says how far each phone stood from the
  sound (below). Skip it with `--sound-only`; it is the slow part, because it reads every picture.
- Writes `timeline.json` with each clip's offset, drift, confidence and distance, every pair
  measurement, and the reason for any clip it could not place. A clip is never forced onto the
  timeline.

**Measured accuracy.** On real phone clips of a live event from the
[Jiku dataset](https://traces.cs.umass.edu/docs/traces/multimedia/), sync agrees with its published
ground truth within 6.4 ms when the clips overlap for about three minutes, and within 26 ms for
80-second overlaps, for five of six phones. The sixth (a Nexus S) differs by 70–110 ms; which side is
right is not settled yet. On the same clips it matched
[audio-offset-finder](https://github.com/bbc/audio-offset-finder) and
[audalign](https://github.com/benfmiller/audalign) over 80 seconds and beat both over three minutes,
where their lack of clock-drift handling shows (`tools/baselines.py`).

**Sound takes time to arrive, so the pictures are matched too.** Sound travels about one metre
every 2.9 ms, so a phone further from the speakers hears everything late — and sync, which listens,
ends up putting its picture ahead of everybody else's by exactly that much: its flash comes first
on the shared clock. At a stadium concert the phones were up to 423 ms apart in when they heard the
music, twelve frames of visible mismatch. So the placed clips are matched a second time by how
their brightness changes (stage lighting, flashes), which gives each clip a `heard_late_s`: how
much later than the nearest clip that phone heard the event.

- `scenefold sync` reports it as a distance, and the viewer can line up the pictures instead of the
  sound — what you want when watching several angles at once.
- It needs light that changes together. Where the lighting is steady it says it cannot tell instead
  of guessing: a match is believed only when it stands clear of matches at unrelated times *and*
  leads every other moment it could have matched, since anything that repeats — stage lighting on
  a beat, or the mark video encoding leaves on every keyframe — matches at every repeat.
- Accuracy: on drawn clips where the true answer is zero it reads within 17 ms, half a frame.

**Different nights, same song.** Bands play along to backing tracks that are identical every night,
so clips of the same song from two shows can match on the music alone. Sync checks that a match
holds all through the overlap (the singing, talk, and crowd must line up too) and sets aside pairs
that match only in parts, so clips from another night are reported instead of placed. Only the
largest group is placed for now.

**Known limits.** Sound that repeats exactly, like the same recorded song played twice, can match
the wrong place when only two clips share it. A phone that moves while filming shifts its sound by
about 3 ms per metre, and one distance per clip cannot follow it. Edited uploads (with cuts) can't
be placed as one clip. Clips without usable sound can't be placed yet.

`scenefold evaluate <event> <truth.json>` measures sync error against ground truth: moments such as
claps, with their time in each clip that caught them.

## What fusing does

`scenefold fuse <event>` is where the clips stop being separate. Every clip's timed moments go onto
the shared clock, and the ones that land together are taken to be the same thing happening:

```
Event coldplay-jan26: 311 moments on the shared clock, 118 of them caught by more than one clip
Where the clips disagree: 69 (62 left unresolved, which is the honest answer when no camera had a
clearly better view)
    198.51 s  both  seen by 5 clips
      A large outdoor concert at night features a brightly lit stage with dynamic lighting effects
```

- **Corroboration is the point.** Five phones catching one instant is far stronger evidence than
  one phone describing it. On clips that all film the same thing, 219 of 298 moments were caught by
  more than one camera; on real concert footage, where phones point different ways, 118 of 311.
- **Disagreements are found, not smoothed over.** When two clips agree something happened and a
  third with as good a view caught nothing, that is recorded with its type and either a resolution
  or an honest "unresolved".
- **Resolved by the better view, never by majority.** Three phones behind a pillar do not outvote
  the one with a clear line of sight. When the camera with the best view is the one that missed it,
  that stays unresolved — it is exactly the case where the thing may not have happened at all.
- **The accounts are read against each other**, which arithmetic cannot do: the model on your
  computer is shown two descriptions of one moment and asked whether both could be true at that
  instant. Differences of wording, detail or focus are not disagreements; a dark empty stage
  against a lit one with a band on it is. The same pair of sentences is only read once however
  many moments they cover, so a five-minute event costs about fifty readings, not three hundred.
  Skip it with `--no-reading`.
- Everything lands in `knowledge.sqlite`, which a later phase can ask questions of, and every claim
  carries the clip it came from.

## What the story does

`scenefold story <event>` writes a short account of what happened, from the event store and
nothing else:

```
  0:33.9  A large concert at night, the audience holding up glowing lights, the stage shifting
          from bright white beams to warm yellow-green.
          from f098e4bb
  2:48.2  A performer stands on a circular stage while the audience holds up red lights.
          (the clips disagree here)
          from 34b92d86, f098e4bb
```

Every sentence ends up pointing at a moment, and the citations are checked **in code, not by the
model**: the moment has to exist in the store, and a clip that saw it has to have been filming at
that time. A sentence that fails is removed and the reason kept in `story.json`, so a reader sees
only what the footage supports and anyone auditing can see what was thrown away.

That is the difference between a story about an event and a story that merely sounds like one: not
that the model behaves, but that nothing reaches a reader without footage behind it. On the concert
it kept 15 sentences, dropped none, and marked 4 as moments the clips disagreed about.

`scenefold ask <event> "<question>"` answers from the same store, cited the same way — and says
so when it cannot:

```
$ scenefold ask coldplay-jan26 "what were the crowd doing with their lights?"
They used the lights to form thousands of glowing dots, seen from above. (the clips disagree here)
  at 3:18.9, from 34b92d86, 60780912, eae777b3

$ scenefold ask coldplay-jan26 "was anybody hurt in the crowd?"
The footage does not show this.
```

**What this cannot check**: whether a sentence that cites a real moment describes it truthfully. A
model can cite correctly and still embroider. That needs a person watching footage they know.

## Who is who across angles

`scenefold people <event>` asks the model, once every ten seconds, who it can make out in each clip
and what they are wearing. `scenefold identify <event>` then joins those sightings up: first within
a clip, so the red top in one window and the red top in the next are one person, then across clips
on the shared clock, so the red top one phone filmed from the left is the red top another filmed
from the right.

Both joins are the same problem — a set of people here, a set there, at most one of each can be the
other — and both are solved by optimal assignment rather than by taking the best-looking pair first.
Two descriptions are scored on the colour-and-garment pairs in them ("red sleeveless top" → red top,
sleeveless top), because those are what separate one person from the next; bare colours count for a
quarter, since at a lit concert everybody is partly black.

Three things it refuses to do:

- **Force a match.** Somebody only one phone filmed stays one person seen from one angle. That is
  the common case at a real event, not a failure.
- **Pretend to be sure.** A join the words only half support is reported as worth checking.
- **Match on nothing.** "A person", "dark clothing", "dark jeans" pick out half the event, so a
  sighting described that way is dropped before any matching happens.

**The failure to know about.** Where people are too small to make out, the model does not say so.
It stops describing people and starts producing a stock answer — one plausible concert-goer, over
and over — and because the repeats are identical, they match each other across angles and come out
marked beyond doubt. Six angles of a stage gave 137 different outfits and 3.8 people a look, and
the commonest outfit was 12% of the sightings. Five angles of a stadium at night gave 19 outfits
and 1.4 people a look, with 56% of the sightings being one invented red top. `scenefold identify`
prints those numbers every time, and says plainly when one description has taken over the event:

```
Behind it: 136 sightings, 19 different outfits, 1.45 people described per look
  Doubt this: 56% of the sightings are the same outfit (red sleeveless top, black trousers).
  Where people are too small to make out, the model stops describing them and repeats one
  plausible person, and those repeats match each other across angles perfectly well.
```

**Other known limits.** This reads clothing out of a small model's words, not out of the pixels.
Two people really can wear the same black t-shirt, and no description will ever separate them.
Accuracy on real footage is not yet measured: that needs an event where who is who is known, which
is the same hand-labelled event the observations are waiting on. Having the model referee its own
doubtful matches was tried and dropped — across three framings of the question its answers swung
between never rejecting a pair and rejecting nearly all of them, so the matches rest on the words
and on an honest "worth checking".

## What the cut does

`scenefold cut <event>` edits the angles into one film. It judges each second of each clip on three
things a camera can be wrong about — how much detail the picture holds, how far the whole frame
shifts (a phone being waved about, or a fast pan) and how much is crushed black or blown white —
and scores them against the other angles of the same event.

That is everything a camera can be wrong about and nothing about what it was pointed at, so the cut
also reads the event store: how much happened in each second, weighted by how many phones caught it,
and how much of that each angle has evidence for. A clip that was filming but reported nothing was
pointed somewhere else. The two are added together, so an angle wins a moment by having both seen it
and been worth looking at — and a cut across a moment costs more than one on the quiet in front of
it, which is where an editor would put it.

Those scores are weighed against three rules an editor would recognise: hold a shot for at least a
few seconds, don't cut unless the new angle is clearly better, and don't bounce straight back to
the angle you just left. Every way the film could be built is weighed at once, so the result is the
best *sequence of shots*, not the best angle second by second.

- The sound comes from one microphone and runs unbroken: the longest clip, and of equally long
  ones the nearest to the stage, because its sound is the least delayed. The film lasts exactly as
  long as that clip was recording.
- Pictures are lined up on the event, not on when each phone heard it, so a cut never jumps in time.
- `cut.json` records every shot with the reason it was chosen, and `cut.mp4` is the film.
  `--plan-only` writes the shot list without rendering.

On the concert, that reads as a shot list a person could argue with:

```
     0.0-  59.0 s  Dharm Bharodiya      the only angle recording
    59.0- 158.0 s  Somnath Das          caught what was happening, and had the better picture of those that did
   158.0- 228.0 s  Gareth Sequeira      steadiest picture of the angles recording
   228.0- 235.0 s  Dharm Bharodiya      kept rolling: cutting away would have cost more than it gained
   ...
   283.0- 289.0 s  Adrit Girish         caught what was happening, which the other angles missed
```

Knowing what happened turned 13 shots into 10 and moved 21 seconds between angles. On the drawn demo
event it changes nothing at all, and that is right: every clip films the same show, so every angle
saw everything (shares of 0.92–0.95, against 0.16–0.75 on real phones pointed different ways).

**Known limits.** Footage outside the microphone clip's span isn't used — the price of never cutting
the sound. What the cut knows about "what happened" is only as good as the event store behind it,
which is built on a small model's descriptions; that is why being pointed at the moment is worth
half of what the picture is worth, and never more. An event with no store yet is cut on the picture
alone, exactly as before.

## What observing does

`scenefold observe <event>` asks a model what each clip shows, a few seconds at a time, and writes
it to `observations/<clip_id>.json` — times in that clip's own seconds, so re-running sync never
invalidates them.

- **It runs on your computer.** The default is `qwen3.5:4b` through [Ollama](https://ollama.com):
  about 3 seconds per window on a laptop GPU, no cost, and no footage leaves the machine. That
  matters, because most footage is of people who agreed to be filmed by a friend, not to be
  uploaded to anyone's API. Any other model is one small class (`observe.Watcher`).
- **Each clip is watched alone**, so two angles of one moment stay two independent witnesses. A
  disagreement between them only means something if neither account was written with the other in
  view.
- **Nothing is taken as true.** Each observation is kept with a reason to doubt it: how good the
  picture was over that window, which model said it, and when. Small models are confident about
  everything, so their self-rated confidence is not recorded — the picture score is.
- Running it again costs nothing: clips already watched with the same model and the same question
  are left alone (`--again` overrides).

It also **listens**, if you install the speech extra. Whisper writes down what was said with a time
for every word, which is what later phases need to find the moment somebody said something.

- Whisper fills silence with whatever it expects to hear, so speech is only kept where a voice was
  actually detected. On twenty minutes of concert footage it kept one short line — the music did
  not become pages of invented lyrics. On a spoken test clip it caught every word, each timed.
- It uses your graphics card if NVIDIA's maths libraries are installed, and the processor if not:
  a missing library makes the run slower, never failed.
- Watching and listening are cached apart, so adding speech later doesn't re-watch the pictures.

It also finds the **moments** in each clip by arithmetic alone — when the sound suddenly grows (a
clap, a hit, a cheer starting) and when the picture suddenly changes (a flash, a light cue). A
model watching ten seconds can say *what* happened but not *when* inside them; an onset can be
placed to a few hundredths of a second without understanding anything. So each description is
pulled onto the moment that stands out most inside its window, and each spoken line onto the sound
that starts it. On the concert clips, 77 of 91 windows ended up with an exact time; a window
described as "bright blue lights and a crane structure" now points at 18.34 s rather than
"somewhere in 12–24 s". Where nothing stands out, the coarse time is kept rather than invented.

```sh
uv run scenefold observe my-event            # needs `ollama pull qwen3.5:4b` once
uv sync --extra speech                       # once, if you want speech as well
uv run scenefold observe my-event --speech-model small
```


---

# Using it, step by step

### Checking sync with claps

Film a few sharp claps that every phone hears, near the start and the end. Find each clap's time in
every clip, for example by stepping frame by frame through the working copies in
`data/<event>/proxies/`, and write them down:

```json
{"moments": [
  {"label": "first clap", "times": {"VID_20260917_153012.mp4": 19.100, "IMG_4821.MOV": 0.620}},
  {"label": "last clap",  "times": {"VID_20260917_153012.mp4": 28.233, "IMG_4821.MOV": 9.753}}
]}
```

```sh
uv run scenefold evaluate my-event claps.json
```

Clips are named by file name, or by clip ID when two files share a name.

### Understanding what happened

Everything so far works on sound and pixels alone. The next three steps need a model, running on
your own computer through [Ollama](https://ollama.com) — no account, no upload, no cost:

```sh
ollama pull qwen3.5:4b
uv run scenefold observe my-event     # what each clip shows, and what was said in it
uv run scenefold fuse my-event        # the same moment seen by several phones becomes one event
uv run scenefold story my-event       # a short account, every sentence citing the footage
```

Really from the concert, shortened here only by cutting whole sentences:

```
What happened at coldplay-jan26, as the footage has it:

  0:48.6  A lone performer stands on a stage while a massive audience holds up thousands of
          red lights [1].
          from 34b92d86
  1:15.3  A large concert is taking place at night with a massive crowd holding up glowing
          lights [1].
          from 34b92d86, f098e4bb
  2:48.2  A performer stands on a circular stage while a massive audience holds up numerous
          red lights creating a dense field of light that occasionally dims, though one
          camera caught nothing here [1]. (the clips disagree here)
          from 34b92d86, 60780912

Story: data/coldplay-jan26/story.json
```

Each clip is watched **on its own** — one clip, a few seconds at a time — so two angles stay two
independent witnesses, and a moment several of them caught means something. Every sentence is
checked in code, not by the model: the moment it cites has to exist, and a clip that saw it has to
have been filming then. Sentences that fail are dropped, with the reason kept.

Then ask it things:

```sh
uv run scenefold ask my-event "what were the crowd doing with their lights?"
```

```
They used the lights to form thousands of glowing dots, seen from above. (the clips disagree here)
  at 3:18.9, from 34b92d86, 60780912, eae777b3
```

It says "The footage does not show this" rather than guessing, which is the point of asking it at
all.

Optionally, who was there and which angles caught them:

```sh
uv run scenefold people my-event      # who each clip can see, and what they are wearing
uv run scenefold identify my-event    # who is the same person across the angles
```

Read [Who is who across angles](#who-is-who-across-angles) before trusting this one: it works where
people are large and lit, and invents them where they are not — and it says which it thinks it is
looking at.

### Making the film

```sh
uv run scenefold cut my-event
```

Really from the concert, with the uploaders' names shortened to fit:

```
Film of coldplay-jan26: 10 shots, 5:08.0 long, sound from Dharm (recorded longest, so its
sound covers the most of the event)
     0.0-  59.0 s  Dharm     the only angle recording
    59.0- 158.0 s  Somnath   caught what was happening, and had the better picture of those that did
   158.0- 228.0 s  Gareth    steadiest picture of the angles recording
   228.0- 235.0 s  Dharm     kept rolling: cutting away would have cost more than it gained
   235.0- 246.0 s  Jyoti     steadiest picture of the angles recording
   246.0- 256.0 s  Gareth    caught what was happening, and had the better picture of those that did
   256.0- 266.0 s  Dharm     sharpest picture of the angles recording
   266.0- 283.0 s  Gareth    sharpest picture of the angles recording
   283.0- 289.0 s  Adrit     caught what was happening, which the other angles missed
   289.0- 308.0 s  Jyoti     best exposed of the angles recording
Shots: data/coldplay-jan26/cut.json
Film: data/coldplay-jan26/cut.mp4
```

Every shot carries the reason it was chosen, so the film is something you can argue with rather
than something you have to take on trust.

### Watching the clips together

```sh
uv run scenefold view my-event
```

This opens the viewer in your browser (served from your own computer only, `http://127.0.0.1:8765`).
Every placed clip plays at once, lined up on the shared clock; clips that weren't recording at that
moment say when they start or that they stopped.

- **Space** plays or pauses; **←/→** jump 5 seconds; **, and .** step one frame; **1–9** pick whose
  sound you hear. Click or drag the lanes under the videos to jump anywhere. 0.25× and 0.5× help
  when checking a clap frame by frame.
- **Line up** chooses what the shared clock holds together: the **pictures** (what you want when
  watching several angles, since a distant phone heard the event late) or the **sound heard**.
- **Sync health** shows, for every frame each video shows, how far it is from where the clock wants
  it. On real phone clips it stays within half a frame; one frame at 30 fps is 33 ms.
- **Sync report** shows which clips were placed, how far each phone stood from the sound, every
  pair measurement, and why any was set aside.

The clock follows the clip you are listening to, so its sound is never sped up or slowed down; the
other videos are nudged a little faster or slower to stay with it.

