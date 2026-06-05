import sys
import os
# Add project root to sys.path so we can import src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../docker")))

import pytest
from pydantic import ValidationError
from src.schemas.models import (
    SystemConfig, DBSchema, TableSchema, DBField, DBType,
    APIConfig, RouteConfig, HTTPMethod, UIConfig, UIComponent,
    ComponentType, AuthRules
)
from test_runner import test_db_schema as run_db_schema, test_api_routes as run_api_routes
from src.runtime.evaluator import RuntimeEvaluator

# Helper to build a valid minimum config
def get_valid_min_config():
    return {
        "system_name": "TestSystem",
        "db": {
            "tables": [
                {
                    "name": "users",
                    "fields": [
                        {"name": "id", "type": "integer", "nullable": False, "unique": True},
                        {"name": "name", "type": "string", "nullable": False, "unique": False}
                    ],
                    "primary_key": "id",
                    "foreign_keys": []
                }
            ]
        },
        "api": {
            "routes": [
                {
                    "path": "/users",
                    "method": "GET",
                    "parameters": [],
                    "request_body_schema": None,
                    "response_body_schema": None,
                    "auth_required": False,
                    "allowed_roles": []
                }
            ]
        },
        "ui": {
            "root_layout": "container",
            "components": [
                {
                    "id": "user_table",
                    "type": "table",
                    "label": "Users List",
                    "children": [],
                    "layout_position": "center",
                    "api_bindings": {"fetch": "/users"},
                    "state_bindings": {}
                }
            ]
        },
        "auth": {
            "roles": ["admin", "user"],
            "public_routes": ["/users"],
            "rbac_mappings": {}
        }
    }

def test_valid_config():
    """Verify that a valid configuration parses without error."""
    data = get_valid_min_config()
    config = SystemConfig.model_validate(data)
    assert config.system_name == "TestSystem"
    assert len(config.db.tables) == 1
    assert len(config.api.routes) == 1

def test_missing_foreign_key_table():
    """Verify cross-layer validation catches invalid foreign key target table."""
    data = get_valid_min_config()
    # Add foreign key referring to non-existent table 'roles'
    data["db"]["tables"][0]["foreign_keys"] = [
        {"field": "role_id", "references_table": "roles", "references_field": "id"}
    ]
    with pytest.raises(ValidationError) as excinfo:
        SystemConfig.model_validate(data)
    assert "Table 'users' refers to non-existent foreign table 'roles'" in str(excinfo.value)

def test_invalid_api_role():
    """Verify cross-layer validation catches API route referencing non-defined role."""
    data = get_valid_min_config()
    # Require auth and allow role 'manager' which is not in auth.roles
    data["api"]["routes"][0]["auth_required"] = True
    data["api"]["routes"][0]["allowed_roles"] = ["manager"]
    with pytest.raises(ValidationError) as excinfo:
        SystemConfig.model_validate(data)
    assert "allows role 'manager' which is not defined in AuthRules.roles" in str(excinfo.value)

def test_invalid_ui_api_binding():
    """Verify cross-layer validation catches UI binding referencing non-defined API route."""
    data = get_valid_min_config()
    # Bind to /nonexistent
    data["ui"]["components"][0]["api_bindings"] = {"fetch": "/nonexistent"}
    with pytest.raises(ValidationError) as excinfo:
        SystemConfig.model_validate(data)
    assert "binds to API path '/nonexistent' which does not exist in API routes" in str(excinfo.value)

def test_sqlite_db_compilation():
    """Verify SQLite table compilation succeeds for valid DB schema."""
    data = get_valid_min_config()
    db_schema = DBSchema.model_validate(data["db"])
    errors = run_db_schema(db_schema)
    assert len(errors) == 0

def test_sqlite_db_compilation_invalid_type():
    """Verify SQLite compilation flags errors (e.g. duplicate columns)."""
    # Create duplicate column name
    db_schema = DBSchema(tables=[
        TableSchema(
            name="users",
            fields=[
                DBField(name="id", type=DBType.INTEGER),
                DBField(name="id", type=DBType.STRING) # duplicate field name
            ],
            primary_key="id"
        )
    ])
    errors = run_db_schema(db_schema)
    assert len(errors) > 0
    assert "SQLite Compilation Error" in errors[0]

def test_fastapi_route_compilation():
    """Verify FastAPI routes validation compiles correctly."""
    data = get_valid_min_config()
    api_config = APIConfig.model_validate(data["api"])
    auth_rules = AuthRules.model_validate(data["auth"])
    errors = run_api_routes(api_config, auth_rules)
    assert len(errors) == 0

def test_fastapi_route_auth_failures():
    """Verify FastAPI route checking catches auth enforcement issues."""
    data = get_valid_min_config()
    # Require auth on a route
    data["api"]["routes"][0]["auth_required"] = True
    data["api"]["routes"][0]["allowed_roles"] = ["admin"]
    api_config = APIConfig.model_validate(data["api"])
    auth_rules = AuthRules.model_validate(data["auth"])
    errors = run_api_routes(api_config, auth_rules)
    # The check runs GET requests without auth headers, so it expects 401.
    # If the mock server returns 200, it would report an error.
    # But since the mock server raises 401 properly, no verification errors should be reported.
    assert len(errors) == 0
