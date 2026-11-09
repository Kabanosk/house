# Templates: demo video and pitch

Pick by `spec.deliverables.video_max_seconds`: up to ~60 s (or unknown) → the default 30–45 s promo; longer demo
videos required by the rules (e.g. Devpost, often up to 3 min) → the long demo below. Script it; do not improvise.
Record from the real app (`demo-flow.md`), never mock-ups presented as the product.

## Default: 30–45 s promo

**0:00–0:05 Hook.** The problem in one line, with the user it hurts, legible on screen by 0:03 (rule 1 below).
**0:05–0:25 The product doing it.** The wow moment from `demo-flow.md`, real screen, no slides. Captions on.
**0:25–0:35 Why it is different.** One line against the closest existing product from the prior-art card.
**0:35–0:45 Name and outcome.** Product name, the concrete outcome for the user, team/track.

## How to make it: video-director + Remotion

- **Direction:** `$HOUSE_ROOT/vendor/skills/video-director/SKILL.md` (treatment, `timeline.json`, QA scripts).
- **Build:** `~/.claude/skills/remotion-best-practices/SKILL.md` (Remotion; installed per machine by
  `house setup`; missing → run it). Put real app recordings in with `<OffthreadVideo>`, then add punch-ins, type
  and captions in code on top of them.
- **Files:** `pitch/TREATMENT.md`, `pitch/FACTS.md`, `pitch/video/` (Remotion project, `timeline.json`, `qa/`).

### House overrides (these win over both skills)

- **Autonomous by default.** Don't stop for treatment approval. Log every creative call as
  `DECISION: … because …` in `pitch/TREATMENT.md` and list them in the delivery report, so the human can overturn
  any of them at the briefing. In `copilot`, show the treatment first only if the human asked for it.
- **No Studio wait.** Never start Remotion Studio and wait for someone to look. Render stills at the beats
  (`npx remotion still`) and look at them yourself.
- **No TTS by default.** The film is silent and must be fully readable with the sound off. Add a voiceover only
  when the spec or the human asks for one, and never through a paid TTS key.

### Production rules

1. **Hook by 3 s, readable with the sound off.** Frame 0 is a finished composition. By 3.0 s the problem or the
   promise is on screen as large text.
2. **Type floors**, as rendered px at a 1080 short side:
   - punchlines 120–160 px, at most 6 words;
   - captions ≥ 48 px;
   - nothing meant to be read below 28 px.

   UI text that is too small to read gets a punch-in or a caption.
3. **Pacing.**
   - No shot runs longer than 5 s without new information. Never hold a dashboard for 12 s.
   - Text holds at least 1.0 s + 0.3 s per word, minimum 1.5 s; `check_timeline.py` enforces this.
4. **Punch-ins of 1.6–2.5×** onto the element that changes (a click, a result, a number). The camera settles
   before the click.
5. **One hero moment at 60–80% of the runtime**, doing something only video can do (e.g. zoom out from one
   session to a wall of all sessions). Don't spend it early.
6. **One product and one story** from the first frame to the last; never switch to another domain or user
   mid-film. Required sponsor tech is large and named in frame (logo and name), not a corner badge.
7. **Truth.**
   - Every number on screen comes from a row in `pitch/FACTS.md` (claim, source, date).
   - Illustrative data says `Example` on the graphic itself.
   - No inflated counts.
8. **Capture the real app at 2×** so punch-ins stay sharp: Xvfb + ffmpeg `x11grab`, with the app at 1280×720
   and a device scale factor of 2 (2560×1440 frames). Check the size with `ffprobe`.
   ```bash
   Xvfb :99 -screen 0 2560x1440x24 &
   DISPLAY=:99 chromium --force-device-scale-factor=2 --window-size=1280,720 --app=http://localhost:3000 &
   ffmpeg -f x11grab -framerate 60 -video_size 2560x1440 -i :99 -c:v libx264 -crf 14 pitch/video/public/capture.mp4
   ```
9. **Transitions need a reason you can say aloud** (next step, match on position, scale through). No glow,
   particles, lens flares, bouncy easing or chains of crossfades.
10. **Sound is optional.** If music is used, master it at -14 LUFS integrated with true peak ≤ -1 dBTP, and don't
    put a sound on every cut.
11. **Formats.**
    - Master in 16:9 at 1920×1080.
    - When the contest or socials want a vertical version, recompose 9:16 at 1080×1920 from the same timeline
      with its own layout. Never crop it out of the 16:9 master.
12. **QA gate before submission.** All of these must pass:
    - `check_timeline.py` reports 0 errors before building.
    - After the render, `qa_video.py` reports no FAIL (or explains each one):
      `uv run --with numpy --with pillow python $HOUSE_ROOT/vendor/skills/video-director/scripts/qa_video.py
      pitch/video/out/promo.mp4 --out pitch/video/qa --beats pitch/video/timeline.json`.
    - You have looked at `hook_sheet.jpg` and `phone_sheet.jpg`.
    - A clean-context reviewer (judge) restates the message from the frames alone.

    Put the QA report path in `PROGRESS.md`.

## Long demo video (~3 min, only when the rules ask for it)

Jurors may stop watching after three minutes: the value must be clear in the first 20 s. The same shape also
works for a live pitch slot of about 3 minutes.

**0:00–0:20 Problem.** "Today, [user] struggles with [specific problem]. Existing solutions require [pain]. We built
[product] to make [outcome] possible."

**0:20–0:40 Product promise.** "In one sentence, [product] does X by using Y. Here is the entire flow."

**0:40–1:50 Live product demo.** The shortest complete journey from `demo-flow.md`. No architecture slides here.
Make the differentiating feature visibly happen.

**1:50–2:20 Technical depth.** "Under the hood, [architecture]. The hard part was [problem], solved with
[specific implementation]." One diagram, metric or meaningful artifact.

**2:20–2:40 Innovation / feasibility.** "Unlike [closest existing product from the prior-art card], we [difference].
It extends to [credible next use]."

**2:40–3:00 Impact and close.** "For [user], this means [concrete outcome]. In [time] we built a working prototype
that demonstrates [core claim]."
