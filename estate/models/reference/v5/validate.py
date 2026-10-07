"""Export recorded chronological holdouts; never refit on a test year's target."""
import argparse
import json
from pathlib import Path

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--recommendations', type=Path, default=Path('.work/build/recommendations.json'))
    p.add_argument('--output', type=Path, default=Path('.work/build/model_validation.json'))
    a = p.parse_args()
    model = json.loads(a.recommendations.read_text(encoding='utf-8'))
    if not model.get('validation'):
        raise ValueError('No independent chronological validation is available')
    result = {k:v for k,v in model.items() if k != 'recommendations'}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8',newline='\n')
    print(json.dumps(result['validation'], ensure_ascii=False))
