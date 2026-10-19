"""Full-page Training Plan v3.1 preview — hero + level card + stats +
motivation chips + consistency strip + drill cards (with action row)
+ coach notes + re-test reminder.

The live authed Streamlit page can't be screenshot'd without a Supabase
login, so this harness composes a faithful static HTML version of the
authed has-data path by:

  1. Importing development_tracker under a Streamlit stub (so
     `_DT_LOCAL_CSS` is read directly from the module — single source
     of truth).
  2. Calling the same helper functions the live page calls
     (`_hero_metrics`, `_build_data_hero_html`, `_build_consistency_html`)
     with realistic fake data.
  3. Hand-writing the *static* HTML versions of the level card, stat
     strip, motivation chips, drill cards, and action row in the
     EXACT same DOM shape that `_render_level_card` /
     `_render_stat_strip` / the drill loop emit — but with bare-HTML
     standins for the Streamlit widgets (a `<label class="tp-fake-check">`
     for the checkbox, a `<input>` for the reps field). That way the CSS
     overrides in `_DT_LOCAL_CSS` light up correctly without needing a
     live Streamlit instance.

Usage:
    PY=/Users/logancollins/barrellabs-swing-app/.venv/bin/python
    $PY scripts/visual_qa/preview_training_plan_full.py

Output: /tmp/training_plan_full_desktop.png + _mobile.png
"""
from __future__ import annotations
import sys
import types
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT))


def _streamlit_stub() -> types.ModuleType:
    """Minimum Streamlit stub the dev_tracker module needs at import."""
    stub = types.ModuleType("streamlit")
    stub.session_state = {}

    def _passthrough(*dargs, **dkwargs):
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]

        def inner(fn):
            return fn

        return inner

    def _noop(*a, **k):
        return None

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    stub.cache_resource = _passthrough
    stub.cache_data = _passthrough
    for name in ("markdown", "write", "error", "warning", "caption",
                 "rerun", "stop", "toast", "success", "info", "image",
                 "code", "header", "subheader", "title", "divider"):
        setattr(stub, name, _noop)
    stub.container = lambda *a, **k: _Ctx()
    stub.columns = lambda n, **k: [_Ctx() for _ in range(
        n if isinstance(n, int) else len(n))]
    stub.expander = lambda *a, **k: _Ctx()
    stub.spinner = lambda *a, **k: _Ctx()
    stub.form = lambda *a, **k: _Ctx()
    stub.form_submit_button = lambda *a, **k: False
    stub.button = lambda *a, **k: False
    stub.checkbox = lambda *a, **k: False
    stub.text_input = lambda *a, **k: ""
    stub.text_area = lambda *a, **k: ""
    stub.number_input = lambda *a, **k: 0
    stub.selectbox = lambda *a, **k: ""
    stub.radio = lambda *a, **k: ""
    stub.download_button = lambda *a, **k: None
    stub.file_uploader = lambda *a, **k: None
    stub.query_params = {}

    _c1 = types.ModuleType("streamlit.components.v1")
    _c1.html = _noop
    _c1.iframe = _noop
    _c0 = types.ModuleType("streamlit.components")
    _c0.v1 = _c1
    stub.components = _c0
    sys.modules["streamlit.components"] = _c0
    sys.modules["streamlit.components.v1"] = _c1
    return stub


# ---------- Static HTML stand-ins for live-Streamlit blocks ----------
def _level_card_html() -> str:
    """Same DOM shape as `_render_level_card` emits."""
    return """
<div class="dt-level-card">
  <div class="dt-level-row">
    <div style="flex:1;min-width:0;">
      <div class="dt-level-eyebrow">Current Level</div>
      <div class="dt-level-name">All-Star</div>
      <div class="dt-level-tagline">Locked in</div>
    </div>
    <div class="dt-xp-pill">
      <span class="dt-xp-num">1,100</span>&nbsp;XP&nbsp;Total
    </div>
  </div>
  <div class="dt-xp-bar-wrap">
    <div class="dt-xp-bar"><div class="dt-xp-bar-fill" style="width:6%"></div></div>
    <div class="dt-xp-bar-foot">
      <span>Level Progress · 6%</span>
      <span class="dt-xp-foot-next">1,400 XP to MVP</span>
    </div>
  </div>
</div>
"""


