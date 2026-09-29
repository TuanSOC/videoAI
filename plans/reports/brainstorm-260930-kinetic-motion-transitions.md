# Brainstorm 260930 — Kinetic Camera Motion & Smart Transitions (Studio-Grade Retention)

## 1. Problem & Context
The current pipeline produces technically solid videos (343 tests passing, SFX engine, document card highlighter, karaoke subtitles). However, from the perspective of a demanding viewer on TikTok/YouTube Shorts, the visual delivery remains somewhat predictable and flat:
1. **Audio-Visual Mismatch**: A fast, airy SFX `whoosh` is accompanied by a slow, soft 0.18s `fade` (dissolve). Hearing a high-speed whoosh while seeing a gentle cross-dissolve feels jarring and weak.
2. **Dead Frames on Static Stock**: Stock video clips on tripods have `motion: "none"`, staying completely frozen for 3.5 seconds.
3. **Linear Camera Curves**: Still images zoom at constant velocity without easing, reading as automated/robotic.
4. **Zero Transition Variety**: All 10-15 scenes in a video use the exact same dissolve transition.

## 2. Technical Validation (Verified on FFmpeg 8.1.1 Full Build)
We ran live FFmpeg verification scripts on this machine and confirmed:
- **`xfade` filter**: Supports 57 transitions natively with NVENC hardware encoding.
  - `smoothleft` / `smoothright` / `wipetl`: Perfect physical match for `whoosh`.
  - `zoomin` / `fadewhite` / `fadeblack`: High-impact punctuation for `impact` and dramatic plot twists.
  - `slideup`: Natural pairing for document evidence cards (`_doc.mp4`).
  - `dissolve`: Soft cut for contemplative / expository scenes.
- **Dynamic Camera Drift via Oversized Crop**:
  - `crop=w=1080:h=1920:x=(in_w-1080)*(0.5+0.15*prog):y=(in_h-1920)*(0.5+0.1*prog)` allows sub-pixel camera drifting across static video footage without changing frame dimensions or causing encoder resets.
- **S-Curve Ease-in-out**:
  - FFmpeg math evaluator natively supports `prog = (1 - cos(PI * t / span)) / 2`, eliminating mechanical linear zoom.

## 3. Architecture Blueprint

### Component 1: Contextual Transition Engine (`src/vidgen/assemble/transitions.py`)
- Define transition rules mapping context to FFmpeg `xfade`:
  ```python
  def select_transition(prev_shot: Shot, cur_shot: Shot, cue: Cue | None, last_transition: str) -> str:
      if cur_shot.src_type == "document":
          return "slideup"
      if cue and cue.kind == "whoosh":
          return random.choice(["smoothleft", "smoothright", "wipetl"])
      if cue and cue.kind == "impact":
          return "zoomin" if cur_shot.kind == "image" else "fadeblack"
      if prev_shot.duration < 1.4:
          return "hard_cut"
      # Default: avoid repeating the same transition
      candidates = ["dissolve", "smoothleft", "wipetr"]
      return next(t for t in candidates if t != last_transition)
  ```

### Component 2: Subtle Drift for Static Video (`src/vidgen/assemble/clips.py`)
- Add an oversized drift mode (scale to 1.06x, animate crop offset `x`/`y` by 2-4% over the shot span) for video clips with low intrinsic motion.

### Component 3: Ease-in-out Ken Burns (`src/vidgen/assemble/clips.py`)
- Replace linear progression `on / span` with cosine ease `(1 - cos(3.14159 * (on + t0) / span)) / 2`.

## 4. Implementation Phasing
- **Phase 1**: Transition Engine (`transitions.py`) + integration into `shot_args()` in `clips.py`.
- **Phase 2**: Eased Ken Burns curves + Subtle Drift for static video stock.
- **Phase 3**: Flash/White shutter transition on `impact` cues.

## 5. Verification & Acceptance Criteria
- 100% green test suite (343+ tests).
- Short 60s render stays under 4 minutes on RTX 4060 Laptop NVENC.
- `sfx.json` cues align with visual transitions (whoosh coincides with directional wipes/slides).
