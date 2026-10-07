from pathlib import Path
from estate.tools.verify_changes import verify


def test_moved_code_counts_new_lines_but_scans_whole_payload(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('moved.py').write_bytes(b'a=1\nb=2\n')
    got=verify(['moved.py'],{'moved.py':b'a=1\n'})
    assert got['ready'] and got['bytes']==len(b'b=2\n')
    Path('raw.csv').write_bytes(b'secret raw data')
    assert not verify(['raw.csv'],{'raw.csv':b'secret raw data'})['ready']


def test_relocated_default_paths_still_point_to_repository_root():
    from estate.data.collection.building_details import ROOT_DIR
    from estate.research.price import retraining,full_history,access_confidence
    root=Path(__file__).resolve().parents[1]
    assert ROOT_DIR==retraining.ROOT==full_history.ROOT==access_confidence.ROOT==root