def _stat_strip_html() -> str:
    return """
<div class="dt-stat-strip">
  <div class="dt-stat-pod">
    <div class="dt-stat-pod-num is-red">0</div>
    <div class="dt-stat-pod-label">Day Streak</div>
  </div>
  <div class="dt-stat-pod">
    <div class="dt-stat-pod-num">1</div>
    <div class="dt-stat-pod-label">Longest Streak</div>
  </div>
  <div class="dt-stat-pod">
    <div class="dt-stat-pod-num">7</div>
    <div class="dt-stat-pod-label">Swings Logged</div>
  </div>
  <div class="dt-stat-pod">
    <div class="dt-stat-pod-num">0</div>
    <div class="dt-stat-pod-label">Drills Done</div>
  </div>
  <div class="dt-stat-pod">
    <div class="dt-stat-pod-num">81</div>
    <div class="dt-stat-pod-label">Best Score</div>
  </div>
</div>
"""


def _motivation_strip_html() -> str:
    return """
<div class="dt-motivate-strip">
  <div class="dt-motivate-chip is-red">1 drill away from unlocking First Reps.</div>
  <div class="dt-motivate-chip">6 days until your Silver Streak Badge.</div>
  <div class="dt-motivate-chip">Only 1,400 XP until MVP.</div>
</div>
"""


def _category_header_html(priority: int, title: str, n_drills: int) -> str:
    return f"""
<div class="dt-cat-header">
  <span class="dt-cat-priority-pill">Priority {priority}</span>
  <span class="dt-cat-title">{title}</span>
  <span class="dt-cat-count">{n_drills} DRILL{'S' if n_drills != 1 else ''}</span>
</div>
"""


def _coach_note_html(body: str) -> str:
    return f"""
<div class="dt-coach">
  <div class="dt-coach-eyebrow">Coach Notes</div>
  <div class="dt-coach-body">{body}</div>
</div>
"""


def _howto_html(instr: dict) -> str:
    """Static `<details>` block — same DOM as the live page emits."""
    def _li(items):
        return "".join(f"<li>{i}</li>" for i in (items or []))

    return f"""
<details class="tp-howto" open>
  <summary>
    <span class="tp-howto-label">How to Perform This Drill</span>
    <span class="tp-howto-chev">›</span>
  </summary>
  <div class="tp-howto-body">
    <div class="tp-howto-block">
      <div class="tp-howto-eyebrow">Setup</div>
      <ul class="tp-howto-list">{_li(instr.get("setup", []))}</ul>
    </div>
    <div class="tp-howto-block">
      <div class="tp-howto-eyebrow">Execution</div>
      <ol class="tp-howto-list is-ordered">{_li(instr.get("execution", []))}</ol>
    </div>
    <div class="tp-howto-block">
      <div class="tp-howto-eyebrow">Focus Points</div>
      <ul class="tp-howto-list">{_li(instr.get("focus_points", []))}</ul>
    </div>
    <div class="tp-howto-block">
      <div class="tp-howto-eyebrow is-red">Common Mistakes</div>
      <ul class="tp-howto-list is-mistakes">{_li(instr.get("common_mistakes", []))}</ul>
    </div>
    <div class="tp-howto-block">
      <div class="tp-howto-eyebrow is-gold">Success Feels Like</div>
      <div class="tp-howto-success">{instr.get("success_feels_like", "")}</div>
    </div>
    <div class="tp-howto-block">
      <div class="tp-howto-eyebrow">Video</div>
      <div class="tp-howto-video">
        <div class="tp-howto-video-thumb">▶</div>
        <div class="tp-howto-video-caption">Watch Coach Demo</div>
      </div>
    </div>
  </div>
</details>
"""


