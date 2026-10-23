from invoices import invoice_total


def test_dict_and_tuple_lines():
    lines = [{"qty": 2, "price": 5.0}, (1, 10.0)]
    assert invoice_total(lines) == 24.0


def test_no_vat():
    assert invoice_total([(3, 1.5)], vat=0) == 4.5
