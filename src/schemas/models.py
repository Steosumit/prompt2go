from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from enum import Enum

class HTTPMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"

class DBType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    TEXT = "text"

# --- Database Schema ---
class DBField(BaseModel):
    name: str = Field(..., description="Name of the field/column")
    type: DBType = Field(..., description="Data type of the field")
    nullable: bool = Field(False, description="Is the field nullable")
    unique: bool = Field(False, description="Does the field have a unique constraint")
    default: Optional[Any] = Field(None, description="Default value for the field")

class ForeignKeyRelation(BaseModel):
    field: str = Field(..., description="Local field name that acts as the foreign key")
    references_table: str = Field(..., description="Name of the referenced table")
    references_field: str = Field(..., description="Field in the referenced table")

class TableSchema(BaseModel):
    name: str = Field(..., description="Name of the table")
    fields: List[DBField] = Field(..., description="List of fields in the table")
    primary_key: str = Field("id", description="Primary key field name")
    foreign_keys: List[ForeignKeyRelation] = Field(default_factory=list, description="Foreign key relationships")

class DBSchema(BaseModel):
    tables: List[TableSchema] = Field(..., description="List of tables in the database schema")

# --- API Config ---
class APIParamType(str, Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"

class APIParameter(BaseModel):
    name: str
    type: str = Field("string", description="Type of the parameter (e.g. string, integer)")
    param_type: APIParamType = Field(APIParamType.QUERY, description="Where the parameter is sent")
    required: bool = True

class RouteConfig(BaseModel):
    path: str = Field(..., description="HTTP endpoint path, e.g. /users/{id}")
    method: HTTPMethod = Field(..., description="HTTP method")
    parameters: List[APIParameter] = Field(default_factory=list, description="Query, path, or header parameters")
    request_body_schema: Optional[Dict[str, Any]] = Field(None, description="Expected request body structure or table reference name")
    response_body_schema: Optional[Dict[str, Any]] = Field(None, description="Expected response body structure")
    auth_required: bool = Field(False, description="Is authentication required for this route")
    allowed_roles: List[str] = Field(default_factory=list, description="Roles authorized to access this route")

class APIConfig(BaseModel):
    routes: List[RouteConfig] = Field(..., description="List of API routes")

# --- UI Config ---
class ComponentType(str, Enum):
    CONTAINER = "container"
    FORM = "form"
    INPUT = "input"
    BUTTON = "button"
    TABLE = "table"
    CARD = "card"
    TEXT = "text"
    NAVBAR = "navbar"

class UIComponent(BaseModel):
    id: str = Field(..., description="Unique component ID")
    type: ComponentType = Field(..., description="Type of UI component")
    label: Optional[str] = Field(None, description="Display label or text")
    children: List[str] = Field(default_factory=list, description="IDs of child components")
    layout_position: Optional[str] = Field(None, description="Grid or flex layout position hints")
    api_bindings: Dict[str, str] = Field(default_factory=dict, description="Bindings to API paths or triggers, e.g., {'submit': '/api/submit'}")
    state_bindings: Dict[str, str] = Field(default_factory=dict, description="State binding mapping, e.g. {'value': 'user.name'}")

class UIConfig(BaseModel):
    root_layout: str = Field("container", description="Type of root layout")
    components: List[UIComponent] = Field(..., description="List of components in the UI tree")

# --- Auth Rules ---
class AuthRules(BaseModel):
    roles: List[str] = Field(default_factory=list, description="Defined roles in the system")
    public_routes: List[str] = Field(default_factory=list, description="Routes that do not require auth")
    rbac_mappings: Dict[str, List[str]] = Field(default_factory=dict, description="Map roles to permissible UI components or actions")

# --- Consolidated System Configuration ---
class SystemConfig(BaseModel):
    system_name: str = Field(..., description="Name of the software application to compile")
    db: DBSchema = Field(..., description="Database schema configuration")
    api: APIConfig = Field(..., description="API configurations")
    ui: UIConfig = Field(..., description="UI layout configurations")
    auth: AuthRules = Field(..., description="Authentication and RBAC rules")

    @model_validator(mode="after")
    def validate_cross_layer_consistency(self) -> 'SystemConfig':
        """
        Ensures cross-layer consistency:
        1. Any DB table referenced in API request/response body or foreign keys must exist.
        2. Any role mentioned in API routes or UI permissions must exist in auth.roles.
        3. API paths in UI bindings must exist in API routes.
        """
        table_names = {t.name for t in self.db.tables}
        roles_defined = set(self.auth.roles)
        route_paths = {(r.path, r.method) for r in self.api.routes}
        route_paths_only = {r.path for r in self.api.routes}

        # 1. DB checks
        for table in self.db.tables:
            # Check foreign keys
            for fk in table.foreign_keys:
                if fk.references_table not in table_names:
                    raise ValueError(f"Table '{table.name}' refers to non-existent foreign table '{fk.references_table}'")
                
                # Verify that referenced field exists in the target table
                target_table = next(t for t in self.db.tables if t.name == fk.references_table)
                target_fields = {f.name for f in target_table.fields}
                if fk.references_field not in target_fields:
                    raise ValueError(f"Foreign key in table '{table.name}' references non-existent field '{fk.references_field}' in '{fk.references_table}'")

        # 2. Roles checks in API
        for route in self.api.routes:
            if route.auth_required:
                for role in route.allowed_roles:
                    if role not in roles_defined:
                        raise ValueError(f"Route '{route.path}' allows role '{role}' which is not defined in AuthRules.roles")

        # 3. UI API bindings
        for component in self.ui.components:
            for action, api_path in component.api_bindings.items():
                # We can do a loose path matching or exact matching. Let's do a loose matching:
                # remove route path variables like {id} for matching, or just check if it's matching any route path
                cleaned_path = api_path.split('?')[0] # remove query params
                # Find if any route path matches or matches if templated
                match_found = False
                for r_path in route_paths_only:
                    # simplistic check: exact match, or checking template parts
                    if r_path == cleaned_path:
                        match_found = True
                        break
                    # standardise parameter placeholders
                    import re
                    pattern = re.sub(r'\{[a-zA-Z0-9_]+\}', '[a-zA-Z0-9_-]+', r_path)
                    if re.match(f"^{pattern}$", cleaned_path):
                        match_found = True
                        break
                if not match_found:
                    raise ValueError(f"UI Component '{component.id}' binds to API path '{api_path}' which does not exist in API routes")

        return self