def _drill_card_html(*, num: str, name: str, role: str, role_cls: str,
                     reps: str, how: str, done: bool, mastery: int = 0,
                     instr: dict | None = None, cat_title: str = "") -> str:
    status = (f'✓ COMPLETED · 14:23' if done else '▸ READY')
    done_cls = "is-done" if done else ""
    role_chip = f'<span class="dt-role {role_cls}">{role}</span>'
    mastery_chip = (f'<span class="tp-mastery">Mastered {mastery}×</span>'
                    if mastery >= 3 else '')
    reps_chip = f'<span class="dt-drill-reps">SUGGESTED · {reps}</span>'
    instr = instr or {}

    meta_strip = (
        f'<div class="tp-drill-meta-strip">'
        f'<span class="tp-meta-item"><span class="ico">◷</span>{instr.get("estimated_time", "")}</span>'
        f'<span class="tp-meta-item"><span class="ico">⚙</span>{instr.get("equipment", "")}</span>'
        f'<span class="tp-meta-item"><span class="ico">▲</span>{instr.get("difficulty", "")}</span>'
        f'<span class="tp-meta-item"><span class="ico">◎</span>{cat_title}</span>'
        f'</div>'
    )
    description_html = f'<div class="dt-drill-how">{how}</div>'
    howto = _howto_html(instr)

    card = f"""
<div class="dt-drill {done_cls}">
  <div class="dt-drill-row">
    <div class="dt-drill-num">{num}</div>
    <div class="dt-drill-meta">
      <div class="dt-drill-name">{name}{role_chip}{mastery_chip}</div>
      {reps_chip}
    </div>
    <div class="dt-drill-status-pill">{status}</div>
  </div>
  {meta_strip}
  {description_html}
  {howto}
</div>
"""
    # Static action row matching the live `st_key-tp_action_*` DOM.
    reps_value = "3×10" if done else ""
    if done:
        actions = f"""
<div class="st-key-tp_action_01">
  <div data-testid="stTextInput" style="margin-bottom:10px;">
    <label>Reps completed</label>
    <div data-baseweb="input"><input type="text" value="{reps_value}"></div>
  </div>
  <div class="tp-done-stamp">
    <span class="tick">✓</span>
    <span>Drill Completed</span>
    <span class="stamp-time">14:23</span>
  </div>
</div>
"""
    else:
        actions = f"""
<div class="st-key-tp_action_01">
  <div data-testid="stTextInput" style="margin-bottom:10px;">
    <label>Reps completed</label>
    <div data-baseweb="input"><input type="text" placeholder="e.g. 4×10"></div>
  </div>
  <div class="stButton" style="margin-top:12px;">
    <button kind="primary" data-testid="stButton">⚡  Complete Drill</button>
  </div>
</div>
"""
    return card + actions


def _achievements_html() -> str:
    """Static render of the achievements grid — matches the live
    `_render_achievements` DOM exactly, with category + unlocked
    classes wired so the v8 per-cat CSS lights up.
    """
    items = [
        # (category, title, desc, icon, unlocked, progress_pct)
        ("swing",       "First Cut",            "Upload your first swing.",         "◎", True,  100),
        ("swing",       "Quick Study",          "Analyze 5 swings.",                "◎", True,  100),
        ("swing",       "Working Class Hitter", "Analyze 10 swings.",               "◎", False, 70),
        ("swing",       "Filmstudy",            "Analyze 25 swings.",               "◎", False, 28),
        ("swing",       "Cage Rat",             "Analyze 50 swings.",               "◎", False, 14),
        ("drill",       "Lab Rat",              "Analyze 100 swings.",              "◇", False, 7),
        ("drill",       "First Reps",           "Complete your first drill.",       "◇", False, 0),
        ("drill",       "Practice Pays",        "Complete 10 drills.",              "◇", False, 0),
        ("drill",       "Drill Sergeant",       "Complete 50 drills.",              "◇", False, 0),
        ("drill",       "Workhorse",            "Complete 100 drills.",             "◇", False, 0),
        ("improvement", "Big Jump",             "Improve a swing score by 10+ over a previous swing.", "▲", True, 100),
        ("score",       "Got Plus Tools",       "Hit a swing score of 70 or higher.","★", True,  100),
        ("score",       "Pro Material",         "Hit a swing score of 80 or higher.","★", True,  100),
        ("score",       "Elite Hitter",         "Hit a swing score of 90 or higher.","★", False, 90),
        ("streak",      "Spark",                "3-day training streak.",            "♢", False, 33),
        ("streak",      "On Fire",              "7-day training streak.",            "♢", False, 14),
        ("streak",      "Locked In",            "30-day training streak.",           "♢", False, 3),
        ("streak",      "Quarter Master",       "90-day training streak.",           "♢", False, 1),
        ("streak",      "Year One",             "365-day training streak.",          "♢", False, 0),
    ]
    parts = ['<div class="dt-ach-grid">']
    for cat, title, desc, icon, unlocked, pct in items:
        cls = f"dt-ach is-cat-{cat} " + ("is-unlocked" if unlocked else "is-locked")
        if unlocked:
            foot = "UNLOCKED · 2026-05-14"
            progress = ""
        else:
            foot = f"LOCKED · {pct}% PROGRESS"
            progress = (
                f'<div class="dt-ach-progress">'
                f'<div class="dt-ach-progress-fill" style="width:{pct}%;"></div>'
                f'</div>'
            )
        parts.append(
            f'<div class="{cls}">'
            f'<div class="dt-ach-badge">{icon}</div>'
            f'<div class="dt-ach-title">{title}</div>'
            f'<div class="dt-ach-desc">{desc}</div>'
            f'<div class="dt-ach-foot">{foot}</div>'
            f'{progress}'
            f'</div>'
        )
    parts.append('</div>')
    return (
        '<div class="dt-gm-section-header">'
        '<span class="dt-gm-section-eyebrow">Milestones</span>'
        '<span class="dt-gm-section-title">Achievements</span>'
        '<span class="dt-gm-section-count">5 / 19 EARNED</span>'
        '</div>'
        + "".join(parts)
    )


