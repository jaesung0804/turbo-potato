from types import SimpleNamespace
import oracledb
from sqlalchemy import bindparam, Text
from research_backend.database import _small_observation_binds


def binding(payloads, table='observations', isinsert=True):
    bind = bindparam('payload', type_=Text())
    sizes = {bind: oracledb.DB_TYPE_CLOB}
    context = SimpleNamespace(isinsert=isinsert, compiled=SimpleNamespace(
        statement=SimpleNamespace(table=SimpleNamespace(name=table)), bind_names={bind: 'payload'}))
    _small_observation_binds(sizes, None, '', payloads, context)
    return sizes


def test_small_json_uses_direct_string_binds_only_for_observation_inserts():
    rows = [{'payload': '{"name":"한글"}'}, {'payload': '{}' }]
    assert binding(rows) == {}
    assert binding(rows, table='research_records')
    assert binding(rows, isinsert=False)


def test_large_multibyte_null_and_unknown_parameters_retain_clob_binding():
    assert binding([{'payload': '가' * 1334}])
    assert binding([{'payload': 'x' * 4001}])
    assert binding([{'payload': None}])
    assert binding([{'payload': ''}])
    assert binding([('unknown-positional-input',)])
    assert binding([{'payload': '가' * 1333}]) == {}
