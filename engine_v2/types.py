"""BL-008C Phase 1 — typed structures for the new reconstruction pipeline.

Every measurement carries its own definition, units, coordinate frame, and
quality status so a consumer (or a future reviewer) never has to guess what
a number means or whether it's trustworthy. Nothing here computes anything;
this module only defines the shapes other engine_v2 modules fill in.

SCHEMA_VERSION bumps whenever a field's meaning changes. A ReferenceRecordV2
with a different SCHEMA_VERSION must never be silently compared against
population statistics built from a different version (see pipeline.py).
"""
from __future__ import annotations

import dataclasses
import enum
from typing import Optional

SCHEMA_VERSION = "engine_v2.0"


class Quality(str, enum.Enum):
    """How much a consumer should trust a given measurement."""
    OK = "ok"                      # directly observed, high confidence
    DEGRADED = "degraded"          # observed but with a known limitation
    SUPPRESSED = "suppressed"      # not reported -- see suppression_reason
    UNAVAILABLE = "unavailable"    # never attempted (e.g. requires 3D)


class EventStatus(str, enum.Enum):
    """BL-008C.1 Section 1 -- operational status of a detected event.
    Separate from Quality (which describes a MEASUREMENT's trust level):
    this describes whether the EVENT ITSELF could be pinned down at all.
    A Measurement that depends on an event with status other than OBSERVED
    must be suppressed (see pipeline.py gating) -- an ESTIMATED_INTERVAL or
    AMBIGUOUS event is not a safe input to a point-in-time measurement."""
    OBSERVED = "observed"                       # single frame, high-confidence signal, no competing candidates
    ESTIMATED_INTERVAL = "estimated_interval"   # plausible but not pinpointed -- see earliest/latest
    AMBIGUOUS = "ambiguous"                     # multiple comparably-strong candidates, no principled way to pick one
    NOT_OBSERVABLE = "not_observable"           # required visual evidence is missing or never present in this footage


@dataclasses.dataclass
class EventCandidate:
    """One detected event, with its evidentiary basis -- the Section-1/4
    requirement that events can be OBSERVED / ESTIMATED_INTERVAL / AMBIGUOUS
    / NOT_OBSERVABLE rather than forced into a single frame number."""
    status: "EventStatus"
    best_estimate_frame: Optional[int]        # only meaningful if status != NOT_OBSERVABLE
    earliest_plausible_frame: Optional[int]
    latest_plausible_frame: Optional[int]
    supporting_signal: str                     # e.g. "forward_extension_max", "ankle_velocity_landed"
    candidate_frames: list[int]                 # all local-extrema candidates considered
    rejected_candidates: list[int]              # candidates considered and rejected
    evidence_note: str
    failure_reason: Optional[str] = None        # set when status is AMBIGUOUS or NOT_OBSERVABLE


class CameraClass(str, enum.Enum):
    """Explicit camera-angle classification. Replaces the overloaded legacy
    'camera_view' field (see BL-008A/B finding: that field actually stores
    camera_view.hip_to_torso_ratio_stance, a different, deliberate concept;
    this is the thing the validation tooling's camera_view enum ACTUALLY
    means, now attached to production records too)."""
    PROFILE = "profile"              # true side-on, perpendicular to plate
    THREE_QUARTER = "three_quarter"  # angled, partial occlusion likely
    FRONT = "front"                  # facing camera, not usable for most 2D side-plane measurements
    UNKNOWN = "unknown"              # not yet classified -- do not assume profile


@dataclasses.dataclass
class FrameTimingReport:
    """Output of decode.py. Describes what we actually know about this
    clip's temporal structure -- never what we assume."""
    container_declared_fps: float
    actual_avg_fps: float             # frame_count / duration, from real PTS
    fps_disagreement_pct: float       # |declared - actual| / declared * 100
    frame_count: int
    duration_s: float
    pts_available: bool               # did we get real per-frame presentation timestamps?
    duplicate_frame_pct: float        # % of frames that are near-identical to their predecessor
    likely_edited_playback: bool      # heuristic flag, NOT proof -- see decode.py docstring
    original_capture_rate_known: bool  # True only if independently confirmed (metadata, user input, etc.)
    notes: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class LandmarkFrame:
    """One frame's pose-extraction result. Coordinates are normalized
    image-space [0,1] x [0,1], origin top-left, as mediapipe returns them --
    NOT real-world units, NOT 3D."""
    frame_index: int
    pts_s: Optional[float]            # presentation timestamp in seconds, if known
    landmarks: Optional[dict]         # {landmark_name: (x, y, visibility)} or None if no pose found
    mean_visibility: float            # 0..1, mediapipe's own confidence signal


