from dataclasses import dataclass
from fractions import Fraction

from measure.dwell import measure_dwell
from measure.intervals import BoundaryInterval, parse_parameters
from measure.provenance import (
    WindowAssessmentProvenance, WindowSegmentProvenance, build_provenance,
)
from measure.qualification import qualify_common
from measure.window_dwell import WindowDwellResult, measure_window_dwell
from measure.windows import WindowClaim, qualify_windows, read_window_claim
from receive.envelope import decode_envelope
from receive.pairing import select_pair
from receive.repository import ReceiptRepository, ReceivedItem
from record.observations import require_number
from report.finalize import finalize
from report.repository import ReportRepository
from report.snapshots import CommittedFinal


@dataclass(frozen=True)
class AssessmentRequest:
    left_receipt_id: int
    right_receipt_id: int
    window: tuple[object, object]
    left_threshold: object
    right_threshold: object


def validate_request(request: AssessmentRequest) -> tuple[BoundaryInterval, Fraction, Fraction]:
    require_number(request.left_receipt_id, 'left receipt ID')
    require_number(request.right_receipt_id, 'right receipt ID')
    return parse_parameters(request.window, request.left_threshold, request.right_threshold)


def is_windowed_payload(payload: bytes) -> bool:
    try:
        return 'window' in decode_envelope(payload).body
    except ValueError:
        return False


def _require_whole_item(item: ReceivedItem) -> None:
    if is_windowed_payload(item.payload):
        raise ValueError('windowed issues cannot be assessed as whole-item receipts')


def sign(
    receipts: ReceiptRepository, reports: ReportRepository, request: AssessmentRequest,
) -> CommittedFinal:
    window, left_limit, right_limit = validate_request(request)
    pair = select_pair(receipts, request.left_receipt_id, request.right_receipt_id)
    _require_whole_item(pair.left)
    _require_whole_item(pair.right)
    spans = qualify_common(pair, window, left_limit, right_limit)
    dwell = measure_dwell(spans)
    provenance = build_provenance(pair, dwell)
    return finalize(reports, window, (left_limit, right_limit), dwell, provenance)


@dataclass(frozen=True)
class WindowAssessmentRequest:
    left_receipt_ids: tuple[int, ...]
    right_receipt_ids: tuple[int, ...]
    window: tuple[object, object]
    left_threshold: object
    right_threshold: object

    def __init__(
        self, left_receipt_ids, right_receipt_ids, window, left_threshold, right_threshold,
    ) -> None:
        if not isinstance(left_receipt_ids, (list, tuple)) or not left_receipt_ids:
            raise ValueError('left receipt group must be a nonempty list or tuple')
        if not isinstance(right_receipt_ids, (list, tuple)) or not right_receipt_ids:
            raise ValueError('right receipt group must be a nonempty list or tuple')
        left_ids, right_ids = tuple(left_receipt_ids), tuple(right_receipt_ids)
        for identifier in (*left_ids, *right_ids):
            require_number(identifier, 'receipt ID')
        if len(set(left_ids)) != len(left_ids) or len(set(right_ids)) != len(right_ids):
            raise ValueError('receipt IDs must not repeat within a group')
        if set(left_ids) & set(right_ids):
            raise ValueError('left and right receipt groups must not share receipts')
        object.__setattr__(self, 'left_receipt_ids', left_ids)
        object.__setattr__(self, 'right_receipt_ids', right_ids)
        object.__setattr__(self, 'window', window)
        object.__setattr__(self, 'left_threshold', left_threshold)
        object.__setattr__(self, 'right_threshold', right_threshold)


def _load_claims(
    receipts: ReceiptRepository, identifiers: tuple[int, ...],
) -> tuple[WindowClaim, ...]:
    claims = []
    for identifier in identifiers:
        claim = read_window_claim(receipts.get(identifier))
        claims.append(claim)
    return tuple(claims)


def _validate_heads(
    left_claims: tuple[WindowClaim, ...], right_claims: tuple[WindowClaim, ...],
) -> None:
    left_heads = {claim.receipt.source.head for claim in left_claims}
    right_heads = {claim.receipt.source.head for claim in right_claims}
    if len(left_heads) != 1 or len(right_heads) != 1:
        raise ValueError('each receipt group must cover a single head')
    if left_heads == right_heads:
        raise ValueError('the two groups must cover different heads')
    trials = {claim.receipt.source.trial_id for claim in (*left_claims, *right_claims)}
    if len(trials) != 1:
        raise ValueError('windowed assessment requires receipts of a single trial')


def _build_window_provenance(
    left_claims: tuple[WindowClaim, ...],
    right_claims: tuple[WindowClaim, ...],
    dwell: WindowDwellResult,
) -> WindowAssessmentProvenance:
    segments = tuple(
        WindowSegmentProvenance(
            tuple(
                (identifier, tuple(sorted(segment.left.get(identifier, ()))))
                for identifier in (claim.receipt.receipt_id for claim in left_claims)
                if identifier in segment.left
            ),
            tuple(
                (identifier, tuple(sorted(segment.right.get(identifier, ()))))
                for identifier in (claim.receipt.receipt_id for claim in right_claims)
                if identifier in segment.right
            ),
        )
        for segment in dwell.segments
    )
    return WindowAssessmentProvenance(
        tuple(claim.evidence_source for claim in left_claims),
        tuple(claim.evidence_source for claim in right_claims),
        segments,
    )


def sign_windows(
    receipts: ReceiptRepository, reports: ReportRepository, request: WindowAssessmentRequest,
) -> CommittedFinal:
    window, left_limit, right_limit = parse_parameters(
        request.window, request.left_threshold, request.right_threshold,
    )
    left_claims = _load_claims(receipts, request.left_receipt_ids)
    right_claims = _load_claims(receipts, request.right_receipt_ids)
    _validate_heads(left_claims, right_claims)
    spans = qualify_windows(left_claims, right_claims, window, left_limit, right_limit)
    dwell = measure_window_dwell(spans)
    provenance = _build_window_provenance(left_claims, right_claims, dwell)
    return finalize(reports, window, (left_limit, right_limit), dwell, provenance)
