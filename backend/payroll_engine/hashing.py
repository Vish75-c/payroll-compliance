import hashlib
import json


def canonical_json(data) -> str:
    """Stable JSON: sorted keys, no whitespace variance. Two equal inputs give equal text."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(data) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
