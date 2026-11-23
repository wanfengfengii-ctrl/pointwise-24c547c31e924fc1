from dataclasses import dataclass

from receive.repository import ReceiptRepository, ReceivedItem


@dataclass(frozen=True)
class ReceiptPair:
    left: ReceivedItem
    right: ReceivedItem


def select_pair(repository: ReceiptRepository, left_id: int, right_id: int) -> ReceiptPair:
    left = repository.get(left_id)
    right = repository.get(right_id)
    if left.source.trial_id != right.source.trial_id:
        raise ValueError('receipts belong to different trials')
    if left.source.head == right.source.head:
        raise ValueError('receipts belong to the same head')
    return ReceiptPair(left, right)
