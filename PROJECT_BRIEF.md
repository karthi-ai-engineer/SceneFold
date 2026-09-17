# Scenefold — Project Brief (Version 1)

> **Many people film the same moment. Each video holds part of the truth. Scenefold combines them into one synchronized, understandable, and trustworthy picture of what happened.**

*Scenefold: many views of one scene, folded into one story. The concept is inspired by the "Rashomon effect," where several witnesses describe the same event differently.*

---

## 🧭 How to Use This Document

This brief is a **compass, not a specification**.

- It explains the **vision, the outcomes we want, what we have already learned, and the questions that are still open**.
- It deliberately does **not** dictate every implementation detail. Where the brief is silent, **research, compare options, and propose the best approach** with clear reasoning.
- If you find a better idea than something suggested here, **say so and explain why**. Suggestions in this file are starting points, not constraints.
- Only the items under **"Principles"** are firm. Everything else is open to improvement.
- Library versions, model names, and prices change quickly. **Verify anything time-sensitive** before relying on it.

---

## 1. The Idea

### The problem
At concerts, matches, weddings, and public events, many people record the same moment on their phones. Those videos:

- start and stop at different times,
- show different angles with different blind spots,
- share no common clock,
- are watched one by one, which is slow and confusing.

No single video tells the whole story, and different videos can even seem to contradict each other.

### The idea
Scenefold takes a set of videos of the same event and produces:

1. **One shared timeline**: every clip placed on the same clock.
2. **One understanding of the event**: who did what, when, and which cameras saw it.
3. **One honest story**: a narrative that cites its sources and shows where the cameras disagree.
4. **One edited film**: an automatic "director's cut" that picks the best angle for each moment.

### The two layers

| Layer | Question it answers | Nature |
|---|---|---|
| **Layer 1: Alignment** | *When* did each clip happen? | Signal processing, no understanding needed |
| **Layer 2: Knowledge** | *What* happened, and *who* saw it? | Perception + reasoning + structured memory |

Layer 1 makes the videos play together. Layer 2 turns them into knowledge.

---

## 2. Who It's For

Scenefold is designed for any situation where several people film one moment. Example audiences:

- **Amateur and school sports**: parents' phone clips become a multi-angle replay.
- **Weddings and family events**: guest videos become one complete film.
- **News verification**: reconstructing public events from bystander footage.
- **Content creators**: automatic multi-camera sync and editing.
- **Coaching**: reviewing technique from several synced angles.

**Suggested demo scenario for V1:** a small event filmed by 3–5 friends (for example, a casual football game or a small performance). It is easy to film, involves consenting participants, and avoids music copyright issues.

---

## 3. What Version 1 Should Achieve

These are **outcomes**, not implementation instructions. Each one lists what "done" roughly looks like.

| # | Outcome | Done looks like |
|---|---|---|
| 1 | **Ingest and normalize clips** | Any common phone video format goes in; consistent, playable clips and audio come out |
| 2 | **Shared clock (sync)** | Every clip with usable audio is placed on one timeline, with a confidence score; clips that can't be placed are reported with a reason |
| 3 | **Synced multi-angle viewer** | All clips play together; the viewer shows which camera was recording when |
| 4 | **Per-clip understanding** | Each clip produces time-stamped observations: actions, sounds, speech, on-screen text, appearance |
| 5 | **Cross-angle identity** | The same person or object seen by different phones gets one shared identity |
| 6 | **Event knowledge** | Observations are merged into events stored in a queryable structure |
| 7 | **Story with citations** | A readable timeline narrative where every statement links to a clip and time |
| 8 | **Disagreement detection** | Conflicting observations are surfaced, explained, and resolved (or marked unresolved) |
| 9 | **Director's cut** | An automatically edited video that picks a good angle for each moment, with a reason |
| 10 | **Ask about the event** | Natural-language questions answered from the event knowledge, with citations |

