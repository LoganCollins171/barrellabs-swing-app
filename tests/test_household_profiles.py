# tests/test_household_profiles.py
"""Household sub-accounts — multi-profile auth helpers."""
from __future__ import annotations
import sys, types
from pathlib import Path
import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


def _passthrough_decorator(*dargs, **dkwargs):
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return dargs[0]
    def inner(fn):
        return fn
    return inner


@pytest.fixture(autouse=True)
def _stub_streamlit(monkeypatch):
    ss = {}
    st_stub = types.SimpleNamespace(
        session_state=ss,
        error=lambda *a, **k: None,
        markdown=lambda *a, **k: None,
        rerun=lambda: None,
        cache_resource=_passthrough_decorator,
        cache_data=_passthrough_decorator,
    )
    monkeypatch.setitem(sys.modules, "streamlit", st_stub)
    # Also stub supabase_client so auth.py can be imported without a live DB
    sc_stub = types.ModuleType("supabase_client")
    sc_stub.get_client = lambda: None
    sc_stub.store_session = lambda *a, **k: None
    sc_stub.clear_session = lambda *a, **k: None
    sc_stub.get_current_user = lambda: None
    monkeypatch.setitem(sys.modules, "supabase_client", sc_stub)
    for m in ("auth",):
        sys.modules.pop(m, None)
    return ss


class TestListHouseholdPlayers:
    def test_returns_all_non_removed(self, monkeypatch):
        import auth
        rows = [
            {"id": "p1", "user_id": "u", "name": "Dad", "removed_at": None},
            {"id": "p2", "user_id": "u", "name": "Tommy", "removed_at": None},
            {"id": "p3", "user_id": "u", "name": "Old", "removed_at": "2026-01-01"},
        ]
        monkeypatch.setattr(auth, "_query_household_rows", lambda uid: rows)
        out = auth.list_household_players("u")
        names = [p["name"] for p in out]
        assert names == ["Dad", "Tommy"]   # removed excluded

    def test_empty_when_no_user(self, monkeypatch):
        import auth
        assert auth.list_household_players("") == []


class TestSetActivePlayer:
    def test_sets_session_when_owned(self, monkeypatch, _stub_streamlit):
        import auth
        rows = [{"id": "p2", "user_id": "u", "name": "Tommy", "removed_at": None}]
        monkeypatch.setattr(auth, "_query_household_rows", lambda uid: rows)
        monkeypatch.setattr(auth, "_current_user_id", lambda: "u")
        ok = auth.set_active_player("p2")
        assert ok is True
        assert _stub_streamlit["player"]["id"] == "p2"

    def test_rejects_unowned_profile(self, monkeypatch, _stub_streamlit):
        """IDOR guard: can't activate a profile that isn't in the household."""
        import auth
        rows = [{"id": "p2", "user_id": "u", "name": "Tommy", "removed_at": None}]
        monkeypatch.setattr(auth, "_query_household_rows", lambda uid: rows)
        monkeypatch.setattr(auth, "_current_user_id", lambda: "u")
        ok = auth.set_active_player("someone-elses-id")
        assert ok is False
        assert "player" not in _stub_streamlit


class TestNeedsProfilePick:
    def test_solo_autoselects(self, monkeypatch, _stub_streamlit):
        import auth
        rows = [{"id": "p1", "user_id": "u", "name": "Solo", "removed_at": None}]
        monkeypatch.setattr(auth, "_query_household_rows", lambda uid: rows)
        monkeypatch.setattr(auth, "_current_user_id", lambda: "u")
        # 1 profile → auto-selected, no pick needed
        assert auth.needs_profile_pick() is False
        assert _stub_streamlit["player"]["id"] == "p1"

    def test_household_needs_pick(self, monkeypatch, _stub_streamlit):
        import auth
        rows = [
            {"id": "p1", "user_id": "u", "name": "Dad", "removed_at": None},
            {"id": "p2", "user_id": "u", "name": "Tommy", "removed_at": None},
        ]
        monkeypatch.setattr(auth, "_query_household_rows", lambda uid: rows)
        monkeypatch.setattr(auth, "_current_user_id", lambda: "u")
        assert auth.needs_profile_pick() is True
        assert "player" not in _stub_streamlit   # nothing auto-picked

    def test_no_pick_once_active(self, monkeypatch, _stub_streamlit):
        import auth
        _stub_streamlit["player"] = {"id": "p2", "name": "Tommy"}
        rows = [
            {"id": "p1", "user_id": "u", "name": "Dad", "removed_at": None},
            {"id": "p2", "user_id": "u", "name": "Tommy", "removed_at": None},
        ]
        monkeypatch.setattr(auth, "_query_household_rows", lambda uid: rows)
        monkeypatch.setattr(auth, "_current_user_id", lambda: "u")
        assert auth.needs_profile_pick() is False


