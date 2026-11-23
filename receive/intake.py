from dataclasses import dataclass
from fractions import Fraction

from measure.intervals import BoundaryInterval
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


def _validate_window_declaration(body: dict) -> None:
    if 'window' not in body:
        return
    window = body['window']
    if not isinstance(window, list) or len(window) != 2 or not all(
        isinstance(bound, str) for bound in window
    ):
        raise ValueError('invalid window declaration')
    try:
        start, end = Fraction(window[0]), Fraction(window[1])
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError('invalid window declaration') from exc
    BoundaryInterval(start, end)


def intake(repository: ReceiptRepository, payload: bytes) -> IntakeResult:
    try:
        envelope = decode_envelope(payload)
        source = validate_source(envelope)
        _validate_window_declaration(envelope.body)
    except ValueError as exc:
        reason = str(exc)
        repository.record_rejection(reason, payload if isinstance(payload, bytes) else None)
        return IntakeResult(None, reason)
    received = repository.register(payload, source)
    return IntakeResult(received.receipt_id, None)
