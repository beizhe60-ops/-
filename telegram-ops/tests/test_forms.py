from app.main import app


def test_account_form_allows_empty_proxy_port():
    route = next(route for route in app.routes if getattr(route, "path", None) == "/accounts" and "POST" in getattr(route, "methods", set()))
    dependant = route.dependant
    field = next(field for field in dependant.body_params if field.name == "proxy_port")
    assert field.field_info.default == ""
