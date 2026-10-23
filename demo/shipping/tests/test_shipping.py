from shipping import quote


def test_one_parcel():
    assert quote({"kg": 2}) == 6.4


def test_many_parcels():
    assert quote([{"kg": 1}, {"kg": 0.5}]) == 4.8
