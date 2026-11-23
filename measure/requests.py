from dataclasses import dataclass
from fractions import Fraction

from measure.dwell import measure_dwell
from measure.intervals import BoundaryInterval, parse_parameters
from measure.provenance import build_provenance
from measure.qualification import qualify_common
from receive.pairing import select_pair
from receive.repository import ReceiptRepository
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


def sign(
    receipts: ReceiptRepository, reports: ReportRepository, request: AssessmentRequest,
) -> CommittedFinal:
    window, left_limit, right_limit = validate_request(request)
    pair = select_pair(receipts, request.left_receipt_id, request.right_receipt_id)
    spans = qualify_common(pair, window, left_limit, right_limit)
    dwell = measure_dwell(spans)
    provenance = build_provenance(pair, dwell)
    return finalize(reports, window, (left_limit, right_limit), dwell, provenance)
