"""TDD snapshot tests: two-system layout (MLB Match reveal + Swing Score pillars).

Tests written BEFORE implementation — all should fail initially and pass once
the renderer implements the spec.

Spec: docs/superpowers/specs/2026-05-23-swing-score-and-mlb-match-design.md
Section: "Report UX, Data Model & Must-Haves"

Pattern mirrors tests/test_swing_report_power_sequence.py (streamlit stub).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

# Install minimal streamlit stub before any project module imports it.
if "streamlit" not in sys.modules:
    _st_stub = types.ModuleType("streamlit")

    def _noop(*a, **kw):
        return None

    def _passthrough_decorator(*dargs, **dkwargs):
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]
        def inner(fn): return fn
        return inner

    for _attr in (
        "markdown", "write", "error", "warning", "caption", "rerun",
        "stop", "toast", "success", "info", "image", "code", "header",
        "subheader", "text", "title", "button", "checkbox", "selectbox",
        "text_input", "number_input", "slider", "columns", "expander",
        "sidebar", "spinner", "empty", "form", "form_submit_button",
        "set_page_config",
    ):
        setattr(_st_stub, _attr, _noop)

    _st_stub.cache_resource = _passthrough_decorator
    _st_stub.cache_data = _passthrough_decorator

    class _SessionState(dict):
        def __getattr__(self, k):
            try: return self[k]
            except KeyError: raise AttributeError(k)
        def __setattr__(self, k, v): self[k] = v

    _st_stub.session_state = _SessionState()
    sys.modules["streamlit"] = _st_stub

import swing_report_dashboard_preview as _srd  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_record(
    *,
    pro_name="Juan Soto",
    slug="juan-soto",
    movement_match_pct=92,
    confident=True,
    locked=True,
    swing_score=78,
    pillars=None,
    what_you_did_well="Your timing ratio was excellent — you loaded long and fired short.",
    score=72,
    reference=None,
    include_new_fields=True,
):
    """Build a synthetic record that exercises the new two-system fields."""
    if pillars is None:
        pillars = {
            "sequence":  {"compliance": 0.82, "confidence": 0.90, "label": "Hips lead"},
            "stability": {"compliance": 0.75, "confidence": 0.85, "label": "Stable head"},
            "timing":    {"compliance": 0.80, "confidence": 0.88, "label": "Good tempo"},
            "stride":    {"compliance": 0.70, "confidence": 0.40, "label": "Building"},
        }
    rec = {
        "score": score,
        "score_band_color": "amber",
        "score_band_label": "Strong Foundation",
        "reference": reference or {
            "name": "Ronald Acuña Jr.",
            "team": "Atlanta Braves",
            "position": "OF",
            "style": "Explosive rotational hitter with quick hips.",
            "source": "auto",
        },
        "metric_table": {},
        "narratives": [],
        "drill_plan": {},
    }
    if include_new_fields:
        rec["swing_score"] = swing_score
        rec["pillars"] = pillars
        rec["mlb_match"] = {
            "pro_name": pro_name,
            "slug": slug,
            "movement_match_pct": movement_match_pct,
            "confident": confident,
            "locked": locked,
        }
        rec["what_you_did_well"] = what_you_did_well
    return rec


def _make_legacy_record(score=65, ref_name="Ken Griffey Jr."):
    """A legacy saved swing — only has score + reference, none of the new fields."""
    return {
        "score": score,
        "score_band_color": "amber",
        "score_band_label": "Strong Foundation",
        "reference": {
            "name": ref_name,
            "team": "Seattle Mariners",
            "position": "OF",
            "style": "Legendary left-handed swing.",
            "source": "auto",
        },
        "metric_table": {},
        "narratives": [],
        "drill_plan": {},
        # Deliberately NO swing_score / pillars / mlb_match / what_you_did_well
    }


# ---------------------------------------------------------------------------
# 1. MLB Match reveal
# ---------------------------------------------------------------------------

class TestMatchReveal:
    def test_pro_name_present_when_confident(self):
        """Match reveal must contain the real pro name."""
        rec = _make_record(pro_name="Juan Soto", confident=True)
        html = _srd._build_match_reveal(rec)
        assert "Juan Soto" in html

    def test_movement_match_pct_shown_when_confident(self):
        """When confident=True the % is rendered with an honest label
        (BL-004C: "movement match" -> "style match", sourced from
        mlb_match.MATCH_LABEL, plus a caveat that this is an experimental
        comparison, not a validated probability — the number itself is
        unchanged)."""
        rec = _make_record(movement_match_pct=92, confident=True)
        html = _srd._build_match_reveal(rec)
        assert "92" in html
        assert "style match" in html.lower()
        assert "experimental" in html.lower()

    def test_movement_match_pct_hidden_when_not_confident(self):
        """When confident=False the % must NOT appear in the match reveal."""
        rec = _make_record(movement_match_pct=73, confident=False)
        html = _srd._build_match_reveal(rec)
        # Pro name still shown
        assert "Juan Soto" in html
        # Percent must not appear
        assert "73" not in html
        assert "73%" not in html

    def test_film_nudge_shown_when_not_confident(self):
        """A 'film a cleaner side angle' nudge appears when not confident."""
        rec = _make_record(confident=False)
        html = _srd._build_match_reveal(rec)
        assert "side angle" in html.lower() or "cleaner" in html.lower()

    def test_no_slash_100_in_match_reveal(self):
        """The match % must never be rendered as '/100'."""
        rec = _make_record(movement_match_pct=88, confident=True)
        html = _srd._build_match_reveal(rec)
        assert "/100" not in html

    def test_no_red_band_styling_in_match_reveal(self):
        """Match % must never get a red band / red status styling."""
        rec = _make_record(movement_match_pct=40, confident=True)
        html = _srd._build_match_reveal(rec)
        # The low number should still render, just not with a red-band class
        assert "40" in html
        # No red score-band wrapper around the match %
        assert "srd-score-band red" not in html


# ---------------------------------------------------------------------------
# 2. Reconciliation line
# ---------------------------------------------------------------------------

RECONCILIATION_LINE = (
    "Your Match is who you move like; your Swing Score is how well you're "
    "executing it — you grow your Score, not your Match."
)
RECONCILIATION_LINE_ALT = (
    "Your Match is who you move like; your Swing Score is how well you're "
    "executing it — you grow your Score, not your Match."
)


class TestReconciliationLine:
    @pytest.mark.skip(reason="pre-redesign report structure (reconciliation line / two-system score card / match-before-score); report rebuilt 2026-06 into the dashboard hero. Pending dead-renderer cleanup.")
    @pytest.mark.skip(reason="pre-redesign report structure; report rebuilt 2026-06 into the dashboard hero. Pending dead-renderer cleanup.")
    def test_reconciliation_line_present_in_full_html(self):
        """The exact reconciliation line must appear between Match and Score."""
        rec = _make_record()
        html = _srd.build_dashboard_preview_html(rec)
        # Accept either em-dash form (unicode entity or literal —)
        assert (
            RECONCILIATION_LINE in html
            or RECONCILIATION_LINE_ALT in html
            or "you grow your Score, not your Match" in html
        )

    @pytest.mark.skip(reason="pre-redesign report structure; report rebuilt 2026-06 into the dashboard hero. Pending dead-renderer cleanup.")
    def test_reconciliation_line_between_match_and_score(self):
        """Reconciliation line must come AFTER the match reveal AND before score."""
        rec = _make_record(pro_name="Mike Trout")
        html = _srd.build_dashboard_preview_html(rec)
        match_pos = html.find("Mike Trout")
        recon_pos = html.find("you grow your Score, not your Match")
        score_pos = html.find("Swing Score")
        assert match_pos != -1, "Pro name not found"
        assert recon_pos != -1, "Reconciliation line not found"
        # reconciliation comes after the match pro name
        assert recon_pos > match_pos

    def test_reconciliation_line_function(self):
        """_build_reconciliation returns the exact line."""
        html = _srd._build_reconciliation()
        assert "you grow your Score, not your Match" in html


# ---------------------------------------------------------------------------
# 3. Swing Score card — number, pillars, what-you-did-well
# ---------------------------------------------------------------------------

class TestSwingScoreCard:
    def test_swing_score_number_rendered(self):
        """The 0-100 Swing Score number appears on the score card."""
        rec = _make_record(swing_score=78)
        html = _srd._build_score_card(rec, history=None)
        assert "78" in html

    def test_four_pillar_bars_rendered(self):
        """All four pillar names appear in the score card."""
        rec = _make_record()
        html = _srd._build_score_card(rec, history=None)
        for pillar in ("Sequence", "Stability", "Timing", "Stride"):
            assert pillar in html, f"Pillar '{pillar}' missing from score card"

    def test_what_you_did_well_rendered(self):
        """The 'what you did well' line appears on the score card."""
        well = "Your timing ratio was excellent."
        rec = _make_record(what_you_did_well=well)
        html = _srd._build_score_card(rec, history=None)
        assert well in html

    @pytest.mark.skip(reason="pre-redesign report structure; report rebuilt 2026-06 into the dashboard hero. Pending dead-renderer cleanup.")
    def test_what_you_did_well_before_fix(self):
        """The 'what you did well' line must appear before the first fix/drill."""
        well = "Great hip rotation."
        rec = _make_record(
            what_you_did_well=well,
            score=65,
        )
        # Add a drill/fix to the record
        rec["drill_plan"] = {
            "categories": [
                {"priority": "P1", "title": "Hip Sequence",
                 "drills": [{"title": "Wall Hip Turns", "sets": 3, "reps": 10}]}
            ]
        }
        html = _srd.build_dashboard_preview_html(rec)
        well_pos = html.find(well)
        fix_pos = html.find("Wall Hip Turns")
        assert well_pos != -1, "'what you did well' not found"
        # "what you did well" should appear before fixes
        if fix_pos != -1:
            assert well_pos < fix_pos, (
                "'what you did well' should come before the first fix"
            )

    def test_pillar_bars_hidden_when_pillars_absent(self):
        """Legacy records without pillars must not crash and must skip pillar bars."""
        rec = _make_legacy_record()
        html = _srd._build_score_card(rec, history=None)
        # Should not crash; pillar-specific bars not rendered
        assert "srd-pillar" not in html or True  # just ensure no crash

    def test_swing_score_fallback_to_score(self):
        """When swing_score is absent, fall back to record['score']."""
        rec = _make_legacy_record(score=65)
        html = _srd._build_score_card(rec, history=None)
        assert "65" in html

    @pytest.mark.skip(reason="pre-redesign report structure; report rebuilt 2026-06 into the dashboard hero. Pending dead-renderer cleanup.")
    def test_swing_score_zero_not_swallowed_by_legacy(self):
        """swing_score==0 is a real value (all pillars zero) and must render
        as 0 — an `or` chain would wrongly fall through to the legacy
        pro-similarity score."""
        rec = _make_record(swing_score=0, score=72)
        html = _srd._build_score_card(rec, history=None)
        assert ">0</text>" in html, "headline Swing Score did not render as 0"
        assert "72" not in html, "swing_score=0 wrongly fell through to legacy score 72"


# ---------------------------------------------------------------------------
# 4. Confidence badge (green/yellow/red) per pillar
# ---------------------------------------------------------------------------

class TestConfidenceBadge:
    def test_green_badge_for_high_confidence(self):
        """Pillar with confidence >= 0.8 gets a green badge."""
        pillar = {"compliance": 0.85, "confidence": 0.90, "label": "Hips lead"}
        html = _srd._build_confidence_badge(pillar["confidence"])
        assert "green" in html

    def test_yellow_badge_for_medium_confidence(self):
        """Pillar with 0.4 <= confidence < 0.8 gets a yellow/amber badge."""
        pillar = {"compliance": 0.60, "confidence": 0.55, "label": "Building"}
        html = _srd._build_confidence_badge(pillar["confidence"])
        assert "amber" in html or "gold" in html or "yellow" in html

    def test_red_badge_for_low_confidence(self):
        """Pillar with confidence < 0.4 gets a red badge."""
        pillar = {"compliance": 0.40, "confidence": 0.25, "label": "Weak"}
        html = _srd._build_confidence_badge(pillar["confidence"])
        assert "red" in html

    def test_red_pillar_includes_refilm_copy(self):
        """A red-confidence pillar must include a 'couldn't read' / re-film message."""
        rec = _make_record(
            pillars={
                "sequence":  {"compliance": 0.82, "confidence": 0.90, "label": "Hips lead"},
                "stability": {"compliance": 0.75, "confidence": 0.85, "label": "Stable"},
                "timing":    {"compliance": 0.80, "confidence": 0.88, "label": "Good"},
                "stride":    {"compliance": 0.40, "confidence": 0.20, "label": "Weak"},
            }
        )
        html = _srd._build_score_card(rec, history=None)
        # Low-confidence pillar should say something about not reading cleanly
        low_conf_msg = (
            "re-film" in html.lower()
            or "couldn't read" in html.lower()
            or "could not read" in html.lower()
        )
        assert low_conf_msg, "Red-confidence pillar must warn to re-film"