@dataclasses.dataclass
class TrackingQualityReport:
    total_frames: int
    frames_with_pose: int
    frames_missing_pose: int
    mean_visibility: float
    low_visibility_frame_pct: float   # % of frames below a visibility threshold
    continuity_breaks: int            # count of gaps where pose was lost then reacquired


@dataclasses.dataclass
class SwingSegment:
    """A candidate single-swing interval within a (possibly longer,
    possibly multi-swing) source clip."""
    start_frame: int
    end_frame: int
    peak_motion_frame: int            # frame of maximum wrist-speed proxy, used only to locate the event
    motion_energy_score: float        # relative, unitless -- for ranking candidate segments against each other
    manually_reviewed: bool = False
    reviewer_note: Optional[str] = None


@dataclasses.dataclass
class SwingEvents:
    """Detected phase boundaries, as FRAME INDICES into the clip's own
    timebase. Convert to seconds only via that clip's own PTS -- never
    assume a nominal frame rate."""
    load_start_frame: Optional[int]
    foot_plant_frame: Optional[int]
    launch_frame: Optional[int]
    contact_frame: Optional[int]
    finish_frame: Optional[int]
    detector_confidence: float        # 0..1, this run's own self-reported confidence
    ground_truth_frame: Optional[dict] = None  # filled in only for manually-annotated pilot clips
    # BL-008C.1: contact is the event that failed manual review hardest and
    # most unpredictably across the pilot (see validation/BL008C1_EVENT_DETECTION.md).
    # contact_frame above is kept for backward compatibility with measure.py's
    # existing call sites, but is now ALWAYS derived from contact_candidate
    # below, and is None whenever contact_candidate.status != OBSERVED --
    # callers must not treat a present contact_frame as self-evidently
    # trustworthy without also checking contact_candidate.status.
    contact_candidate: Optional["EventCandidate"] = None


@dataclasses.dataclass
class Measurement:
    """One biomechanical measurement, fully self-describing. This is the
    core Phase-1 requirement: never report a bare number."""
    name: str
    definition: str
    units: str                        # e.g. "ratio [0,1]", "normalized px", "degrees", "ms (real-time, verified)"
    coordinate_frame: str              # e.g. "image-space 2D, normalized", "phase-relative [plant,contact]"
    required_observations: list[str]   # landmark names / events this depends on
    value: Optional[float]
    quality: Quality
    source_frame_interval: Optional[tuple[int, int]]  # (start_frame, end_frame) it was computed over
    uncertainty_note: str
    suppression_reason: Optional[str] = None


@dataclasses.dataclass
class ReferenceRecordV2:
    schema_version: str
    source_file: str
    source_sha256: str
    player_name: Optional[str]
    handedness: Optional[str]
    camera_class: CameraClass
    camera_class_confidence: float
    timing: FrameTimingReport
    tracking: TrackingQualityReport
    segment: SwingSegment
    events: SwingEvents
    measurements: list[Measurement]
    processing_notes: list[str]
    accepted: bool                     # False if this profile should NOT enter the comparison library
    rejection_reason: Optional[str] = None
    # BL-008C.3: contact-independent window + its measurements, populated
    # regardless of whether `events`/`measurements` (contact-dependent)
    # succeeded. `accepted` above still gates the CONTACT-DEPENDENT path
    # only; window_accepted below is the separate, contact-independent
    # eligibility flag (explicit Phase A/Phase 5-of-BL008C.1 requirement:
    # "separate video quality, pose quality, event quality, measurement
    # quality, and overall comparison eligibility").
    window: Optional[dict] = None
    window_measurements: list = dataclasses.field(default_factory=list)
    window_accepted: bool = False
    window_rejection_reason: Optional[str] = None

    def to_json(self) -> dict:
        d = dataclasses.asdict(self)
        return d
