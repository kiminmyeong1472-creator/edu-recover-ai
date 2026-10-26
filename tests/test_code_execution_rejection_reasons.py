import pytest

from app.services import code_execution_runtime as rt


@pytest.mark.parametrize("text,code", [
    ("[{'message': 'not authorized'}]", "ACCESS_DENIED"),
    ("[{'message': 'sandbox limit exceeded for your plan'}]", "PLAN_LIMIT"),
    ("[{'message': 'environment not found'}]", "ENVIRONMENT_INVALID"),
    ("[{'message': 'cannot query field \"sandboxcreate\"'}]", "SCHEMA_MISMATCH"),
    ("[{'message': 'something odd'}]", "REQUEST_REJECTED"),
])
def test_rejection_is_classified(text, code):
    assert rt._classify_rejection(text) == code


def test_every_runtime_code_has_a_page_reason():
    from app.services.code_execution_workspace import code_execution_simple_html
    page = code_execution_simple_html()
    for code, _ in rt._REJECTION_RULES:
        assert code + ":" in page