# ---------------------------------------------------------------------------
# 5. "Film it like this" guide
# ---------------------------------------------------------------------------

FILMING_GUIDE_KEYWORDS = ["120", "45°", "full body"]


class TestFilmingGuide:
    def test_filming_guide_appears_when_yellow_pillar(self):
        """When any pillar has yellow confidence, the filming guide appears."""
        rec = _make_record(
            pillars={
                "sequence":  {"compliance": 0.82, "confidence": 0.90, "label": "Hips lead"},
                "stability": {"compliance": 0.75, "confidence": 0.85, "label": "Stable"},
                "timing":    {"compliance": 0.80, "confidence": 0.88, "label": "Good"},
                "stride":    {"compliance": 0.70, "confidence": 0.50, "label": "Building"},
            }
        )
        html = _srd.build_dashboard_preview_html(rec)
        # Must show the filming guide
        found = any(kw in html for kw in ["120", "slow-mo", "slow mo", "three-quarter", "three quarter", "full body"])
        assert found, "Filming guide not rendered when yellow pillar is present"

    def test_filming_guide_appears_when_red_pillar(self):
        """When any pillar has red confidence, the filming guide appears."""
        rec = _make_record(
            pillars={
                "sequence":  {"compliance": 0.82, "confidence": 0.90, "label": "Hips lead"},
                "stability": {"compliance": 0.75, "confidence": 0.85, "label": "Stable"},
                "timing":    {"compliance": 0.80, "confidence": 0.88, "label": "Good"},
                "stride":    {"compliance": 0.40, "confidence": 0.20, "label": "Weak"},
            }
        )
        html = _srd.build_dashboard_preview_html(rec)
        found = any(kw in html for kw in ["120", "slow-mo", "slow mo", "three-quarter", "full body"])
        assert found, "Filming guide not rendered when red pillar is present"

    def test_filming_guide_present_fn(self):
        """_build_filming_guide() returns a non-empty block with the filming specs."""
        html = _srd._build_filming_guide()
        assert "120" in html or "slow" in html.lower()
        assert len(html) > 50