It's fine to build these in stages. Outcomes 1–3 alone already make a strong first demo.

---

## 4. Shared Vocabulary

| Term | Meaning |
|---|---|
| **Clip** | One input video from one device |
| **Master timeline** | The shared clock all clips are placed on |
| **Offset** | Where a clip starts on the master timeline |
| **Coverage** | The time range during which a clip was recording |
| **Observation** | One note produced by analyzing one clip, in that clip's local time (e.g., "person in red jumps at 01:29") |
| **Tracklet** | One object's movement within a single clip |
| **Entity** | A real-world person or object, possibly seen by several clips |
| **Event** | A confirmed happening on the master timeline, supported by one or more observations |
| **Conflict** | Observations that describe the same moment differently |
| **Confidence** | How much the system trusts a result; always shown, never hidden |

---

## 5. Architecture Shape (Suggested)

The system works best as a **pipeline of independent stages** that communicate through simple, inspectable files. This keeps each stage testable and replaceable.

```
 raw videos
     │
     ▼
 ① Ingest & normalize ──► clean clips + audio + manifest
     │
     ▼
 ② Sync ────────────────► master timeline (offsets + confidence)
     │
     ├──► ③ Viewer (synced playback)
     │
     ▼
 ④ Per-clip understanding ──► observations per clip (local time)
     │
     ▼
 ⑤ Identity matching ──► entities across clips
     │
     ▼
 ⑥ Event fusion ──► events + conflicts (master time)
     │
     ▼
 ⑦ Knowledge store ──► queryable event graph
     │
     ├──► ⑧ Story + disagreements
     ├──► ⑨ Director's cut
     └──► ⑩ Q&A
```

**Suggested intermediate artifacts** (names and shapes can evolve):

- `manifest` — list of clips with basic metadata
- `timeline` — offset, duration, and confidence per clip, plus the pair measurements used
- `observations/<clip>` — observations in local time
- `entities`, `events` — merged knowledge in master time
- `story`, `cut` — narrative and edit decisions

Design these contracts thoughtfully; they matter more than any single model choice.

---

## 6. What We Already Know

These learnings come from early prototyping and research during planning. Treat them as useful evidence, not as final answers.

### Audio sync
- Sync does **not** need speech recognition. It compares the **pattern of sound over time**: music, claps, cheers, traffic, and even unintelligible talking all help.
- **GCC-PHAT** (generalized cross-correlation with phase transform) worked well on synthetic tests: roughly **10 ms average error** across 5 simulated phones with different microphones, noise, and echo.
- A **softer whitening (PHAT-β ≈ 0.8)** was more robust than full PHAT in experiments.
- A **peak-confidence score** separated real matches from unrelated audio clearly on synthetic data (unrelated audio stayed low, real matches scored far higher). Thresholds should be re-checked on real footage.
- **Short overlaps (under ~5 s)** produced unreliable matches. Low confidence caught them.
- Measuring **all clip pairs**, then solving for offsets with **weighted least squares** and **dropping inconsistent pairs**, worked well. It also links clips that never overlap directly (A↔B and B↔C place A and C).
- **Container and codec details matter**: an MKV file introduced a hidden ~23 ms audio delay (AAC encoder priming). Normalizing clips and checking stream start times is important.
- **Heavy echo** caused 10–25 ms bias on some pairs.
- **Sound travels ~343 m/s**: a phone far from the source hears events later (~3 ms per metre). This creates small audio-vs-video misalignment and could also be used to estimate relative distance.
- Clips **without audio** cannot be synced this way and need another method.

