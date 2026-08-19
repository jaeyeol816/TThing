"""Mapping 은 어떤 방법으로도 영속화되지 않아야 한다 (BR-G-09).

매핑 테이블은 기호와 실제 이름의 사전이다.
유출되면 과거의 모든 감사 로그가 복호화된다.

pydantic BaseModel 이 아니라 dataclass 로 만든 이유:
pydantic 모델은 model_dump() 로 쉽게 dict 가 되고, 그 dict 가
로그·응답에 실려 나갈 수 있다.
"""

import copy
import json
import pickle

import pytest

from mesh.schemas import Mapping, PayloadEnvelope


@pytest.fixture
def mapping() -> Mapping:
    return Mapping(
        table={
            "REQ_A": "고객사 H · REQ-4412 (req-spec-2026H.md §3.2)",
            "COMP_B": "자사 SDK v3.2 토큰 정책 (auth-design.md §5)",
        }
    )


def test_json_dumps_fails(mapping):
    with pytest.raises(TypeError):
        json.dumps(mapping)


def test_pickle_fails(mapping):
    with pytest.raises(TypeError):
        pickle.dumps(mapping)


def test_deepcopy_fails(mapping):
    with pytest.raises(TypeError):
        copy.deepcopy(mapping)


def test_getstate_fails(mapping):
    with pytest.raises(TypeError):
        mapping.__getstate__()


def test_repr_does_not_leak_values(mapping):
    """로그에 실수로 찍혀도 실제 이름이 노출되지 않아야 한다."""
    r = repr(mapping)
    assert "REQ-4412" not in r
    assert "고객사" not in r
    assert "SDK v3.2" not in r
    assert "2 entries redacted" in r


def test_str_does_not_leak_values(mapping):
    assert "REQ-4412" not in str(mapping)


def test_fstring_does_not_leak_values(mapping):
    assert "REQ-4412" not in f"{mapping}"


def test_payload_envelope_has_no_mapping_field():
    """PayloadEnvelope 에 mapping 필드가 없어야 한다.
    있으면 model_dump() 가 매핑을 함께 직렬화한다."""
    fields = set(PayloadEnvelope.model_fields)
    assert "mapping" not in fields
    assert "table" not in fields


def test_payload_envelope_has_no_plaintext_field():
    """원문 필드도 없어야 한다 — 타입 수준에서 원문이 담기지 않는다."""
    fields = set(PayloadEnvelope.model_fields)
    for forbidden in ("text", "raw", "original", "originals", "chunk_text", "source_text"):
        assert forbidden not in fields, f"PayloadEnvelope 에 {forbidden} 필드가 있다"


def test_lookup_still_works(mapping):
    """차단은 직렬화만이다. 조회는 정상 동작해야 한다."""
    assert mapping.get("REQ_A") == "고객사 H · REQ-4412 (req-spec-2026H.md §3.2)"
    assert mapping.get("NOPE") is None


def test_keys_longest_first_prevents_partial_replacement():
    """<SYS_1> 과 <SYS_11> 이 함께 있을 때 긴 것을 먼저 치환해야 한다 (BR-P-04)."""
    m = Mapping(table={"<SYS_1>": "Nova", "<SYS_11>": "Legacy SSO", "<SYS_2>": "Gateway"})
    keys = m.keys_longest_first()
    assert keys.index("<SYS_11>") < keys.index("<SYS_1>")


def test_empty_factory():
    assert Mapping.empty().table == {}
