import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class V1Envelope:
    source: dict
    body: dict


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate envelope field {key!r}')
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f'invalid JSON number {value}')


def encode_envelope(envelope: V1Envelope) -> bytes:
    content = {'version': 1, 'source': envelope.source, 'body': envelope.body}
    digest = hashlib.sha256(_canonical(content)).hexdigest()
    return _canonical({**content, 'sha256': digest})


def decode_envelope(payload: bytes) -> V1Envelope:
    if not isinstance(payload, bytes):
        raise ValueError('an issued item must be UTF-8 bytes')
    try:
        content = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant)
        if not isinstance(content, dict) or set(content) != {'version', 'source', 'body', 'sha256'}:
            raise ValueError('invalid V1 envelope fields')
        if type(content['version']) is not int or content['version'] != 1:
            raise ValueError('unsupported envelope version')
        if not isinstance(content['source'], dict) or not isinstance(content['body'], dict):
            raise ValueError('invalid envelope source or body')
        digest = content.pop('sha256')
        if digest != hashlib.sha256(_canonical(content)).hexdigest():
            raise ValueError('envelope content digest mismatch')
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('invalid envelope JSON') from exc
    return V1Envelope(content['source'], content['body'])