def _rewards_html() -> str:
    """Static render of the rewards roadmap — matches the live
    `_render_rewards` DOM exactly, with per-tier classes wired.
    """
    # (id, day, title, desc, kind, tier, unlocked, days_left, hoodie?, hof?)
    rewards = [
        ("r_silver_badge",    7,   "Silver Streak Badge",          "A 7-day badge on your profile and reports.", "status",      "bronze",    True,  0,  False, False),
        ("r_milestone_patch", 14,  "Milestone Patch",              "A digital patch to display on your locker.", "collectible", "silver",    False, 6,  False, False),
        ("r_player_card",     30,  "Personalized Player Card",     "Your own MLB-style player card.",            "graphic",     "silver",    False, 23, False, False),
        ("r_progress_report", 60,  "Elite Progress Report",        "A printable PDF summary of your gains.",     "report",      "gold",      False, 56, False, False),
        ("r_locker_title",    90,  "Locker Room Title",            "A custom title displayed on your profile.",  "title",       "gold",      False, 86, False, False),
        ("r_hoodie",          180, "Limited Edition BarrelLabs Hoodie", "Real merch shipped to your door.",    "physical",    "diamond",   False, 176, True,  False),
        ("r_merch_discount",  270, "Lifetime 25% Merch Discount",  "Forever discount on BarrelLabs merch.",      "perk",        "diamond",   False, 266, False, False),
        ("r_hall_of_fame",    365, "Hall of Fame Status",          "Permanent recognition on the leaderboard.",  "legacy",      "legendary", False, 361, False, True),
    ]
    parts = ['<div class="dt-reward-grid">']
    for rid, day, title, desc, kind, tier, unlocked, days_left, hoodie, hof in rewards:
        classes = ["dt-reward", f"is-tier-{tier}"]
        if unlocked:
            classes.append("is-unlocked")
        if hoodie:
            classes.append("is-hoodie")
        if hof:
            classes.append("is-hof")
        card_cls = " ".join(classes)
        if unlocked:
            status_html = '<div class="dt-reward-status">UNLOCKED · 2026-05-14</div>'
        else:
            plural = "S" if days_left != 1 else ""
            status_html = f'<div class="dt-reward-status">{days_left} DAY{plural} TO GO</div>'
        kind_label = kind.upper()
        tier_tag = f'<div class="tp-tier-tag is-tier-{tier}">{tier.upper()}</div>'
        parts.append(
            f'<div class="{card_cls}">'
            f'{tier_tag}'
            f'<div class="dt-reward-day">'
            f'<div class="dt-reward-day-num">{day}</div>'
            f'<div class="dt-reward-day-lbl">DAYS</div>'
            f'</div>'
            f'<div class="dt-reward-body">'
            f'<div class="dt-reward-title">{title}</div>'
            f'<div class="dt-reward-desc">{desc}</div>'
            f'<div class="dt-reward-meta-row">'
            f'<span class="dt-reward-kind is-{kind}">{kind_label}</span>'
            f'</div>'
            f'</div>'
            f'{status_html}'
            f'</div>'
        )
    parts.append('</div>')
    return (
        '<div class="dt-gm-section-header">'
        '<span class="dt-gm-section-eyebrow">Rewards Roadmap</span>'
        '<span class="dt-gm-section-title">Earned through consistency</span>'
        '<span class="dt-gm-section-count">1 / 8 UNLOCKED</span>'
        '</div>'
        + "".join(parts)
    )


