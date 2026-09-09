import numpy as np
import pandas as pd
import pytest

from analyze_estate_full_history_adaptation import COLS, PRICE_LEVELS, inputs, training_weights


def test_relative_inputs_do_not_depend_on_nominal_price_scale():
    a=pd.DataFrame({c:[1.,2.] for c in COLS})
    a['anchor']=[4.,5.]
    b=a.copy()
    b[['anchor',*PRICE_LEVELS]]+=3.
    pd.testing.assert_frame_equal(inputs(a,'relative_uniform'),inputs(b,'relative_uniform'))
    assert 'anchor' not in inputs(a,'relative_uniform')


def test_all_old_rows_keep_positive_weight_and_future_rows_are_rejected():
    f=pd.DataFrame({'year':[2007,2020,2024],'month':['2007-03','2020-12','2024-12']})
    w=training_weights(f,2025,'recency_four_years')
    assert np.all(w>0)
    assert w[0]<w[1]<w[2]
    assert w.mean()==pytest.approx(1.)
    assert w[2]/w[1]==pytest.approx(2.)
    with pytest.raises(ValueError): training_weights(f,2024,'all_history')
