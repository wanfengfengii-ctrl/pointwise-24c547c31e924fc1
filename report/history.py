from report.repository import ReportRepository
from report.snapshots import CommittedFinal


def retrieve(repository: ReportRepository, report_id: int) -> CommittedFinal:
    return repository.get(report_id)
