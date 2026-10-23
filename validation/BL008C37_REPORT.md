# BL-008C.37: false swing rejection fixed, experimental flow validated (local)

Branch `integration/bl008c19-manual-swing-review`, base 2ba4200 (= production
`exp/streamlit-cloud-compat`, verified with `git ls-remote`). Nothing pushed or
deployed. No production credits used. No footage committed: the real-footage
landmark fixtures live outside this public repo.

## 1. Root cause (confirmed on the owner's own clip)

Reported case: production clip with 115 decoded frames at 30 fps, selection
frames 6-60 (0.20-2.00 s), rejected as "more than one distinct movement".
The clip is `MarioTSwing.mov` (the local decode gives 116 frames; the same
result either way). Frame review: stance 0-30, load and stride ~33-40, swing
~40-50, follow-through to ~60, then he walks away.

How the old check got there (`engine_v2/user_selection_validation.py`, called
from `engine_v2/user_guided_flow.analyze_user_selection`):

1. It never looked at the selection on its own. It ran a whole-clip burst
   detector (`event_candidates.extract_event_candidates`) over a combined
   wrist + ankle + rotation signal, normalized by the whole clip's maximum.
2. The real swing (frames 40-49, the strongest motion in the clip) was split
   by the internal-dip rule into two 4-frame bursts, [40-43] and [45-49].
   Both were then **discarded as "shorter than 5 frames, likely noise"**.
3. What survived: a faint stance sway [0-23] (peak 0.125, about 18% of the
   swing) and the follow-through glued onto walking away ([51-58] + [66-85] +
   [87-115], merged by the 0.5 x median-width adjacency rule).
4. Two groups had a peak inside the selection (the tolerance even allowed
   peaks up to max(5, width/4) = 13 frames outside it), so the check returned
   MULTIPLE_MOVEMENTS, a **hard block that confirmation could not override**.

Not the cause (checked): marker values reach the check exactly (frames 6-60
seen in the server text, player and check input); no off-by-one; no stale
marker; timestamps correct (30.0 fps from real frame times). Tracking was 100%
on every frame of the clip, so occlusion or low confidence was not involved.

The same failure was already recorded in the BL-008C.16 decision matrix: one
swing with some extra lead-in was called "multiple" on IMG_8436, IMG_8605,
IMG_8607, IMG_8608 and on every IMG_9005 selection. The bug was systematic.

## 2. New check (`engine_v2/selection_structure.py`)

Looks only at the selected frames (plus 0.25 s on each side, used only to
detect a cut-off swing). Signal: **hand movement relative to the body**: the
midpoint of the two wrists, relative to the hip midpoint, in the batter's own
torso lengths, per second of real frame time. That removes camera panning and
walking drift, mostly cancels walking arm swing, and works for any distance,
resolution or frame rate. One-frame pose glitches (a wrist jumping more than
1 torso length from its neighbours) are dropped.

The motion is split into phases of activity (at least 25% of the selection's
peak). Pauses under 0.5 s are bridged, so load, stride, swing, follow-through
and bringing the bat back down form ONE phase even with several speed peaks.

| Decision | When | Effect |
|---|---|---|
| accepted | one main movement, tracked well, not cut off, at full-swing speed | confirm, then analyze |
| uncertain | possible second movement (pause 0.5-0.8 s), slower than a full-speed swing, no clear movement, cut off at a marker, tracking gap in the movement, partial tracking, no timestamps | **warning, the user may still confirm and analyze**; the result is labelled lower confidence |
| multiple (block) | two or more fast movements (each at least 60% of the fastest and at least 0.1 s long, both fully inside the selection) with a well-tracked break of at least 0.8 s | blocked; the page lists each movement with its times and offers one-click "Use movement N" buttons |
| unusable (block) | body tracked in under 50% of the selection, or hands never tracked | blocked as `poor_quality` (same 50% floor the analysis already had) |

Why each number: a hitter can't reload and start a second swing within 0.5 s
(IMG_8601's bat recovery comes back 0.37 s after the follow-through); real
repeated swings in this set are about 3.5 s apart, so 0.8 s leaves margin on
both sides. Full-speed phone swings here peak at 6.2-10.6 torso lengths/s;
walking away and bat resets peak at 3.3-4.2; so 5.0 separates "full-speed
swing" from "can't tell" (a warning only, because slow-motion video looks
slow too). Without real timestamps a break is measured as if the clip were
120 fps, so a high-frame-rate clip can't be mistaken for two swings.

