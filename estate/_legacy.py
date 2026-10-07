"""Read previously published immutable model files after the package move."""
import importlib
import importlib.abc
import importlib.util
import sys
ALIASES = {'estate_model': 'estate.models.reference.v5.model', 'estate_nowcast': 'estate.models.nowcast.v1.model', 'estate_regional_nowcast': 'estate.models.nowcast.regional_v2.model', 'estate_quantile_nowcast': 'estate.models.nowcast.quantile_v1.model', 'estate_price_confidence': 'estate.models.nowcast.confidence.model'}
class LegacyModels(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ALIASES:
            return importlib.util.spec_from_loader(fullname,self)
    def create_module(self,spec):
        return None
    def exec_module(self,module):
        target=importlib.import_module(ALIASES[module.__name__])
        for name,value in vars(target).items():
            if not name.startswith('__'):
                setattr(module,name,value)
def install():
    if not any(isinstance(f,LegacyModels) for f in sys.meta_path):
        sys.meta_path.append(LegacyModels())
