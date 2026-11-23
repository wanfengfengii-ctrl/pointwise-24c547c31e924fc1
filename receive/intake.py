from dataclasses import dataclass

from receive.envelope import decode_envelope
from receive.repository import ReceiptRepository
from receive.source import validate_source


@dataclass(frozen=True)
class IntakeResult:
    receipt_id: int | None
    reason: str | None

    @property
    def accepted(self) -> bool:
        return self.receipt_id is not None


def intake(repository: ReceiptRepository, payload: bytes) -> IntakeResult:
    try:
        envelope = decode_envelope(payload)
        source = validate_source(envelope)
    except ValueError as exc:
        reason = str(exc)
        repository.record_rejection(reason, payload if isinstance(payload, bytes) else None)
        return IntakeResult(None, reason)
    received = repository.register(payload, source)
    return IntakeResult(received.receipt_id, None)