### Using a vision-language model (e.g., Gemini)
- Whole videos (with audio) can be sent directly; the model understands picture and sound together.
- Video is sampled at roughly **1 frame per second** by default, so fast actions may be missed.
- Timestamps come back at **second-level precision**, so precise sync must stay in Layer 1.
- Sending video is far cheaper per frame than sending extracted images.
- Rough cost estimate (Sept 2026 pricing, verify): **about $0.10 per event** of 5 clips × 30 s on a Flash-tier model. Output and "thinking" tokens are the main cost driver, not the video.
- Free tiers may use submitted content to improve the provider's products. Use consenting footage and consider face blurring before upload.
- A VLM is excellent for **describing, explaining, and verifying**, but should **not be the primary cross-clip matcher** at scale: it is slower, costlier, and less consistent than embedding-based matching.

### Identity across angles
- Reliable matching combines **appearance** (re-identification embeddings), **timing**, and **motion/position** cues rather than appearance alone.
- A useful pattern: embeddings and filters narrow many candidates down to a few; a VLM double-checks and explains the top candidates.

---

## 7. Suggested Tech Stack (Starting Point)

Choose freely if something better fits. Verify current versions.

| Area | Current leaning | Also worth considering |
|---|---|---|
| Language | Python | — |
| Environment | `uv` | pip, conda |
| Video/audio handling | FFmpeg, NumPy, SciPy | PyAV, librosa |
| Detection and tracking | Ultralytics YOLO (latest) | SAM 3 for text-prompted segmentation/tracking |
| Re-identification | BoxMOT or similar | CLIP/SigLIP crop embeddings |
| Clip understanding | Gemini (Flash tier) | Qwen3-VL locally, Claude |
| Speech | Gemini | faster-whisper for word-level timing |
| Sound events | Gemini | PANNs, BEATs |
| Knowledge store | Neo4j | Lighter embedded graph or relational options for V1 simplicity |
| Reasoning/narration | Gemini or Claude | Any model behind a common interface |
| Backend | FastAPI | — |
| Frontend | Plain HTML/JS first | React, Svelte; Cytoscape.js or similar for graphs |
| Quality | pytest, ruff, GitHub Actions | — |

---

## 8. Hardware and Environment

| Machine | Role | Notes |
|---|---|---|
| **MacBook (Apple M3, 8 GB)** | Development, ingest, sync, viewer, API calls, demos | Limited memory; avoid heavy local models |
| **Acer Predator (NVIDIA GPU)** | Detection, tracking, re-identification, local models, graph database | Check VRAM with `nvidia-smi`; likely via WSL2 + CUDA |
| **Cloud APIs** | Clip understanding, narration | Keys stored in `.env`, never committed |

- Early stages (ingest, sync, viewer) should run on CPU alone.
- GPU-heavy stages should degrade gracefully (smaller models, or cloud fallback).
- Code lives in GitHub; large video files do not.

---

## 9. Principles (Firm)

These are the few non-negotiable rules.

1. **Never modify original footage.** Work on copies; keep results reproducible.
2. **Every claim is traceable.** Anything the system states about the event links back to a clip and a time.
3. **Show uncertainty honestly.** Always expose confidence. "Unknown" and "not synced" are valid answers; never force a match.
4. **Stay model-agnostic.** AI providers sit behind a simple interface so they can be swapped.
5. **Respect privacy.** Use consenting or public footage. Offer face blurring before cloud processing. Link appearances across clips without identifying real people.
6. **Measure everything.** Each stage has a way to evaluate its quality.
7. **Be cost-aware.** Cache results; never analyze the same clip twice unnecessarily.
8. **Keep secrets out of the repo.**

---

## 10. Evaluation Ideas

- **Sync accuracy**: millisecond error against ground truth (synthetic clips with known offsets; real clips with a clap or flash as a reference).
- **Sync robustness**: correct rejection of unrelated clips; behavior with noise, echo, repetitive music, and short overlaps.
- **Identity matching**: accuracy against a small hand-labeled set.
- **Event recall**: how many hand-labeled moments the system finds.
- **Faithfulness**: whether narrative statements are actually supported by their cited clips.
- **Cost and speed**: API cost and processing time per event.

A small, well-labeled test event is more valuable than a large unlabeled one.

---

## 11. Roadmap (Flexible)