class TestActivePlayerDrivesAge:
    """The active player (st.session_state['player']) — not a stale app-level
    'user' copy — must drive the age bracket. Regression guard for the
    session-drift bug where a freshly-saved birth year, or a switch to a
    different household child, never reached the analysis age path."""

    @staticmethod
    def _rows():
        return [
            {"id": "p1", "user_id": "u", "name": "Ann",
             "handedness": "RIGHT", "birth_year": 2010, "removed_at": None},
            {"id": "p2", "user_id": "u", "name": "Ben",
             "handedness": "RIGHT", "birth_year": 2016, "removed_at": None},
        ]

    def test_switch_child_updates_age_source(self, monkeypatch, _stub_streamlit):
        import datetime
        import auth
        from analyzer import age_from_birth_year

        monkeypatch.setattr(auth, "_query_household_rows",
                            lambda uid: self._rows())
        monkeypatch.setattr(auth, "_current_user_id", lambda: "u")

        # App started on the first child and cached a stale `user` copy.
        _stub_streamlit["user"] = {"id": "p1", "birth_year": 2010}

        # Household switches to the younger child.
        assert auth.set_active_player("p2") is True
        assert _stub_streamlit["player"]["birth_year"] == 2016

        # app.py reconcile: `user` follows the active player every render.
        if _stub_streamlit.get("player"):
            _stub_streamlit["user"] = _stub_streamlit["player"]

        # Age now derives from the ACTIVE child, not the stale 2010 copy.
        user = _stub_streamlit["user"]
        assert user["birth_year"] == 2016
        assert (age_from_birth_year(user["birth_year"])
                == datetime.date.today().year - 2016)

    def test_birth_year_edit_reaches_age_source(self, monkeypatch,
                                                _stub_streamlit):
        import datetime
        import auth
        from analyzer import age_from_birth_year

        # Solo player saved with no birth year; app cached a stale copy.
        _stub_streamlit["user"] = {"id": "p1", "birth_year": None}
        _stub_streamlit["player"] = {"id": "p1", "birth_year": None}

        # update_profile writes the new birth year to the canonical ["player"].
        class _Resp:
            data = [{"id": "p1", "name": "Solo", "handedness": "RIGHT",
                     "birth_year": 2014}]

        class _Tbl:
            def update(self, p): return self
            def eq(self, *a, **k): return self
            def execute(self): return _Resp()

        monkeypatch.setattr(
            auth, "get_client",
            lambda: types.SimpleNamespace(table=lambda _n: _Tbl()))

        prof = auth.update_profile("p1", birth_year=2014)
        assert prof["birth_year"] == 2014
        assert _stub_streamlit["player"]["birth_year"] == 2014

        # Reconcile → `user` reflects the edit, so analysis sees the new age.
        _stub_streamlit["user"] = _stub_streamlit["player"]
        assert (age_from_birth_year(_stub_streamlit["user"]["birth_year"])
                == datetime.date.today().year - 2014)


