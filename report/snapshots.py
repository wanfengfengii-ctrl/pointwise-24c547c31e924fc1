import json
from dataclasses import asdict, dataclass
from fractions import Fraction

from measure.intervals import BoundaryInterval
from measure.provenance import (
    AssessmentProvenance, EvidenceSource, SegmentProvenance,
    WindowAssessmentProvenance, WindowSegmentProvenance,
)
from receive.source import SourceIdentity


@dataclass(frozen=True)
class FinalSnapshot:
    window: BoundaryInterval
    thresholds: tuple[Fraction, Fraction]
    segments: tuple[BoundaryInterval, ...]
    total_seconds: Fraction
    longest_seconds: Fraction
    provenance: AssessmentProvenance | WindowAssessmentProvenance
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


def _encode_entry(entry: tuple[int, tuple[str, ...]]) -> list:
    receipt_id, pats = entry
    return [receipt_id, list(pats)]


def _decode_entry(value: list) -> tuple[int, tuple[str, ...]]:
    receipt_id, pats = value
    return receipt_id, tuple(pats)


def encode_snapshot(snapshot: FinalSnapshot) -> bytes:
    provenance = snapshot.provenance
    if isinstance(provenance, WindowAssessmentProvenance):
        provenance_value = {
            'kind': 'window',
            'left': [asdict(source) for source in provenance.left],
            'right': [asdict(source) for source in provenance.right],
            'segments': [
                {'left': [_encode_entry(entry) for entry in segment.left],
                 'right': [_encode_entry(entry) for entry in segment.right]}
                for segment in provenance.segments
            ],
        }
    else:
        provenance_value = asdict(provenance)
    value = {
        'window': _encode_interval(snapshot.window),
        'thresholds': [str(threshold) for threshold in snapshot.thresholds],
        'segments': [_encode_interval(interval) for interval in snapshot.segments],
        'total_seconds': str(snapshot.total_seconds),
        'longest_seconds': str(snapshot.longest_seconds),
        'provenance': provenance_value,
        'dispatch_seconds': str(snapshot.dispatch_seconds),
    }
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _decode_provenance(value: dict) -> AssessmentProvenance | WindowAssessmentProvenance:
    if value.get('kind') == 'window':
        return WindowAssessmentProvenance(
            tuple(_decode_source(source) for source in value['left']),
            tuple(_decode_source(source) for source in value['right']),
            tuple(
                WindowSegmentProvenance(
                    tuple(_decode_entry(entry) for entry in segment['left']),
                    tuple(_decode_entry(entry) for entry in segment['right']),
                )
                for segment in value['segments']
            ),
        )
    return AssessmentProvenance(
        _decode_source(value['left']), _decode_source(value['right']),
        tuple(SegmentProvenance(tuple(segment['left_pats']), tuple(segment['right_pats']))
              for segment in value['segments']),
    )


def decode_snapshot(payload: bytes) -> FinalSnapshot:
    value = json.loads(payload)
    provenance = _decode_provenance(value['provenance'])
    left, right = value['thresholds']
    return FinalSnapshot(
        _decode_interval(value['window']), (Fraction(left), Fraction(right)),
        tuple(_decode_interval(segment) for segment in value['segments']),
        Fraction(value['total_seconds']), Fraction(value['longest_seconds']), provenance,
        Fraction(value['dispatch_seconds']),
    )
