# Related work: does this already exist?

Researched 2026-09-17 (three parallel web searches: products, research and open source, investigative
practice). "Not found" means not found in that search, not proof that nothing exists.

## Short answer

Pieces exist; the whole chain does not.

- **Audio sync** is solved and built into professional editors, but an editor still gathers the clips,
  checks the sync, and picks every angle by hand.
- **Automatic angle switching** exists for podcasts and interviews (it follows who is talking), not for
  events filmed by a crowd.
- **Crowd concert apps** were tried and stopped, or rely on human editors.
- **Investigations** that rebuild events from bystander videos are still largely manual and slow.
- **Cited multi-video stories** exist only as 2025–2026 research, and **flagging where cameras disagree**
  is the least covered area.
- **No product, paper, or repo found** chains sync → synced viewer → cross-camera understanding →
  cited story with disagreements → automatic cut, for phone videos of one event.

## Products

| Kind | Examples | What stays manual or limited |
|---|---|---|
| Editors with audio sync | Premiere Pro ([multicam](https://masv.io/blog/multicam-editing-premiere)), DaVinci Resolve 21 ([club](https://davinciresolveclub.com/davinci-resolve-multicam-editing/)), Final Cut Pro ([Apple](https://support.apple.com/guide/final-cut-pro/create-multicam-clips-ver23c764f1/mac)), Avid | One editor, one project; clips grouped and sync checked by hand; no understanding of content |
| Standalone sync | PluralEyes: limited maintenance 2023, retired because editors absorbed the feature ([Maxon](https://www.maxon.net/en/article/pluraleyes-to-enter-limited-maintenance-mode)) | Gone |
| AI angle switching | Resolve SmartSwitch, [AutoPod](https://www.autopod.fm/), [Descript](https://help.descript.com/hc/en-us/articles/28736507904525-Automatic-multicam), Riverside, [Eddie AI](https://www.heyeddie.ai/blog/eddie-v4) | Decides by who is speaking; built for talking-head footage |
| Crowd event video | Vyclone (shut 2016, [TechCrunch](https://techcrunch.com/2016/07/07/vyclone-hits-the-deadpool)); Snapchat Crowd Surf (2017 test, [MacRumors](https://www.macrumors.com/2017/08/15/snapchat-crowd-surf-feature-stitch-videos/)); YouTube multi-angle demo (2015); [Fanshot](https://fanshot.live/) (human editors, 2–3 weeks) | Discontinued, tests, or manual; wedding guest apps only collect uploads |
| Live multi-phone recording | Final Cut Camera Live Multicam, Blackmagic Camera, Switcher Studio, Mevo | Sync only because devices join before recording |
| Sports | Veo, Pixellot, Hudl, Trace | Their own fixed cameras; parents' phone clips not combined |
| Police evidence | Axon multicam (Axon cameras only, [docs](https://www.axon.com/help/axon-evidence/software/axon-evidence/evidence/review-evidence/multicam-playback.htm)); Amped FIVE (analyst lines up waveforms by hand); [Rigr AI](https://rigr.ai/articles/multi-camera-video-evidence/) (Aug 2026: auto sync of bodycam, CCTV, bystander video) | No disagreement detection documented |

## How investigators do it today

- NYT Las Vegas shooting: 30+ videos synced by the pattern of gunfire bursts, by hand
  ([CJR](https://www.cjr.org/hit_or_miss/nytimes-vegas-shooting-video.php)); NYT "Day of Rage" (Jan 6)
  took six months.
- Newsrooms line clips up on a Premiere or CapCut timeline by gunshot spikes
  ([Poynter 2026](https://www.poynter.org/reporting-editing/2026/bystander-surveillance-video-ai-visual-investigations-journalism/)).
- Forensic Architecture uses Praat/Reaper audio analysis and shadows as "physical clocks"; SITU builds
  3D models; HRW dates footage from shadows and metadata
  ([HRW](https://www.hrw.org/news/2024/07/17/time-will-tell-how-human-rights-watch-identifies-time-through-analyzing-videos)).
- Euromaidan (Carnegie Mellon): one analyst spent 8 months syncing part of the footage by hand; the
  algorithm then synced 4 h 16 min in a few days; ~20% of the corpus were duplicates
  ([CMU PDF](https://www.cmu.edu/chrs/publications/pdf/Aronson_Comp_Vision_and_ML.pdf)).
- Open tools stop short: SITU [Codec](https://github.com/SITU-Research/codec) and Forensic Architecture
  [Timemap](https://github.com/forensic-architecture/timemap) need times typed in by hand.
- Ethics raised: surveillance misuse, identifying people, re-victimization
  ([WITNESS](https://lab.witness.org/ethics-curating-citizen-video/)). Practitioners want humans to confirm
  every alignment, speed-of-sound correction, and gaps flagged instead of guessed.

## Research

- **Audio sync (solved):** Kennedy & Naaman 2009 (concert fingerprints); Kammerl et al. 2014 (Google),
  all pairs + global solve, the same structure as Scenefold
  ([PDF](https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/42193.pdf));
  Bano & Cavallaro 2015, 99.6% of clips synced, fingerprinting fails on short clips and echo
  ([PDF](https://sophiabano.github.io/allpublications/images/pdfs/2015-Elsevier-IS-Discovery-and-Organization.pdf)).
- **Visual sync when audio fails (active):** VisualSync, NeurIPS 2025, median error under 50 ms
  ([arXiv](https://arxiv.org/abs/2512.02017)); VideoSync 2025 ([arXiv](https://arxiv.org/abs/2506.15937)).
- **Automatic mashups (older, no public code found):** MoViMash 2012, Jiku Director 2013,
  Arev et al. SIGGRAPH 2014, MoVieUp 2015. Newer LLM editing: EditIQ 2025, DIRECT 2026.
- **Cross-video understanding (weak today):** best models ~50–63% versus humans ~90% on cross-video
  reasoning (CVBench 2025 [arXiv](https://arxiv.org/abs/2508.19542), CrossVid, SYNCR 2026); telling the
  same person apart across videos is a named failure.
- **Cited multi-video stories (new research):** WikiVideo 2025 ([arXiv](https://arxiv.org/abs/2504.00939),
  code MIT); TRACE 2026, citations per video but merges duplicates rather than resolving contradictions
  ([arXiv](https://arxiv.org/abs/2605.16740)); MAGMaR 2026 shared task.
- **Disagreements across videos (thinnest):** only benchmark questions (VNU-Bench 2026, CVBench).

## Open source worth knowing

| Repo | Use for Scenefold |
|---|---|
| [audalign](https://github.com/benfmiller/audalign) (MIT) | Sync accuracy baseline |
| [bbc/audio-offset-finder](https://github.com/bbc/audio-offset-finder) (Apache-2.0) | Sync accuracy baseline |
| [align-videos-by-sound](https://github.com/align-videos-by-sound/align-videos-by-sound) (no license, inactive since 2019) | Closest to sync + side-by-side player; don't copy |
| [visualsync](https://github.com/stevenlsw/visualsync) (no license) | Reference for syncing clips without audio (V2) |
| [WikiVideo](https://arxiv.org/abs/2504.00939) | Reference for Phase 7 cited stories |

## What this means for Scenefold

1. **Sync is the foundation, not the novelty.** Our method matches proven research, so measure it
   honestly against audalign / audio-offset-finder and publish the numbers. First numbers
   (2026-09-18, two Jiku subsets, see ROADMAP Phase 2): Scenefold median 2.4–4.5 ms, audio-offset-finder
   3.9–7.3 ms, audalign 10.8–14.6 ms; Scenefold also measures clock drift, which neither baseline does.
2. **The novel value is the combination and Layer 2:** a cited account of one event with visible
   disagreements, plus angle choice based on what happens (not who talks), for crowd phone footage.
3. **AI is weakest exactly there** (~50–63% on cross-video reasoning), which supports our principles:
   show confidence, allow "unknown", validate citations in code, keep a human able to check every claim.
4. **Crowd apps failed as businesses** (Vyclone, Crowd Surf), and big editors could add features.
   For a portfolio project the gap is clear; as a product it would need a distinct audience
   (e.g. amateur sports, weddings, verification).
5. **Responsible use** should cite the investigators' concerns: no facial identification, humans confirm,
   gaps flagged rather than guessed.