class TestCreateHouseholdPlayerBirthYear:
    """Adding a household child captures birth year so their FIRST swing is
    scored on the right age band (the create RPC has no birth_year param, so
    it's applied via an owner-scoped follow-up update on the new row)."""

    def test_birth_year_applied_after_create(self, monkeypatch, _stub_streamlit):
        import auth
        created = {"id": "p9", "user_id": "u", "name": "Kid",
                   "handedness": "RIGHT", "birth_year": None}
        updates = []

        class _RPC:
            def execute(self):
                return types.SimpleNamespace(data=[dict(created)])

        class _Tbl:
            def update(self, payload): self._p = payload; return self
            def eq(self, *a, **k): return self
            def execute(self):
                updates.append(self._p)
                merged = dict(created); merged.update(self._p)
                return types.SimpleNamespace(data=[merged])

        class _Client:
            def rpc(self, *a, **k): return _RPC()
            def table(self, *a, **k): return _Tbl()

        monkeypatch.setattr(auth, "get_client", lambda: _Client())

        # Use a teen birth year: COPPA consent is only required under 13, so a
        # 2009 birth year exercises the birth_year-stamping path without the
        # guardian_consent gate (which would also add consent fields to update).
        res = auth.create_household_player("Kid", "RIGHT", None, True,
                                           birth_year=2009)
        assert res["ok"] is True
        assert updates == [{"birth_year": 2009}]
        assert res["player"]["birth_year"] == 2009

    def test_blank_birth_year_skips_update_for_explicit_adult(
            self, monkeypatch, _stub_streamlit):
        """An explicitly non-minor profile (is_minor=False — e.g. the
        household owner adding another adult) can still be created with no
        birth year at all, with no follow-up write. BL-004C: this must stay
        true even after the fail-closed consent fix, so adult workflows
        aren't forced to collect unnecessary birth-year data (see
        TestCreateHouseholdPlayerConsentFailClosed for the minor-path
        enforcement, which is a *different* call shape: is_minor=True)."""
        import auth
        created = {"id": "p9", "user_id": "u", "name": "Kid",
                   "handedness": "RIGHT"}
        updates = []

        class _RPC:
            def execute(self):
                return types.SimpleNamespace(data=[dict(created)])

        class _Tbl:
            def update(self, payload): updates.append(payload); return self
            def eq(self, *a, **k): return self
            def execute(self): return types.SimpleNamespace(data=[dict(created)])

        class _Client:
            def rpc(self, *a, **k): return _RPC()
            def table(self, *a, **k): return _Tbl()

        monkeypatch.setattr(auth, "get_client", lambda: _Client())

        res = auth.create_household_player("Kid", birth_year="", is_minor=False)
        assert res["ok"] is True
        assert updates == []  # no valid birth year → no follow-up write