def _retest_html() -> str:
    return """
<div class="dt-retest">
  <div class="dt-retest-icon">↻</div>
  <div>
    <div class="dt-retest-eyebrow">Re-Test Reminder</div>
    <div class="dt-retest-title">When to upload your next swing</div>
    <ul class="dt-retest-list">
      <li>Pick 2 drills from PRIORITY 1 — do them every practice session.</li>
      <li>Pick 1 drill from PRIORITY 2 — do it 3× per week.</li>
      <li>Re-film and re-run the comparison every 2–3 weeks. The goal is your similarity score climbing over time.</li>
    </ul>
  </div>
</div>
"""


def _progress_card_html() -> str:
    """Static stand-in for `_render_progress_card`."""
    circumference = 2 * 3.14159 * 70
    dash_offset = circumference * (1 - 0.25)
    return f"""
<div class="dt-progress-card">
  <div class="dt-ring">
    <svg viewBox="0 0 160 160">
      <circle class="dt-ring-track" cx="80" cy="80" r="70"></circle>
      <circle class="dt-ring-fill"  cx="80" cy="80" r="70"
              stroke-dasharray="{circumference:.2f}"
              stroke-dashoffset="{dash_offset:.2f}"></circle>
    </svg>
    <div class="dt-ring-center">
      <div><span class="dt-ring-pct">25</span><span class="dt-ring-pct-sym">%</span></div>
      <div class="dt-ring-tag">Warming Up</div>
    </div>
  </div>
  <div>
    <div class="dt-progress-meta-eyebrow">Session Progress</div>
    <div class="dt-progress-meta-title">Training plan for Jordan Ellis</div>
    <div class="dt-progress-meta-line">
      Built from your latest report on <strong>May 16, 2026</strong>.
      Check off drills as you complete them — progress saves automatically.
    </div>
    <div class="dt-stat-row">
      <div class="dt-stat-item">
        <div class="dt-stat-num is-red">1</div>
        <div class="dt-stat-label">Completed</div>
      </div>
      <div class="dt-stat-item">
        <div class="dt-stat-num">3</div>
        <div class="dt-stat-label">Remaining</div>
      </div>
      <div class="dt-stat-item">
        <div class="dt-stat-num">4</div>
        <div class="dt-stat-label">Total Drills</div>
      </div>
    </div>
  </div>
</div>
"""