It does not identify a baseball swing, detect contact, or score quality. The
user's explicit confirmation is still required for every analysis.

## 3. Real-footage results (old check vs new)

Labels: one reviewer's frame-by-frame visual review of contact sheets (an AI
reviewer, not the owner). **Owner review requested**, especially for
IMG_8605 (the old annotation said two swings; this review sees one swing
plus a stance adjustment) and sample_swing.mp4 (no swing at all).

| Clip | Frames | What it is | Old | New | Reasons | Peak (torso/s) |
|---|---|---|---|---|---|---|
| MarioTSwing | 6-60 | **owner's exact production selection** | MULTIPLE (block) | accepted | one_movement | 8.27 |
| MarioTSwing | 0-115 | swing, then walking away | MULTIPLE (block) | accepted | one_movement | 8.49 |
| MarioTSwing | 30-44 | partial: ends mid-swing | insufficient | uncertain | edge_cut_end | 8.5 |
| MarioTSwing | 0-25 | stance only | single | uncertain | no_clear_movement | 1.17 |
| MarioTSwing | 65-115 | walking away only | single | uncertain | slower_than_full_speed | 4.18 |
| IMG_9005 | 45-90 | one swing | single | accepted | one_movement | 8.85 |
| IMG_9005 | 0-102 | whole clip, one swing | MULTIPLE (block) | uncertain | tracking_gap_in_movement | 9.2 |
| IMG_9005 | 20-60 | ends as the swing starts | MULTIPLE (block) | uncertain | edge_cut_end | 5.69 |
| IMG_8605 | 90-135 | one swing | MULTIPLE (block) | accepted | one_movement | 10.12 |
| IMG_8605 | 60-145 | one swing, generous padding | MULTIPLE (block) | accepted | one_movement | 9.93 |
| IMG_8605 | 140-165 | walking and turning after | single | uncertain | slower_than_full_speed | 3.3 |
| IMG_8608 | 28-74 | one swing | single | accepted | one_movement | 8.35 |
| IMG_8608 | 0-79 | whole clip, one swing | MULTIPLE (block) | accepted | one_movement | 8.4 |
| IMG_8608 | 28-44 | ends before the swing | ambiguous | uncertain | no_clear_movement | 2.01 |
| IMG_2271 | 35-95 | one swing, front view, 540x960 | MULTIPLE (block) | accepted | one_movement | 9.19 |
| IMG_8436 | 48-75 | one swing | single | accepted | one_movement | 9.04 |
| IMG_8436 | 18-75 | one swing, early start | MULTIPLE (block) | accepted | one_movement | 8.8 |
| IMG_8436 | 48-103 | one swing, late end | single | accepted | one_movement | 9.05 |
| IMG_8601 | 60-135 | swing + bat brought back down | single | accepted | one_movement | 10.37 |
| IMG_8601 | 60-105 | one swing, follow-through cut | single | uncertain | edge_cut_end | 10.63 |
| My_swing | 60-215 | **two real swings ~3.5 s apart** | MULTIPLE | **multiple (block)** | separate_fast_movements | 9.16 |
| My_swing | 0-452 | whole multi-swing session | MULTIPLE | multiple (block) | separate_fast_movements | 8.8 |
| My_swing | 120-430 | START inside a bat reset's tail | MULTIPLE | multiple (block) | separate_fast_movements | 6.77 |
| My_swing | 175-220 | one swing from the session | MULTIPLE (block) | accepted | one_movement | 6.25 |
| My_swing | 55-110 | one swing, then a fast bat reset | single | uncertain | edge_cut_end | 6.42 |
| sample_swing (54 fps) | 24-54 | no swing (stance, bat waggle) | single | uncertain | slower_than_full_speed | 3.44 |
| sample_swing (54 fps) | 0-322 | no swing | MULTIPLE (block) | uncertain | slower_than_full_speed | 3.87 |

Summary: legitimate single-swing selections blocked by the old check: 9.
Blocked by the new check: 0. Genuine multi-swing selections still blocked: 3
of 3. Selections without a real full-speed swing (stance, walking, a clip with
no swing) are no longer called "one clear movement"; they get an honest
warning. Corrupt clip: refused before the check with "We couldn't open this
video file" (`cannot_open`), nothing charged.