| Phase | Focus | Outcome |
|---|---|---|
| **0. Setup** | Repo, environments, test footage | Both machines ready; a small filmed test event |
| **1. Alignment** | Ingest, sync, viewer | Clips play perfectly together (first public demo) |
| **2. Understanding** | Per-clip observations, identity | Structured notes per clip, shared identities |
| **3. Knowledge** | Event fusion, knowledge store, conflicts | Queryable event graph |
| **4. Story and polish** | Narration, director's cut, Q&A, UI | End-to-end demo, README, demo video |

Order and scope can change as we learn.

---

## 12. Open Questions (Please Explore)

These are genuinely unresolved. Research, experiment, and propose.

**Sync**
- What works best for music-heavy or rhythmically repetitive events: raw waveform correlation, onset envelopes, spectral features, or a combination with voting?
- How should clock drift in long recordings be handled?
- Should acoustic delay (distance from the sound source) be corrected, and how could distance be estimated without ground truth?
- How can clips with no usable audio still be placed (for example, shared visual events like flashes)?

**Understanding and identity**
- What is the best balance between local models and a cloud VLM for per-clip understanding?
- How can a VLM's second-level timestamps be refined using local detection and tracking?
- How should identity matching handle similar outfits, occlusion, and very different viewpoints?

**Knowledge**
- How do we decide that observations from different clips describe the *same* event?
- What is the simplest knowledge store that still enables rich questions for V1?
- How should conflicts be classified and resolved (visibility, interpretation, audio-visual mismatch)?

**Output and experience**
- What makes a good automatic angle choice (sharpness, stability, subject size, audio quality, variety, pacing)?
- How can many videos play smoothly in sync inside a browser?
- How should confidence and conflicts be shown so they help rather than overwhelm?

**Beyond the basics (ideas welcome)**
- A map showing where each camera probably was
- Personal edits ("my view plus the best moments")
- Highlight reels generated from event importance
- Multilingual narration
- Near-real-time mode during a live event

---

## 13. Out of Scope for V1 (Parked for V2)

Recorded here so the design stays open to them later:

- **Silent CCTV footage**, synced using visual signals (brightness changes, motion, on-screen clock text) instead of audio.
- **Unreliable camera clocks**, including drift over long periods.
- **Non-overlapping camera networks**, where people move from one camera's view to another's, requiring route and travel-time reasoning.
- **Professional investigative use**, which would need evidence integrity, audit logs, human review, and legal review.

**Implication for V1:** keep the sync solver modular so that other offset estimators (visual, text, external logs) could plug in later.

---

## 14. UI Vision

A design preview exists (`scenefold-ui-preview.html`). Key ideas:

- A large **director's cut player** that explains why each angle was chosen.
- A strip of **all angles**, showing when each phone was not recording.
- A **timeline** with key moments, the final cut, and one coverage lane per camera.
- A **"What happened"** story where each moment lists the cameras that saw it.
- A **disagreement card** comparing what each camera suggests, with a resolution and confidence.
- An **ask box** whose answers link to clips and times.
- An **event graph** and a **sync report**.
- Each camera has its **own color**, used consistently everywhere, so viewers always know where information came from.

The preview is a direction, not a fixed design.

---

## 15. Portfolio Goals

This project is also a portfolio piece. Aim for:

- A short **demo video or GIF** at the top of the README
- A clear **architecture diagram**
- **Real metrics** (sync error, matching accuracy, cost per event)
- A **limitations** section and a **responsible use** section
- **Progress posts** at milestones (the synced viewer is the first natural one)
- Clean, well-tested code that others can run

---

## 16. Definition of Success for V1

Someone unfamiliar with the project can:

1. drop in a handful of phone videos of the same event,
2. watch them play together in sync,
3. read a short, cited account of what happened,
4. see where the videos disagree and why,
5. watch an automatically edited version,

and come away thinking: *"I understand this event better than any single video could show me."*
