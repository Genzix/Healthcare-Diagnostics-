"""
Booking lifecycle — the single source of truth for statuses and transitions.

Partner → Laboratory → Patient pipeline:

    CONFIRMED
      └─ SAMPLE_COLLECTED      Sample collector completes collection.            (partner)
          └─ SAMPLE_RECEIVED   Partner confirms the sample reached the lab.      (partner)
              └─ LAB_ANALYSIS  Laboratory starts processing the sample.          (partner)
                  └─ REPORT_GENERATED  Laboratory prepares the test report.      (partner)
                      └─ REPORT_UPLOADED  Partner uploads the final lab PDF.     (partner)
                          ├─ REPORT_VERIFIED  Authorised personnel verify it.    (partner)
                          │   └─ REPORT_READY  Released to the patient; push +
                          │       │             in-app notification are sent.   (partner)
                          │       └─ REPORT_DOWNLOADED  Patient opens it.       (patient)
                          └─ REPORT_GENERATED  Verifier sends it back for correction.

Every rule (who may move a booking, what each step requires, which steps the
patient hears about) lives here so views, services, serializers and tests all
agree. Nothing in this module touches the database.
"""
from dataclasses import dataclass, field
from typing import FrozenSet, Optional, Tuple

# ── statuses ────────────────────────────────────────────────────────────────

AWAITING_ADVANCE_PAYMENT = 'AWAITING_ADVANCE_PAYMENT'
AWAITING_PARTNER_CONFIRMATION = 'AWAITING_PARTNER_CONFIRMATION'
CONFIRMED = 'CONFIRMED'
SAMPLE_COLLECTED = 'SAMPLE_COLLECTED'
SAMPLE_RECEIVED = 'SAMPLE_RECEIVED'
LAB_ANALYSIS = 'LAB_ANALYSIS'
REPORT_GENERATED = 'REPORT_GENERATED'
REPORT_UPLOADED = 'REPORT_UPLOADED'
REPORT_VERIFIED = 'REPORT_VERIFIED'
REPORT_READY = 'REPORT_READY'
REPORT_DOWNLOADED = 'REPORT_DOWNLOADED'
TEST_COMPLETED = 'TEST_COMPLETED'  # legacy terminal state (pre-pipeline rows); never a new target
CANCELLED = 'CANCELLED'
REJECTED_BY_PARTNER = 'REJECTED_BY_PARTNER'

STATUS_CHOICES = (
    (AWAITING_ADVANCE_PAYMENT, 'Awaiting Advance Payment'),
    (AWAITING_PARTNER_CONFIRMATION, 'Awaiting Partner Confirmation'),
    (CONFIRMED, 'Confirmed'),
    (SAMPLE_COLLECTED, 'Sample Collected'),
    (SAMPLE_RECEIVED, 'Sample Received at Lab'),
    (LAB_ANALYSIS, 'Lab Analysis'),
    (REPORT_GENERATED, 'Report Generated'),
    (REPORT_UPLOADED, 'Report Uploaded'),
    (REPORT_VERIFIED, 'Report Verified'),
    (REPORT_READY, 'Report Ready'),
    (REPORT_DOWNLOADED, 'Report Downloaded'),
    (TEST_COMPLETED, 'Test Completed (legacy)'),
    (CANCELLED, 'Cancelled'),
    (REJECTED_BY_PARTNER, 'Rejected by Partner'),
)
LABELS = dict(STATUS_CHOICES)

# Ordered lab pipeline (index = progress).
PIPELINE: Tuple[str, ...] = (
    CONFIRMED,
    SAMPLE_COLLECTED,
    SAMPLE_RECEIVED,
    LAB_ANALYSIS,
    REPORT_GENERATED,
    REPORT_UPLOADED,
    REPORT_VERIFIED,
    REPORT_READY,
    REPORT_DOWNLOADED,
)

# ── groups ──────────────────────────────────────────────────────────────────

PRE_SERVICE = frozenset({AWAITING_ADVANCE_PAYMENT, AWAITING_PARTNER_CONFIRMATION, CONFIRMED})
IN_LAB = frozenset({SAMPLE_COLLECTED, SAMPLE_RECEIVED, LAB_ANALYSIS, REPORT_GENERATED, REPORT_UPLOADED, REPORT_VERIFIED})
# The patient may see / download the report only in these states.
REPORT_RELEASED = frozenset({REPORT_READY, REPORT_DOWNLOADED, TEST_COMPLETED})
# A report file must exist in these states.
REPORT_ON_FILE = frozenset({REPORT_UPLOADED, REPORT_VERIFIED, REPORT_READY, REPORT_DOWNLOADED})
VOID = frozenset({CANCELLED, REJECTED_BY_PARTNER})
COMPLETED = REPORT_RELEASED
# Once a sample is taken the booking can no longer be cancelled by the patient.
CANCELLABLE = PRE_SERVICE
# Balance can be settled from the moment the sample is collected (collector cash, UPI at centre, or online later).
BALANCE_PAYABLE = IN_LAB | REPORT_RELEASED
# Bookings the patient still has to wait on (user "upcoming" view).
ACTIVE = PRE_SERVICE | IN_LAB