Broadcast MLB clips (informational, not used for thresholds): mostly slow-motion
replays and camera cuts, so speeds read 1-5 torso/s and nearly everything is
"uncertain". Kyle Tucker's old "three swings" labels look like slow-motion
replays of one swing on review; Bregman's labelled "running" interval reads as
one fast movement (accepted), which is the known limit: the check can't tell
running from a swing, the user's confirmation decides.

Synthetic clips (`tests/_synthetic_swings.py`, always run): one swing with two
speed peaks at 30 / 29.97 / 54 / 60 fps accepted; two swings 2.4 s apart
blocked, with each bracketed swing accepted (proves the selection, not the
clip, is read); 1.7 s apart uncertain; partial swing, stance only, tracking
dropouts, one-frame glitch and missing timestamps each handled.

## 4. Flow and page changes (`manual_swing_review.py`)

- Clear results: "Looks good: one continuous movement", "Not sure about this
  selection, you can still continue", or "Can't use this selection", each with
  the check's own plain reason and a recovery hint.
- Two-movement block: one-click "Use movement N (a s to b s)" buttons that move
  both markers (padded 0.5 s before, 0.4 s after, never into the neighbour).
- The confirm checkbox is keyed to the exact selection. Found while doing this
  work: a tick used to carry over to a changed selection.
- Uncertain-but-confirmed results show a "Lower confidence" warning.
- Markers set by the server and the fallback slider now land only on frames
  the player can show. Found in the browser: long clips (over 240 frames) have
  a preview for every 2nd frame, and the player showed frame 158 while the
  server checked 159.

Charging is untouched: only the confirmed "Analyze this selection" click can
charge; the check re-runs inside that click, so a blocked selection can never
be measured or charged; reruns can't charge.

## 5. Evidence

- Full suite: **909 passed, 6 skipped, 0 failed**.
- New: `tests/test_selection_structure_bl008c37.py` (synthetic + 27 real cases +
  owner case end to end + old-check root cause), `tests/test_manual_review_bl008c37.py`
  (page flow through AppTest with counted charges). The real-footage tests skip
  when `$BL_PRIVATE_FIXTURES` is absent.
- Real browser, local app + local fake Supabase (fake `@example.test` account):
  owner clip 6-60 accepted, confirmed, analyzed, exactly 1 charge, 0 more after
  reruns; markers moved: check, confirmation and result cleared; multi-swing
  clip blocked with picks; pick moved player and server to the same frames
  and re-checked as accepted; normal "Analyze swing" saved its report (1 swing
  record, 1 charge); logout then refresh stayed logged out; saved login
  survived a server restart.
- Check cost: 19 ms on the owner clip, 88-113 ms on 450-870-frame clips,
  under 1 MB extra. No decoding or pose work: it reuses the prepared landmarks.
- Original footage: SHA-256 of all 14 source files identical before and after.

## 6. Limitations

1. Two swings started within ~1.5 s of each other merge into one movement
   (pinned by `test_known_limitation_back_to_back_swings_merge`).
2. The check can't tell a swing from running, walking or a body turn. Fast
   non-swing motion can be "accepted"; slow motion gets a warning. The user
   confirms.
3. Thresholds come from 9 phone clips from one facility and one hitter group,
   reviewed by a single non-owner reviewer. Owner review of the labels is
   requested.
4. Slow-motion exports read as "slower than a full-speed swing" (warning).
5. The experimental metrics are unchanged: e.g. the owner clip reports
   "shoulder-width normalizer varied 9.0x" because the body turns during the
   swing, already shown as "degraded".
6. Not tested in production. The Supabase idempotency migration is still not
   applied, so exactly-once charging rests on the existing app-side nonce,
   as before.
7. Pre-existing and unchanged: the upload page writes a new temp copy of the
   upload on every rerun; the age-based sweep removes them.

## 7. Deploy and rollback

Deploy only with owner authorization: push the commit to
`exp/streamlit-cloud-compat`, then Reboot in the Streamlit dashboard (a push
alone has not redeployed this app). Verify: owner re-checks MarioTSwing 6-60
(expect "Looks good"), confirms nothing is charged until "Analyze this
selection". Rollback: a normal `git revert` of this commit, push, Reboot. No
schema, secret, dependency or packages.txt changes.
