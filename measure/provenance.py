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


@dataclass(frozen=True)
class WindowSegmentProvenance:
    # Each side lists (receipt_id, pats) in the same order as the request group.
    left: tuple[tuple[int, tuple[str, ...]], ...]
    right: tuple[tuple[int, tuple[str, ...]], ...]


@dataclass(frozen=True)
class WindowAssessmentProvenance:
    left: tuple[EvidenceSource, ...]
    right: tuple[EvidenceSource, ...]
    segments: tuple[WindowSegmentProvenance, ...]
