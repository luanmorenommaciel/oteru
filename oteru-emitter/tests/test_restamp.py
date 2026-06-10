"""Testes de ``oteru_emitter.rewrite.restamp`` (tempo + rotação de IDs)."""

from __future__ import annotations

import json

from oteru_emitter.rewrite.restamp import restamp
from oteru_emitter.sources.replay import load_batches

ROTATE = ("session.id", "prompt.id", "request_id")
NOW_NS = 2_000_000_000_000_000_000

SESSION_OLD = "3df407ff-be89-4272-ba1b-ee8b1fb588b8"
PROMPT_OLD_1 = "aab6e0c2-7c9d-4f1e-9b3a-2d4e5f6a7b8c"
PROMPT_OLD_2 = "bcd7f1d3-8dae-4a2f-8c4b-3e5f6a7b8c9d"
REQ_OLD_1 = "req_011AAAAAAAAAAAAAAAAAAAAA"
REQ_OLD_2 = "req_022BBBBBBBBBBBBBBBBBBBBB"


def _dump(batches) -> list[str]:
    return [json.dumps(b.payload, sort_keys=True) for b in batches]


def _coletar(batches, attr_values, key) -> set[str]:
    valores: set[str] = set()
    for b in batches:
        valores.update(attr_values(b.payload, key))
    return valores


def test_mesma_seed_e_now_ns_reproduz_byte_a_byte(tiny_path):
    a = load_batches(str(tiny_path))
    b = load_batches(str(tiny_path))
    restamp(a, rotate_keys=ROTATE, seed=42, now_ns=NOW_NS)
    restamp(b, rotate_keys=ROTATE, seed=42, now_ns=NOW_NS)
    assert _dump(a) == _dump(b)


def test_rotacao_consistente_mesmo_valor_antigo(tiny_batches, attr_values):
    restamp(tiny_batches, rotate_keys=ROTATE, seed=1, now_ns=NOW_NS)
    novos = _coletar(tiny_batches, attr_values, "session.id")
    # o mesmo session.id antigo aparece nas 3 batches -> 1 único valor novo
    assert len(novos) == 1
    assert SESSION_OLD not in novos


def test_ids_distintos_continuam_distintos(tiny_batches, attr_values):
    restamp(tiny_batches, rotate_keys=ROTATE, seed=1, now_ns=NOW_NS)
    prompts = _coletar(tiny_batches, attr_values, "prompt.id")
    assert len(prompts) == 2
    assert not prompts & {PROMPT_OLD_1, PROMPT_OLD_2}


def test_identidade_do_principal_nunca_rotacionada(tiny_batches, attr_values):
    restamp(tiny_batches, rotate_keys=ROTATE, seed=1, now_ns=NOW_NS)
    assert _coletar(tiny_batches, attr_values, "user.email") == {"user@example.com"}
    assert _coletar(tiny_batches, attr_values, "user.id") == {"0" * 64}
    assert _coletar(tiny_batches, attr_values, "organization.id") == {
        "11111111-1111-1111-1111-111111111111"
    }


def test_sem_shift_sem_rotacao_e_identidade(tiny_path):
    originais = _dump(load_batches(str(tiny_path)))
    batches = load_batches(str(tiny_path))
    offset = restamp(batches, shift_time=False, rotate_keys=())
    assert offset == 0
    assert _dump(batches) == originais


def test_req_mantem_o_formato(tiny_batches, attr_values):
    restamp(tiny_batches, rotate_keys=ROTATE, seed=1, now_ns=NOW_NS)
    novos = _coletar(tiny_batches, attr_values, "request_id")
    assert len(novos) == 2
    assert not novos & {REQ_OLD_1, REQ_OLD_2}
    for novo in novos:
        assert novo.startswith("req_")
        assert len(novo) == len(REQ_OLD_1)


def test_offset_e_now_menos_o_menor_anchor(tiny_batches):
    menor_anchor = min(b.anchor_ns for b in tiny_batches if b.anchor_ns is not None)
    offset = restamp(tiny_batches, now_ns=NOW_NS)
    assert offset == NOW_NS - menor_anchor


def test_duracao_da_metrica_preservada(tiny_path):
    def duracao(batches) -> int:
        sum_ = batches[2].payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]
        ponto = sum_["dataPoints"][0]
        return int(ponto["timeUnixNano"]) - int(ponto["startTimeUnixNano"])

    batches = load_batches(str(tiny_path))
    antes = duracao(batches)
    restamp(batches, now_ns=NOW_NS)
    assert duracao(batches) == antes
