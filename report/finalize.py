from fractions import Fraction

from measure.dwell import DwellResult
from measure.intervals import BoundaryInterval
from measure.provenance import AssessmentProvenance
from report.repository import ReportRepository
from report.snapshots import CommittedFinal, FinalSnapshot


def finalize(
    repository: ReportRepository, window: BoundaryInterval,
    thresholds: tuple[Fraction, Fraction], dwell: DwellResult, provenance: AssessmentProvenance,
) -> CommittedFinal:
    snapshot = FinalSnapshot(
        window, thresholds, tuple(segment.interval for segment in dwell.segments),
        dwell.total_seconds, dwell.longest_seconds, provenance, dwell.longest_seconds,
    )
    return repository.commit_final(snapshot)
