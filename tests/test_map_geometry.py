import json
import pytest
from shapely.geometry import box, mapping, MultiPolygon, shape, Point
from build_public_site import light_map


def test_map_dissolves_internal_dong_edges_but_keeps_districts(tmp_path):
    dong_a=box(127,37,127.01,37.01)
    dong_b=box(127.01,37,127.02,37.01)
    other=box(127.02,37,127.03,37.01)
    features=[{'type':'Feature','properties':{'SIDO_NM':'서울특별시','ADM_SGG_CD':str(i)},'geometry':mapping(g)}
              for i,g in enumerate([MultiPolygon([dong_a,dong_b]),other])]
    path=tmp_path/'map.json'
    path.write_text(json.dumps({'type':'FeatureCollection','features':features}))
    result=light_map(path)
    assert len(result['features'])==2
    first=shape(result['features'][0]['geometry'])
    assert first.geom_type=='Polygon' and first.is_valid
    assert first.area==pytest.approx(dong_a.area+dong_b.area)
    y,x=result['features'][0]['properties']['label_point']
    assert first.covers(Point(x,y))
    assert len(result['province_boundaries']['features'])==1
    province=shape(result['province_boundaries']['features'][0]['geometry'])
    assert province.geom_type=='Polygon' and province.is_valid
    assert province.area==pytest.approx(first.area+other.area)