# Query-string groups accepted by list endpoints: ?status=in_lab
STATUS_GROUPS = {
    'pending': {AWAITING_ADVANCE_PAYMENT, AWAITING_PARTNER_CONFIRMATION},
    'active': set(ACTIVE),
    'in_lab': set(IN_LAB),
    'report_review': {REPORT_UPLOADED, REPORT_VERIFIED},
    'completed': set(COMPLETED),
    'released': set(COMPLETED),
    'cancelled': set(VOID),
}

# Legacy / alternate spellings accepted from clients → canonical status.
ALIASES = {
    'SAMPLE_COLLECTION': SAMPLE_COLLECTED,
    'SAMPLE_DONE': SAMPLE_COLLECTED,
    'VISIT_DONE': SAMPLE_COLLECTED,
    'COLLECTED': SAMPLE_COLLECTED,

    'SAMPLE_RECEIVED_AT_LAB': SAMPLE_RECEIVED,
    'RECEIVED_AT_LAB': SAMPLE_RECEIVED,
    'RECEIVED': SAMPLE_RECEIVED,
    'AT_LAB': SAMPLE_RECEIVED,

    'IN_ANALYSIS': LAB_ANALYSIS,
    'ANALYSIS': LAB_ANALYSIS,
    'PROCESSING': LAB_ANALYSIS,
    'LAB_PROCESSING': LAB_ANALYSIS,
    'UNDER_TESTING': LAB_ANALYSIS,
    'UNDER_ANALYSIS': LAB_ANALYSIS,

    'GENERATED': REPORT_GENERATED,
    'REPORT_PREPARED': REPORT_GENERATED,

    'UPLOADED': REPORT_UPLOADED,
    'UPLOAD_REPORT': REPORT_UPLOADED,

    'VERIFIED': REPORT_VERIFIED,
    'VERIFY_REPORT': REPORT_VERIFIED,
    'REPORT_VERIFICATION': REPORT_VERIFIED,

    'READY': REPORT_READY,
    'RELEASED': REPORT_READY,
    'RELEASE': REPORT_READY,
    'RELEASE_REPORT': REPORT_READY,

    'DOWNLOADED': REPORT_DOWNLOADED,
}


def normalize(value) -> str:
    """Any client spelling ("sample received at lab", "verified") → canonical status."""
    if not value:
        return ''
    s = str(value).strip().upper().replace(' ', '_').replace('-', '_')
    return ALIASES.get(s, s)


# ── transitions ─────────────────────────────────────────────────────────────

PARTNER = 'PARTNER'
PATIENT = 'PATIENT'
ADMIN = 'ADMIN'
SYSTEM = 'SYSTEM'


@dataclass(frozen=True)
class Step:
    """Metadata for entering `target`."""
    target: str
    actors: FrozenSet[str]
    action_label: str                 # what the button says
    description: str                  # what the step means
    requires_report_file: bool = False
    requires_verifier: bool = False
    requires_notes: bool = False
    # Patient notification on entering this state (None = internal step, patient not told).
    notify_type: Optional[str] = None
    notify_title: Optional[str] = None
    notify_message: Optional[str] = None   # str.format(booking_id=, partner=)
    push: bool = False


_PARTNER_ADMIN = frozenset({PARTNER, ADMIN})

STEPS = {
    SAMPLE_COLLECTED: Step(
        SAMPLE_COLLECTED, _PARTNER_ADMIN, 'Mark Sample Collected',
        'Sample collector completes collection.',
        notify_type='SAMPLE_COLLECTED',
        notify_title='Sample collected',
        notify_message='Your sample for booking {booking_id} has been collected by {partner}. It is on its way to the laboratory.',
    ),
    SAMPLE_RECEIVED: Step(
        SAMPLE_RECEIVED, _PARTNER_ADMIN, 'Confirm Received at Lab',
        'Partner confirms that the sample has reached the laboratory.',
        notify_type='SAMPLE_RECEIVED',
        notify_title='Sample received at the lab',
        notify_message='Your sample for booking {booking_id} has reached the {partner} laboratory.',
    ),
    LAB_ANALYSIS: Step(
        LAB_ANALYSIS, _PARTNER_ADMIN, 'Start Lab Analysis',
        'Laboratory starts processing the sample.',
        notify_type='LAB_ANALYSIS',
        notify_title='Lab analysis in progress',
        notify_message='The laboratory has started processing your sample for booking {booking_id}.',
    ),
    REPORT_GENERATED: Step(
        REPORT_GENERATED, _PARTNER_ADMIN, 'Mark Report Generated',
        'Laboratory prepares the test report.',
    ),
    REPORT_UPLOADED: Step(
        REPORT_UPLOADED, _PARTNER_ADMIN, 'Upload Report',
        'Partner uploads the final laboratory-generated PDF.',
        requires_report_file=True,
    ),
    REPORT_VERIFIED: Step(
        REPORT_VERIFIED, _PARTNER_ADMIN, 'Verify Report',
        'Authorised laboratory personnel verify the report.',
        requires_verifier=True,
    ),
    REPORT_READY: Step(
        REPORT_READY, _PARTNER_ADMIN, 'Release to Patient',
        'Report becomes available to the patient. Push and in-app notifications are sent.',
        notify_type='REPORT_READY',
        notify_title='Your report is ready',
        notify_message='Your verified report for booking {booking_id} from {partner} is ready. Open the app to view or download it.',
        push=True,
    ),
    REPORT_DOWNLOADED: Step(
        REPORT_DOWNLOADED, frozenset({PATIENT}), 'Download Report',
        'Patient views or downloads the report.',
    ),
}