def main() -> None:
    sys.modules["streamlit"] = _streamlit_stub()
    sys.modules.pop("development_tracker", None)
    import development_tracker as dt

    # ---- Realistic mock data ----
    saved_swing = {
        "date": "May 16, 2026",
        "timestamp": "2026-05-16T18:20:00",
        "picked_slug": "mike_trout",
        "reference_name": "mike_trout",
        "drill_plan": {
            "categories": [
                {
                    "priority": 1,
                    "title": "Sharpen Timing & Quickness",
                    "drills": [
                        {"name": "Short-Toss Quick Hands", "reps": "3 sets of 10",
                         "how": "Partner soft-tosses from 8–10 feet away. Focus on the shortest, most direct path from load to contact. No wasted motion."},
                        {"name": "Tennis Ball Reactions", "reps": "3 sets of 10",
                         "how": "Partner randomly tosses tennis balls (different speeds, small intentional pauses). Forces you to read and react rather than time a rhythm."},
                        {"name": "One-Hand Top-Hand Tee", "reps": "3 sets of 8",
                         "how": "Take swings off a tee using only your top (back) hand. Forces a compact, quick path — no looping or dragging."},
                    ],
                },
                {
                    "priority": 2,
                    "title": "Quiet the Head",
                    "drills": [
                        {"name": "Eye on Tee", "reps": "3 sets of 5",
                         "how": "Keep eyes locked through contact."},
                    ],
                },
            ],
            "weekly_guide": [
                "Pick 2 drills from PRIORITY 1 — do them every practice session.",
                "Pick 1 drill from PRIORITY 2 — do it 3× per week.",
                "Re-film and re-run the comparison every 2–3 weeks. The goal is your similarity score climbing over time.",
            ],
        },
    }
    gm_state = {"current_streak_days": 0, "longest_streak_days": 1}
    history = [
        {"timestamp": "2026-05-16T18:20:00", "date": "2026-05-16"},
        {"timestamp": "2026-05-13T08:00:00", "date": "2026-05-13"},
    ]

    metrics = dt._hero_metrics(
        saved_swing=saved_swing,
        gm_state=gm_state,
        total_completed=1,
        total_drills=4,
    )
    hero = dt._build_data_hero_html(metrics, saved_swing["date"])
    cons = dt._build_consistency_html(history, metrics["today_pct"])
    css = dt._DT_LOCAL_CSS

    # ---- Compose the full page body in the same order the live page renders ----
    coach_note = (
        "A long, slow swing arrives late and leaves you guessing. Quick "
        "hands and a compact path let you wait longer on the pitch and "
        "still get the barrel there on time."
    )

    body = (
        hero
        + cons
        + _level_card_html()
        + _stat_strip_html()
        + _motivation_strip_html()
        + _progress_card_html()
        + _category_header_html(1, "Sharpen Timing & Quickness", 3)
        + _coach_note_html(coach_note)
        + _drill_card_html(
            num="01", name="Short-Toss Quick Hands",
            role="PRIMARY", role_cls="is-primary",
            reps="3 sets of 10",
            how="Partner soft-tosses from 8–10 feet away. Focus on the shortest, most direct path from load to contact. No wasted motion.",
            done=True, mastery=4,
            instr=dt._drill_instructions("Short-Toss Quick Hands"),
            cat_title="Sharpen Timing & Quickness",
        )
        + _drill_card_html(
            num="02", name="Tennis Ball Reactions",
            role="SUPPORTING", role_cls="is-supporting",
            reps="3 sets of 10",
            how="Partner randomly tosses tennis balls (different speeds, small intentional pauses). Forces you to read and react rather than time a rhythm.",
            done=False,
            instr=dt._drill_instructions("Tennis Ball Reactions"),
            cat_title="Sharpen Timing & Quickness",
        )
        + _drill_card_html(
            num="03", name="One-Hand Top-Hand Tee",
            role="SUPPORTING", role_cls="is-supporting",
            reps="3 sets of 8",
            how="Take swings off a tee using only your top (back) hand. Forces a compact, quick path — no looping or dragging.",
            done=False,
            instr=dt._drill_instructions("One-Hand Top-Hand Tee"),
            cat_title="Sharpen Timing & Quickness",
        )
        + _category_header_html(2, "Quiet the Head", 1)
        + _coach_note_html(
            "Head drift adds variance to your contact point. Pinning it "
            "down stabilises everything downstream."
        )
        + _drill_card_html(
            num="04", name="Eye on Tee",
            role="CHALLENGE", role_cls="is-challenge",
            reps="3 sets of 5",
            how="Keep eyes locked through contact.",
            done=False,
            instr=dt._drill_instructions("Eye-on-the-Tee"),
            cat_title="Quiet the Head",
        )
        + _retest_html()
        + _achievements_html()
        + _rewards_html()
    )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Training Plan v3.1 full preview</title>
{css}
<style>
body {{ background: #0A0B0E; margin: 0; padding: 40px 56px; }}
</style>
</head>
<body>
<div class="tp-shell bl-page">
{body}
</div>
</body></html>
"""
    out_html = Path("/tmp/training_plan_full.html")
    out_html.write_text(html, encoding="utf-8")
    print(f"wrote {out_html}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(out_html.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        out_desk = Path("/tmp/training_plan_full_desktop.png")
        page.screenshot(path=str(out_desk), full_page=True)
        print(f"wrote {out_desk}")

        ctx.close()
        ctx = b.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        page.goto(out_html.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        out_mob = Path("/tmp/training_plan_full_mobile.png")
        page.screenshot(path=str(out_mob), full_page=True)
        print(f"wrote {out_mob}")
        b.close()


if __name__ == "__main__":
    main()
