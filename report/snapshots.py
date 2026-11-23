import json
from dataclasses import asdict, dataclass
from fractions import Fraction

from measure.intervals import BoundaryInterval
from measure.provenance import AssessmentProvenance, EvidenceSource, SegmentProvenance
from receive.source import SourceIdentity


@dataclass(frozen=True)
class FinalSnapshot:
    window: BoundaryInterval
    thresholds: tuple[Fraction, Fraction]
    segments: tuple[BoundaryInterval, ...]
    total_seconds: Fraction
    longest_seconds: Fraction
    provenance: AssessmentProvenance
    dispatch_seconds: Fraction


@dataclass(frozen=True)
class FirstReceipt:
    receipt_id: int
    report_id: int


@dataclass(frozen=True)
class CommittedFinal:
    report_id: int
    snapshot: FinalSnapshot
    first_receipt: FirstReceipt


def _encode_interval(interval: BoundaryInterval) -> list:
    return [str(interval.start), str(interval.end), interval.left_closed, interval.right_closed]


def _decode_interval(value: list) -> BoundaryInterval:
    start, end, left_closed, right_closed = value
    return BoundaryInterval(Fraction(start), Fraction(end), left_closed, right_closed)


def _decode_source(value: dict) -> EvidenceSource:
    return EvidenceSource(value['receipt_id'], SourceIdentity(**value['source']))


def encode_snapshot(snapshot: FinalSnapshot) -> bytes:
    value = {
        'window': _encode_interval(snapshot.window),
        'thresholds': [str(threshold) for threshold in snapshot.thresholds],
        'segments': [_encode_interval(interval) for interval in snapshot.segments],
        'total_seconds': str(snapshot.total_seconds),
        'longest_seconds': str(snapshot.longest_seconds),
        'provenance': asdict(snapshot.provenance),
        'dispatch_seconds': str(snapshot.dispatch_seconds),
    }
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


def decode_snapshot(payload: bytes) -> FinalSnapshot:
    value = json.loads(payload)
    evidence = value['provenance']
    provenance = AssessmentProvenance(
        _decode_source(evidence['left']), _decode_source(evidence['right']),
        tuple(SegmentProvenance(tuple(segment['left_pats']), tuple(segment['right_pats']))
              for segment in evidence['segments']),
    )
    left, right = value['thresholds']
    return FinalSnapshot(
        _decode_interval(value['window']), (Fraction(left), Fraction(right)),
        tuple(_decode_interval(segment) for segment in value['segments']),
        Fraction(value['total_seconds']), Fraction(value['longest_seconds']), provenance,
        Fraction(value['dispatch_seconds']),
    )