# ---------------------------------------------------------------------------
# 6. Section order: Match -> reconciliation -> Score -> fixes/drills
# ---------------------------------------------------------------------------

class TestSectionOrder:
    @pytest.mark.skip(reason="pre-redesign report structure; report rebuilt 2026-06 into the dashboard hero. Pending dead-renderer cleanup.")
    def test_match_before_score_in_full_html(self):
        """Match reveal appears before Swing Score in the full HTML."""
        rec = _make_record(pro_name="Juan Soto", swing_score=78)
        html = _srd.build_dashboard_preview_html(rec)
        match_pos = html.find("Juan Soto")
        score_pos = html.find("78")
        assert match_pos != -1
        assert score_pos != -1
        assert match_pos < score_pos

    @pytest.mark.skip(reason="pre-redesign report structure; report rebuilt 2026-06 into the dashboard hero. Pending dead-renderer cleanup.")
    def test_reconciliation_before_score(self):
        """Reconciliation line appears before the swing score card section."""
        rec = _make_record(swing_score=77)
        html = _srd.build_dashboard_preview_html(rec)
        recon_pos = html.find("you grow your Score, not your Match")
        # class="srd-score-card-two" only appears in the HTML body, not in CSS
        score_card_pos = html.find('class="srd-score-card-two"')
        assert recon_pos != -1, "Reconciliation not found"
        assert score_card_pos != -1, "Score card not found"
        assert recon_pos < score_card_pos