# Verifier rejects an uploaded report → back to REPORT_GENERATED for correction.
SEND_BACK = Step(
    REPORT_GENERATED, _PARTNER_ADMIN, 'Send Back for Correction',
    'Verifier found an issue; the lab must correct and re-upload the report.',
    requires_notes=True,
)

TRANSITIONS = {
    CONFIRMED: (SAMPLE_COLLECTED,),
    SAMPLE_COLLECTED: (SAMPLE_RECEIVED,),
    SAMPLE_RECEIVED: (LAB_ANALYSIS,),
    LAB_ANALYSIS: (REPORT_GENERATED,),
    REPORT_GENERATED: (REPORT_UPLOADED,),
    # Re-upload (replace file) is a self-transition; send-back returns to GENERATED.
    REPORT_UPLOADED: (REPORT_VERIFIED, REPORT_UPLOADED, REPORT_GENERATED),
    REPORT_VERIFIED: (REPORT_READY,),
    REPORT_READY: (REPORT_DOWNLOADED,),
    TEST_COMPLETED: (REPORT_DOWNLOADED,),
}


def step_for(current: str, target: str) -> Optional[Step]:
    if target not in TRANSITIONS.get(current, ()):
        return None
    if current == REPORT_UPLOADED and target == REPORT_GENERATED:
        return SEND_BACK
    return STEPS.get(target)


@dataclass
class TransitionCheck:
    ok: bool
    step: Optional[Step] = None
    error: str = ''
    code: str = ''
    allowed: Tuple[str, ...] = field(default_factory=tuple)


def check(current: str, target: str, actor: str) -> TransitionCheck:
    allowed = tuple(t for t in TRANSITIONS.get(current, ()) if actor in (step_for(current, t) or STEPS[t]).actors)
    if current in VOID:
        return TransitionCheck(False, error=f"Booking is {LABELS.get(current, current).lower()}; its status can no longer change.", code='booking_closed')
    if current in (AWAITING_ADVANCE_PAYMENT, AWAITING_PARTNER_CONFIRMATION):
        return TransitionCheck(False, error='The booking must be confirmed before the lab workflow can start.', code='not_confirmed')
    step = step_for(current, target)
    if step is None:
        nxt = ', '.join(LABELS.get(t, t) for t in allowed) or 'none'
        return TransitionCheck(
            False,
            error=f"Cannot move from {LABELS.get(current, current)} to {LABELS.get(target, target)}. Next allowed step: {nxt}.",
            code='invalid_transition',
            allowed=allowed,
        )
    if actor not in step.actors:
        return TransitionCheck(False, error=f"{actor.title()} accounts cannot perform '{step.action_label}'.", code='forbidden_actor', allowed=allowed)
    return TransitionCheck(True, step=step, allowed=allowed)


def next_steps(current: str, actor: str = PARTNER):
    """Actions the given actor can take right now — drives the UI buttons."""
    out = []
    for t in TRANSITIONS.get(current, ()):
        s = step_for(current, t)
        if s and actor in s.actors:
            out.append({
                'status': s.target,
                'label': s.action_label,
                'description': s.description,
                'requires_report_file': s.requires_report_file,
                'requires_verifier': s.requires_verifier,
                'requires_notes': s.requires_notes,
                'kind': 'reupload' if (current == t) else 'send_back' if s is SEND_BACK else 'advance',
            })
    return out


def progress(current: str) -> dict:
    """Position in the pipeline for progress bars."""
    if current == TEST_COMPLETED:
        current = REPORT_READY
    idx = PIPELINE.index(current) if current in PIPELINE else -1
    return {'index': idx, 'total': len(PIPELINE), 'percent': round(100 * idx / (len(PIPELINE) - 1)) if idx >= 0 else 0}
