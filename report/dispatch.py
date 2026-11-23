from dataclasses import dataclass
from fractions import Fraction

from report.repository import ReportRepository


@dataclass(frozen=True)
class DispatchBasis:
    report_id: int
    longest_seconds: Fraction


def read_dispatch_basis(repository: ReportRepository, report_id: int) -> DispatchBasis:
    report = repository.get(report_id)
    return DispatchBasis(report.report_id, report.snapshot.dispatch_seconds)