class TestCreateHouseholdPlayerConsentFailClosed:
    """BL-004C: create_household_player must fail closed when a minor's
    age can't be verified, instead of silently treating "unknown" as
    "doesn't need consent" (the bug: is_under_coppa_age() returns None for
    a missing/invalid birth year, and the old code did
    bool(None) == False, which bool()-coerced "can't tell" into "old
    enough"). Covers: missing birth year, invalid birth year, an actual
    underage player with and without consent, a teen (not COPPA-covered),
    and an explicit adult profile needing neither."""

    @staticmethod
    def _client(created: dict):
        """A minimal stub Supabase client: the create RPC returns `created`,
        and any update() call records its payload and merges it in."""
        updates = []

        class _RPC:
            def execute(self):
                return types.SimpleNamespace(data=[dict(created)])

        class _Tbl:
            def update(self, payload):
                self._p = payload
                return self
            def eq(self, *a, **k):
                return self
            def execute(self):
                updates.append(self._p)
                merged = dict(created)
                merged.update(self._p)
                return types.SimpleNamespace(data=[merged])

        class _Client:
            def rpc(self, *a, **k): return _RPC()
            def table(self, *a, **k): return _Tbl()

        return _Client(), updates

    def test_missing_birth_year_with_default_is_minor_fails_closed(
            self, monkeypatch, _stub_streamlit):
        """The exact bug: no birth year, is_minor left at its default
        (True). Must be REJECTED, not silently allowed through."""
        import auth
        client, updates = self._client(
            {"id": "p1", "user_id": "u", "name": "Kid", "handedness": "RIGHT"})
        monkeypatch.setattr(auth, "get_client", lambda: client)

        res = auth.create_household_player("Kid")  # no birth_year at all
        assert res["ok"] is False
        assert "birth year" in res["error"].lower()
        assert updates == []  # never reached the create RPC / DB at all

    def test_invalid_birth_year_fails_closed(self, monkeypatch, _stub_streamlit):
        """An unparseable/out-of-range birth year must be treated the same
        as missing — never silently treated as "old enough"."""
        import auth
        client, updates = self._client(
            {"id": "p2", "user_id": "u", "name": "Kid", "handedness": "RIGHT"})
        monkeypatch.setattr(auth, "get_client", lambda: client)

        for bad_value in ("not-a-year", "3000", "1850"):
            res = auth.create_household_player("Kid", birth_year=bad_value)
            assert res["ok"] is False, f"birth_year={bad_value!r} should fail closed"
            assert "birth year" in res["error"].lower()
        assert updates == []

    def test_underage_player_without_consent_is_blocked(
            self, monkeypatch, _stub_streamlit):
        """A verified under-13 birth year, no guardian_consent → blocked,
        with the specific COPPA error message (not the generic
        missing-birth-year one — the birth year WAS provided here)."""
        import auth
        client, updates = self._client(
            {"id": "p3", "user_id": "u", "name": "Kid", "handedness": "RIGHT"})
        monkeypatch.setattr(auth, "get_client", lambda: client)

        res = auth.create_household_player("Kid", birth_year=2017)  # ~9yo
        assert res["ok"] is False
        assert "parent or" in res["error"].lower()
        assert "guardian" in res["error"].lower()
        assert updates == []

    def test_underage_player_with_consent_succeeds_and_stamps_record(
            self, monkeypatch, _stub_streamlit):
        """A verified under-13 birth year WITH guardian_consent=True must
        succeed and stamp the consent audit-trail fields on the new row."""
        import auth
        client, updates = self._client(
            {"id": "p4", "user_id": "u", "name": "Kid", "handedness": "RIGHT"})
        monkeypatch.setattr(auth, "get_client", lambda: client)
        monkeypatch.setattr(auth, "_current_user_id", lambda: "owner-uid")

        res = auth.create_household_player(
            "Kid", birth_year=2017, guardian_consent=True)
        assert res["ok"] is True
        assert len(updates) == 1
        assert updates[0]["birth_year"] == 2017
        assert updates[0]["guardian_consent_by"] == "owner-uid"
        assert "guardian_consent_at" in updates[0]
        assert updates[0]["guardian_consent_method"] == "authenticated_account"

    def test_teen_birth_year_needs_no_consent(self, monkeypatch, _stub_streamlit):
        """A verified 13-17 birth year is a minor but NOT COPPA-covered —
        must succeed with no consent and no consent fields stamped."""
        import auth
        client, updates = self._client(
            {"id": "p5", "user_id": "u", "name": "Teen", "handedness": "RIGHT"})
        monkeypatch.setattr(auth, "get_client", lambda: client)

        res = auth.create_household_player("Teen", birth_year=2012)  # ~14yo
        assert res["ok"] is True
        assert updates == [{"birth_year": 2012}]  # no guardian_consent_* keys

    def test_explicit_adult_with_no_birth_year_needs_neither(
            self, monkeypatch, _stub_streamlit):
        """is_minor=False (an explicit, affirmative "this is not a minor")
        must succeed with no birth year and no consent required — adult
        workflows must keep working, and shouldn't be forced to supply
        unnecessary personal information."""
        import auth
        client, updates = self._client(
            {"id": "p6", "user_id": "u", "name": "Coach", "handedness": "RIGHT"})
        monkeypatch.setattr(auth, "get_client", lambda: client)

        res = auth.create_household_player("Coach", is_minor=False)
        assert res["ok"] is True
        assert updates == []

    def test_is_minor_false_is_overridden_by_a_contradicting_birth_year(
            self, monkeypatch, _stub_streamlit):
        """If a birth year IS provided and it says under-13, that real
        data must win over a caller's is_minor=False claim — never trust
        an unverified flag over an actual verified age."""
        import auth
        client, updates = self._client(
            {"id": "p7", "user_id": "u", "name": "Kid", "handedness": "RIGHT"})
        monkeypatch.setattr(auth, "get_client", lambda: client)

        res = auth.create_household_player(
            "Kid", birth_year=2018, is_minor=False)  # contradicts is_minor
        assert res["ok"] is False
        assert "guardian" in res["error"].lower()
        assert updates == []