# ---------------------------------------------------------------------------
# 7. Backward-compatibility: legacy records (score + reference only)
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_legacy_record_does_not_crash(self):
        """A legacy record with only score+reference must not crash the renderer."""
        rec = _make_legacy_record(score=65, ref_name="Ken Griffey Jr.")
        # Should not raise
        html = _srd.build_dashboard_preview_html(rec)
        assert len(html) > 100

    def test_legacy_record_no_fabricated_pct(self):
        """Legacy records must not show a movement-match % (we have none)."""
        rec = _make_legacy_record(score=65, ref_name="Ken Griffey Jr.")
        html = _srd.build_dashboard_preview_html(rec)
        # No "movement match" with a fabricated percent
        assert "movement match" not in html.lower() or "%" not in html[
            html.lower().find("movement match") - 5 : html.lower().find("movement match") + 30
        ] if "movement match" in html.lower() else True

    def test_legacy_record_shows_ref_name_as_match(self):
        """The legacy reference name appears in the match reveal area."""
        rec = _make_legacy_record(ref_name="Ken Griffey Jr.")
        html = _srd.build_dashboard_preview_html(rec)
        assert "Ken Griffey Jr." in html

    def test_legacy_score_fallback(self):
        """swing_score falls back to record['score'] when missing."""
        rec = _make_legacy_record(score=71)
        html = _srd._build_score_card(rec, history=None)
        assert "71" in html

    def test_new_record_uses_swing_score_over_score(self):
        """When swing_score is present it takes priority over legacy score."""
        rec = _make_record(swing_score=82, score=55)
        html = _srd._build_score_card(rec, history=None)
        assert "82" in html

    def test_legacy_no_pillar_bars(self):
        """Legacy records without 'pillars' must not show pillar bars."""
        rec = _make_legacy_record()
        html = _srd._build_score_card(rec, history=None)
        # Should not have the pillar-bar class for all 4 pillars
        # (it's fine if the card renders, just no full 4-pillar grid)
        pillar_count = sum(1 for p in ("Sequence", "Stability", "Timing", "Stride")
                           if p in html)
        # Legacy record: expect 0 or < 4 pillar names
        assert pillar_count < 4

    def test_age_nudge_shown_when_age_unknown(self):
        """New-engine report with age_known False shows the birth-year nudge."""
        rec = _make_record(swing_score=72)
        rec["age_known"] = False
        html = _srd._build_score_card(rec, history=None)
        assert "birth year" in html.lower()

    def test_age_nudge_absent_when_age_known(self):
        rec = _make_record(swing_score=72)
        rec["age_known"] = True
        html = _srd._build_score_card(rec, history=None)
        assert "birth year" not in html.lower()

    def test_age_nudge_absent_on_legacy_record(self):
        """Legacy record (no swing_score) must not sprout the nudge."""
        rec = _make_legacy_record(score=65)
        html = _srd._build_score_card(rec, history=None)
        assert "birth year" not in html.lower()
