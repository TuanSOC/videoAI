# Brainstorm 260930 — Pipeline reliability (no stuck, no races) + review fixes

## Problem
/code-review (19 commits) found 10 issues; user wants a clear pipeline that never gets stuck.
Root cause of the worst ones: folder ownership = job.json (a status file) + an in-memory studio lock.
- killed/Ctrl-C series → job.json stays "running" → every later run refuses the folder;
- studio never sees a CLI/series job; on startup it marks it interrupted and Resume starts a 2nd pipeline;
- set_look read-modify-writes state.json beside a running pipeline;
- deferred AI drawing writes scene_NNN.<ext> over the stock file hardlinked from cache/files → cache poisoned
  (only when extensions match: Cloudflare .jpg over a stock .jpg; ComfyUI .png never matched a stock file);
- text-subject filter blocks "no visible text", "text-free", "typing on laptop";
- check_facts raises on a bad script.json; strip_meta can empty a query / cut "concept car"; rewrite path skips
  strip_meta/plain; an impact at a cut losing to a whoosh (or on a card) gets no visual accent; series status
  logic duplicated 3×; ComfyUI image timeout 1800 s (a hung ComfyUI stalls 30 min per scene).

## Decision (approved)
- A: OS file lock per video folder (`.vidgen.lock`; msvcrt on Windows, fcntl on POSIX) — released by the OS on
  exit/kill. Every pipeline run (studio jobs, `vidgen resume`, `vidgen series`) holds it; busy = lock held;
  studio marks a job interrupted only if nobody holds the lock; set_look refuses while held (409).
  job.json stays the status display only.
- B: drawn stills get their own name `scene_NNN_ai.*`.
- C: NO_TEXT handles visible/readable/any + "text-free"/"without writing"; drop "typing on", "books".
- D: check_facts reads the script inside the try.
- E: strip_meta only metaphor/symbolic words; empty → alts/previous; rewrite_one applies strip_meta + plain.
- F: impacts beat whooshes at a cut; impacts not used by a transition get the punch zoom.
- G: one series status function used by CLI dry-run and run().
- H: ComfyUI image timeout 180 s (video keeps 1800 s).

## Verification
Unit tests per item; live: Ctrl-C mid `vidgen series` then rerun resumes; studio started during a series run
shows it running and refuses a second job; cache scan for poisoned files.
