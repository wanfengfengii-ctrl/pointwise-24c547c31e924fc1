from dataclasses import dataclass

from measure.dwell import DwellResult
from receive.pairing import ReceiptPair
from receive.source import SourceIdentity


@dataclass(frozen=True)
class EvidenceSource:
    receipt_id: int
    source: SourceIdentity


@dataclass(frozen=True)
class SegmentProvenance:
    left_pats: tuple[str, ...]
    right_pats: tuple[str, ...]


@dataclass(frozen=True)
class AssessmentProvenance:
    left: EvidenceSource
    right: EvidenceSource
    segments: tuple[SegmentProvenance, ...]


def build_provenance(pair: ReceiptPair, dwell: DwellResult) -> AssessmentProvenance:
    return AssessmentProvenance(
        EvidenceSource(pair.left.receipt_id, pair.left.source),
        EvidenceSource(pair.right.receipt_id, pair.right.source),
        tuple(SegmentProvenance(segment.left_pats, segment.right_pats) for segment in dwell.segments),
    )
