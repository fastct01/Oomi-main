from oomi.fingerprint import command_base, extract_error, redact, signature

IVAN = '''Traceback (most recent call last):
  File "/Users/ivan/code/shop/app.py", line 3, in <module>
    import jwt
ModuleNotFoundError: No module named 'jwt'
'''
OLGA = '''Traceback (most recent call last):
  File "/home/olga/work/api/server.py", line 17, in <module>
    import jwt
ModuleNotFoundError: No module named 'jwt'
'''
PYTEST = '''============================= test session starts ==============================
platform darwin -- Python 3.12.1, pytest-8.0.0
collected 3 items
tests/test_auth.py F..                                                   [100%]
E   AssertionError: assert 401 == 200
FAILED tests/test_auth.py::test_login - AssertionError: assert 401 == 200
========================= 1 failed, 2 passed in 0.12s ==========================
'''


def test_same_bug_on_two_machines_has_the_same_fingerprint():
    a, b = extract_error(IVAN), extract_error(OLGA)
    assert a == b == "ModuleNotFoundError: No module named 'jwt'"
    assert signature(a) == signature(b)


def test_pytest_failure_keeps_the_assertion_and_drops_noise():
    e = extract_error(PYTEST)
    assert "AssertionError: assert 401 == 200" in e
    assert "test session starts" not in e and "0.12s" not in e


def test_nothing_error_like_gives_something_short():
    assert extract_error("") == ""
    assert len(extract_error("just some text\nmore text")) < 100


def test_redact_removes_secrets_and_personal_details():
    text = ("token=abc123 at /Users/ivan/x key sk-ant-12345678901234567 by ivan@example.com "
            "via mongodb+srv://u:p@c.mongodb.net/db")
    out = redact(text)
    for leaked in ("abc123", "/Users/ivan", "sk-ant", "ivan@example.com", "u:p@"):
        assert leaked not in out


def test_command_base_groups_test_runs():
    assert command_base("pytest tests/test_a.py -x") == command_base("pytest") == "pytest"
    assert command_base("python app.py") == "python app.py"
