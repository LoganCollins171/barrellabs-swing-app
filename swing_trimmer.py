"""BL-008C.36 -- Python side of components/swing_trimmer (the visual swing
selection player). See that file for the browser half.

Preview frames are served as Streamlit media files (the same mechanism
st.image uses): each is an unguessable, content-addressed URL registered to
THIS browser session only, re-registered every run and dropped by Streamlit
when the session stops using it. So frames never travel through the
websocket on every rerun, the browser can cache them, and one user's frames
are never listed for another user.
"""
from __future__ import annotations

import base64
import os

import streamlit.components.v1 as components

_component_fn = None


def _component():
    global _component_fn
    if _component_fn is None:
        _component_fn = components.declare_component(
            "swing_trimmer",
            path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "swing_trimmer"),
        )
    return _component_fn


def preview_urls(clip_id: str, previews: list[tuple[int, bytes]]) -> list[str]:
    """Register each JPEG for this session and return its URL. Must be called
    on every run that shows the player (Streamlit forgets a session's media
    references at the start of each run and deletes orphans after it)."""
    from streamlit import runtime
    if not runtime.exists():          # headless tests (AppTest): inline instead
        return ["data:image/jpeg;base64," + base64.b64encode(b).decode("ascii") for _, b in previews]
    mgr = runtime.get_instance().media_file_mgr
    return [mgr.add(data, "image/jpeg", f"bl_trim.{clip_id}.{n}") for n, (_, data) in enumerate(previews)]


def swing_trimmer(*, clip_id: str, previews: list[tuple[int, bytes]], pts: list[float],
                  total_frames: int, start: int, end: int, preview_size: tuple[int, int],
                  fps_label: str | None, key: str, ack_seq: int | None = None) -> dict | None:
    """Render the player. Returns the user's last COMMITTED selection
    {"clip_id", "start", "end", "seq"} (decoded frame indices), or None.
    Playing, scrubbing and dragging never produce a value -- only releasing
    a marker or pressing "Set START/END here" does.

    `ack_seq` is the seq of the last committed selection the server has
    applied; the player ignores selection arguments older than its own latest
    commit, so a render already in flight can never undo a newer edit."""
    urls = preview_urls(clip_id, previews)
    frames = [{"i": int(idx), "t": round(float(pts[idx]), 4), "u": u} for (idx, _), u in zip(previews, urls)]
    return _component()(
        clip_id=clip_id, frames=frames, total_frames=int(total_frames), start=int(start), end=int(end),
        width=int(preview_size[0]), height=int(preview_size[1]), fps_label=fps_label,
        ack_seq=ack_seq, key=key, default=None,
    )
