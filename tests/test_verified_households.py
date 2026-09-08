import copy
import json

import pytest

from collect_verified_households import apply_verified_households, normalize


def source():
    return {
        "DESCRIPTION": {"TNOHSH": "k-전체세대수", "WHOL_DONG_CNT": "k-전체동수"},
        "DATA": [{"apt_cd": "A10025003", "apt_nm": "마포그랑자이아파트",
                  "apt_rdn_addr": "서울특별시 마포구 대흥로 175", "tnohsh": 1248,
                  "whol_dong_cnt": 18, "mdfcn_ymd": 1788811389000}],
    }


def building(lot="806"):
    return {"key": f"마포구 대흥동 {lot} 마포그랑자이 | 84.98㎡",
            "complex_key": f"마포구 대흥동 {lot} 마포그랑자이",
            "building_name": "마포그랑자이", "households": 66}


def summary():
    rows = [building(), building("807")]
    return {"regions": [{"code": "11440:대흥동", "gu_code": "11440", "dong_name": "대흥동",
                         "all": {"addresses": rows},
                         "years": {"2026": {"addresses": copy.deepcopy(rows)},
                                   "2024": {"addresses": copy.deepcopy(rows)}}}]}


def register(tmp_path):
    doc = normalize(json.dumps(source()).encode(), "2026-09-08T22:55:27+00:00")
    path = tmp_path / "register.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path, doc


def test_reviewed_identity_matches_exact_lot_in_every_bucket(tmp_path):
    path, _ = register(tmp_path)
    data = summary()
    result = apply_verified_households(data, path)
    assert result["matched_types"] == 1
    assert result["matched_bucket_rows"] == 3
    for bucket in [data["regions"][0]["all"], *data["regions"][0]["years"].values()]:
        yes, no = bucket["addresses"]
        assert yes["households"] == 1248 and yes["legacy_households"] == 66
        assert yes["households_verified"]
        assert not yes["household_use_for_model"] and not yes["household_use_for_turnover"]
        assert yes["household_is_current_observation"]
        assert no == building("807")
    apply_verified_households(data, path)
    assert data["regions"][0]["all"]["addresses"][0]["legacy_households"] == 66


def test_same_name_elsewhere_and_changed_official_address_do_not_match(tmp_path):
    path, doc = register(tmp_path)
    data = summary()
    data["regions"][0]["gu_code"] = "11740"
    assert apply_verified_households(data, path)["matched_types"] == 0
    doc["records"][0]["road_address"] = "다른 주소"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert apply_verified_households(summary(), path)["matched_types"] == 0


def test_names_without_approved_identity_do_not_match(tmp_path):
    path, doc = register(tmp_path)
    doc["approved_matches"] = []
    path.write_text(json.dumps(doc), encoding="utf-8")
    data = summary()
    assert apply_verified_households(data, path)["matched_types"] == 0
    assert data["regions"][0]["all"]["addresses"][0]["households"] == 66


def test_scope_change_or_duplicate_official_id_is_rejected():
    data = source()
    data["DESCRIPTION"]["TNOHSH"] = "동별 세대수"
    with pytest.raises(ValueError, match="definitions changed"):
        normalize(json.dumps(data).encode(), "2026-09-08T22:55:27+00:00")
    data = source()
    data["DATA"].append(copy.deepcopy(data["DATA"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        normalize(json.dumps(data).encode(), "2026-09-08T22:55:27+00:00")
