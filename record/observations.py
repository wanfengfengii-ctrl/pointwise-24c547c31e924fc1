from dataclasses import dataclass
from fractions import Fraction


def as_rational(value: object, field: str) -> Fraction:
    if type(value) is not int and not isinstance(value, (str, Fraction)):
        raise ValueError(f'{field} must be an exact rational')
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f'{field} must be an exact rational') from exc


def require_label(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonblank string')
    value.encode('utf-8')
    return value


def require_number(value: object, field: str) -> int:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError(f'{field} must be a positive ledger number')
    return value


@dataclass(frozen=True)
class Observation:
    pat_id: str
    seconds: Fraction
    celsius: Fraction | None

    def __post_init__(self) -> None:
        require_label(self.pat_id, 'pat_id')
        object.__setattr__(self, 'seconds', as_rational(self.seconds, 'seconds'))
        if self.celsius is not None:
            object.__setattr__(self, 'celsius', as_rational(self.celsius, 'celsius'))


def make_observation(pat_id: str, seconds: object, celsius: object) -> Observation:
    return Observation(pat_id, seconds, celsius)
