import sys
import json
import sqlite3
import re
import os
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator
from enum import Enum

# --- Duplicate models for self-contained sandbox run ---
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

class DBField(BaseModel):
    name: str
    type: DBType
    nullable: bool = False
    unique: bool = False
    default: Optional[Any] = None

class ForeignKeyRelation(BaseModel):
    field: str
    references_table: str
    references_field: str

class TableSchema(BaseModel):
    name: str
    fields: List[DBField]
    primary_key: str = "id"
    foreign_keys: List[ForeignKeyRelation] = []

class DBSchema(BaseModel):
    tables: List[TableSchema]

class APIParamType(str, Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"

class APIParameter(BaseModel):
    name: str
    type: str = "string"
    param_type: APIParamType = APIParamType.QUERY
    required: bool = True

class RouteConfig(BaseModel):
    path: str
    method: HTTPMethod
    parameters: List[APIParameter] = []
    request_body_schema: Optional[Dict[str, Any]] = None
    response_body_schema: Optional[Dict[str, Any]] = None
    auth_required: bool = False
    allowed_roles: List[str] = []

class APIConfig(BaseModel):
    routes: List[RouteConfig]

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
    id: str
    type: ComponentType
    label: Optional[str] = None
    children: List[str] = []
    layout_position: Optional[str] = None
    api_bindings: Dict[str, str] = {}
    state_bindings: Dict[str, str] = {}

class UIConfig(BaseModel):
    root_layout: str = "container"
    components: List[UIComponent]

class AuthRules(BaseModel):
    roles: List[str] = []
    public_routes: List[str] = []
    rbac_mappings: Dict[str, List[str]] = {}

class SystemConfig(BaseModel):
    system_name: str
    db: DBSchema
    api: APIConfig
    ui: UIConfig
    auth: AuthRules

    @model_validator(mode="after")
    def validate_cross_layer_consistency(self) -> 'SystemConfig':
        table_names = {t.name for t in self.db.tables}
        roles_defined = set(self.auth.roles)
        route_paths_only = {r.path for r in self.api.routes}

        for table in self.db.tables:
            for fk in table.foreign_keys:
                if fk.references_table not in table_names:
                    raise ValueError(f"Table '{table.name}' refers to non-existent foreign table '{fk.references_table}'")
                target_table = next(t for t in self.db.tables if t.name == fk.references_table)
                target_fields = {f.name for f in target_table.fields}
                if fk.references_field not in target_fields:
                    raise ValueError(f"Foreign key in table '{table.name}' references non-existent field '{fk.references_field}' in '{fk.references_table}'")

        for route in self.api.routes:
            if route.auth_required:
                for role in route.allowed_roles:
                    if role not in roles_defined:
                        raise ValueError(f"Route '{route.path}' allows role '{role}' which is not defined in AuthRules.roles")

        for component in self.ui.components:
            for action, api_path in component.api_bindings.items():
                cleaned_path = api_path.split('?')[0]
                match_found = False
                for r_path in route_paths_only:
                    if r_path == cleaned_path:
                        match_found = True
                        break
                    pattern = re.sub(r'\{[a-zA-Z0-9_]+\}', '[a-zA-Z0-9_-]+', r_path)
                    if re.match(f"^{pattern}$", cleaned_path):
                        match_found = True
                        break
                if not match_found:
                    raise ValueError(f"UI Component '{component.id}' binds to API path '{api_path}' which does not exist in API routes")

        return self

def test_db_schema(db_schema: DBSchema) -> List[str]:
    """Test if the DB schema compiles successfully in SQLite."""
    errors = []
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    type_map = {
        DBType.STRING: "TEXT",
        DBType.TEXT: "TEXT",
        DBType.INTEGER: "INTEGER",
        DBType.FLOAT: "REAL",
        DBType.BOOLEAN: "INTEGER",
        DBType.DATETIME: "TEXT"
    }

    try:
        cursor.execute("PRAGMA foreign_keys = ON;")
        for table in db_schema.tables:
            columns_sql = []
            for field in table.fields:
                col_type = type_map.get(field.type, "TEXT")
                constraints = []
                if field.name == table.primary_key:
                    constraints.append("PRIMARY KEY")
                if not field.nullable:
                    constraints.append("NOT NULL")
                if field.unique:
                    constraints.append("UNIQUE")
                if field.default is not None:
                    if isinstance(field.default, str):
                        constraints.append(f"DEFAULT '{field.default}'")
                    else:
                        constraints.append(f"DEFAULT {field.default}")
                
                columns_sql.append(f"{field.name} {col_type} {' '.join(constraints)}")
            
            for fk in table.foreign_keys:
                columns_sql.append(
                    f"FOREIGN KEY ({fk.field}) REFERENCES {fk.references_table} ({fk.references_field})"
                )
            
            create_sql = f"CREATE TABLE {table.name} (\n  " + ",\n  ".join(columns_sql) + "\n);"
            cursor.execute(create_sql)
            
        conn.commit()
    except Exception as e:
        errors.append(f"SQLite Compilation Error: {str(e)}")
    finally:
        conn.close()
    
    return errors

def test_api_routes(api_config: APIConfig, auth_rules: AuthRules) -> List[str]:
    """Test if the API routes config compiles and works correctly using FastAPI and HTTPX."""
    errors = []
    
    try:
        from fastapi import FastAPI, Depends, Header, HTTPException, status
        from fastapi.testclient import TestClient
    except ImportError as e:
        return [f"FastAPI import failed: {str(e)}"]

    app = FastAPI(title="Sandbox Validation API")

    def get_current_user_role(x_user_role: Optional[str] = Header(None)) -> Optional[str]:
        return x_user_role

    for route in api_config.routes:
        path = route.path if route.path.startswith("/") else f"/{route.path}"
        
        def make_endpoint(route_cfg=route):
            async def endpoint(
                role: Optional[str] = Depends(get_current_user_role)
            ):
                if route_cfg.auth_required:
                    if not role:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required"
                        )
                    if route_cfg.allowed_roles and role not in route_cfg.allowed_roles:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Insufficient permissions"
                        )
                mock_data = route_cfg.response_body_schema or {"status": "success", "message": f"Mock data for {route_cfg.path}"}
                return mock_data
            return endpoint

        method_lower = route.method.value.lower()
        getattr(app, method_lower)(path)(make_endpoint(route))

    try:
        client = TestClient(app)
        for route in api_config.routes:
            path = route.path if route.path.startswith("/") else f"/{route.path}"
            test_path = re.sub(r'\{[a-zA-Z0-9_]+\}', '1', path)
            
            if route.method == HTTPMethod.GET:
                response = client.get(test_path)
            elif route.method == HTTPMethod.POST:
                response = client.post(test_path, json={})
            elif route.method == HTTPMethod.PUT:
                response = client.put(test_path, json={})
            elif route.method == HTTPMethod.DELETE:
                response = client.delete(test_path)
            else:
                response = client.request(route.method.value, test_path)

            if route.auth_required:
                if response.status_code != 401:
                    errors.append(
                        f"Route {route.method.value} {route.path} requires auth but returned status {response.status_code} without credentials"
                    )
                
                for role in route.allowed_roles:
                    headers = {"X-User-Role": role}
                    if route.method == HTTPMethod.GET:
                        res = client.get(test_path, headers=headers)
                    elif route.method == HTTPMethod.POST:
                        res = client.post(test_path, json={}, headers=headers)
                    elif route.method == HTTPMethod.PUT:
                        res = client.put(test_path, json={}, headers=headers)
                    elif route.method == HTTPMethod.DELETE:
                        res = client.delete(test_path, headers=headers)
                    else:
                        res = client.request(route.method.value, test_path, headers=headers)
                    
                    if res.status_code in (401, 403):
                        errors.append(
                            f"Route {route.method.value} {route.path} denied access to valid role '{role}' (returned status {res.status_code})"
                        )
            else:
                if response.status_code in (401, 403):
                    errors.append(
                        f"Public route {route.method.value} {route.path} returned status {response.status_code}"
                    )
                    
    except Exception as e:
        errors.append(f"FastAPI TestClient execution failed: {str(e)}")

    return errors

def run_live_server(schema_data: Dict[str, Any]):
    """Start a real FastAPI Uvicorn server exposing the schema configuration as a functional mock application."""
    import uvicorn
    from fastapi import FastAPI, Depends, Header, HTTPException, status, Request
    from fastapi.responses import HTMLResponse
    from fastapi.middleware.cors import CORSMiddleware
    
    app = FastAPI(
        title=schema_data.get("system_name", "Compiled Mock Application"),
        description="Dynamic mock backend and frontend generated by prompt2go."
    )
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    mock_db = {}
    db_schema = schema_data.get("db", {})
    for table in db_schema.get("tables", []):
        mock_db[table["name"]] = []
        
    def get_current_user_role(x_user_role: Optional[str] = Header(None)) -> Optional[str]:
        return x_user_role

    api_config = schema_data.get("api", {})
    
    for route in api_config.get("routes", []):
        path = route["path"] if route["path"].startswith("/") else f"/{route['path']}"
        method = route["method"].lower()
        
        def make_live_endpoint(route_cfg=route):
            async def endpoint(
                request: Request,
                role: Optional[str] = Depends(get_current_user_role)
            ):
                if route_cfg.get("auth_required"):
                    if not role:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required"
                        )
                    allowed = route_cfg.get("allowed_roles", [])
                    if allowed and role not in allowed:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Insufficient permissions"
                        )
                
                path_parts = [p for p in route_cfg["path"].strip("/").split("/") if p]
                if path_parts:
                    resource = path_parts[0]
                    if resource in mock_db:
                        if request.method == "GET":
                            if len(path_parts) > 1:
                                item_id = path_parts[1]
                                for item in mock_db[resource]:
                                    if str(item.get("id")) == str(item_id):
                                        return item
                                raise HTTPException(status_code=404, detail="Item not found")
                            return mock_db[resource]
                        
                        elif request.method == "POST":
                            try:
                                body = await request.json()
                            except Exception:
                                body = {}
                            if "id" not in body or not body["id"]:
                                body["id"] = len(mock_db[resource]) + 1
                            mock_db[resource].append(body)
                            return body
                            
                        elif request.method == "PUT" or request.method == "PATCH":
                            if len(path_parts) > 1:
                                item_id = path_parts[1]
                                try:
                                    body = await request.json()
                                except Exception:
                                    body = {}
                                for i, item in enumerate(mock_db[resource]):
                                    if str(item.get("id")) == str(item_id):
                                        mock_db[resource][i].update(body)
                                        return mock_db[resource][i]
                                raise HTTPException(status_code=404, detail="Item not found")
                                
                        elif request.method == "DELETE":
                            if len(path_parts) > 1:
                                item_id = path_parts[1]
                                for i, item in enumerate(mock_db[resource]):
                                    if str(item.get("id")) == str(item_id):
                                        deleted = mock_db[resource].pop(i)
                                        return {"status": "deleted", "item": deleted}
                                raise HTTPException(status_code=404, detail="Item not found")

                mock_res = route_cfg.get("response_body_schema")
                if not mock_res:
                    mock_res = {"status": "success", "message": f"Mock response for {route_cfg['path']}"}
                return mock_res
                
            return endpoint

        getattr(app, method)(path)(make_live_endpoint(route))
        
    @app.get("/api/system-schema")
    def get_schema():
        return schema_data

    @app.get("/", response_class=HTMLResponse)
    def serve_dashboard():
        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mock Application Preview</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --panel-bg: rgba(22, 29, 48, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --accent-cyan: #06b6d4;
            --accent-purple: #8b5cf6;
            --accent-gradient: linear-gradient(135deg, #06b6d4, #8b5cf6);
            --success: #10b981;
            --danger: #ef4444;
            --shadow-lg: 0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-color);
            background-image: radial-gradient(circle at top right, rgba(139, 92, 246, 0.08), transparent 400px),
                              radial-gradient(circle at bottom left, rgba(6, 182, 212, 0.08), transparent 400px);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }
        header {
            background: rgba(15, 23, 42, 0.65);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border-color);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .logo-container { display: flex; align-items: center; gap: 0.75rem; }
        .logo-badge {
            background: var(--accent-gradient);
            padding: 0.4rem 0.8rem;
            border-radius: 8px;
            font-weight: 700;
            font-size: 0.9rem;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            box-shadow: 0 4px 12px rgba(6, 182, 212, 0.2);
        }
        .system-name {
            font-weight: 700;
            font-size: 1.25rem;
            background: linear-gradient(to right, #ffffff, #9ca3af);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .header-controls { display: flex; align-items: center; gap: 1rem; }
        .role-selector-container {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            background: rgba(255, 255, 255, 0.05);
            padding: 0.35rem 0.75rem;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }
        .role-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            font-weight: 600;
        }
        select.role-select {
            background: transparent;
            color: var(--text-primary);
            border: none;
            outline: none;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
        }
        select.role-select option {
            background: var(--bg-color);
            color: var(--text-primary);
        }
        .app-container { flex: 1; display: flex; position: relative; }
        .sidebar {
            width: 250px;
            background: rgba(15, 23, 42, 0.4);
            border-right: 1px solid var(--border-color);
            padding: 2rem 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        .nav-link {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.75rem 1rem;
            color: var(--text-secondary);
            text-decoration: none;
            font-weight: 500;
            font-size: 0.9rem;
            border-radius: 8px;
            transition: all 0.2s ease;
            cursor: pointer;
        }
        .nav-link:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.03); }
        .nav-link.active {
            color: var(--text-primary);
            background: rgba(6, 182, 212, 0.1);
            border-left: 3px solid var(--accent-cyan);
        }
        .main-content {
            flex: 1;
            padding: 2rem;
            overflow-y: auto;
            max-width: 1200px;
            margin: 0 auto;
            width: 100%;
        }
        .grid-layout { display: grid; grid-template-columns: repeat(12, 1fr); gap: 1.5rem; }
        .col-12 { grid-column: span 12; }
        .col-8 { grid-column: span 8; }
        .col-6 { grid-column: span 6; }
        .col-4 { grid-column: span 4; }
        .col-3 { grid-column: span 3; }
        
        .glass-panel {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-radius: 16px;
            padding: 1.5rem;
            box-shadow: var(--shadow-lg);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .glass-panel:hover { box-shadow: 0 15px 30px -5px rgba(0, 0, 0, 0.4); }
        .stat-card { display: flex; flex-direction: column; gap: 0.5rem; }
        .stat-label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); font-weight: 600; }
        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .form-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 1rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.5rem; }
        .form-group { margin-bottom: 1.25rem; display: flex; flex-direction: column; gap: 0.35rem; }
        .form-label { font-size: 0.8rem; font-weight: 500; color: var(--text-secondary); }
        .form-control {
            background: rgba(15, 23, 42, 0.5);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.65rem 0.85rem;
            color: var(--text-primary);
            font-size: 0.9rem;
            font-family: inherit;
            outline: none;
            transition: border-color 0.2s ease;
        }
        .form-control:focus { border-color: var(--accent-cyan); box-shadow: 0 0 0 2px rgba(6, 182, 212, 0.15); }
        .btn {
            background: var(--accent-gradient);
            color: white;
            border: none;
            padding: 0.65rem 1.25rem;
            font-size: 0.9rem;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            box-shadow: 0 4px 12px rgba(139, 92, 246, 0.25);
            display: inline-flex;
            justify-content: center;
            align-items: center;
        }
        .btn:hover { transform: translateY(-1px); box-shadow: 0 6px 16px rgba(139, 92, 246, 0.35); }
        .btn:active { transform: translateY(1px); }
        .table-container { overflow-x: auto; width: 100%; }
        table.data-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.9rem; }
        table.data-table th {
            padding: 0.75rem 1rem;
            border-bottom: 2px solid var(--border-color);
            color: var(--text-secondary);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
        }
        table.data-table td { padding: 0.85rem 1rem; border-bottom: 1px solid var(--border-color); color: var(--text-primary); }
        table.data-table tr:hover { background: rgba(255, 255, 255, 0.01); }
        .badge { display: inline-block; padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }
        .badge.success { background: rgba(16, 185, 129, 0.1); color: var(--success); }
        .badge.warning { background: rgba(245, 158, 11, 0.1); color: #f59e0b; }
        .badge.info { background: rgba(6, 182, 212, 0.1); color: var(--accent-cyan); }
        
        .toast-container { position: fixed; bottom: 2rem; right: 2rem; display: flex; flex-direction: column; gap: 0.75rem; z-index: 1000; }
        .toast {
            background: rgba(15, 23, 42, 0.9);
            border-left: 4px solid var(--accent-cyan);
            border-radius: 8px;
            padding: 1rem 1.5rem;
            color: var(--text-primary);
            box-shadow: var(--shadow-lg);
            display: flex;
            align-items: center;
            gap: 0.75rem;
            animation: slideIn 0.3s ease forwards;
            font-size: 0.85rem;
            font-weight: 500;
        }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-container">
            <span class="logo-badge">sandbox</span>
            <span class="system-name" id="app-title">Application Dashboard</span>
        </div>
        <div class="header-controls">
            <div class="role-selector-container">
                <span class="role-label">Role</span>
                <select class="role-select" id="role-select" onchange="changeRole(this.value)">
                    <option value="">Guest (Public)</option>
                </select>
            </div>
        </div>
    </header>
    
    <div class="app-container">
        <aside class="sidebar" id="sidebar-nav"></aside>
        <main class="main-content" id="main-view"></main>
    </div>
    
    <div class="toast-container" id="toast-container"></div>
    
    <script>
        let schema = {};
        let activeRole = localStorage.getItem("activeRole") || "";
        let activeView = "";
        
        async function init() {
            try {
                const res = await fetch("/api/system-schema");
                schema = await res.json();
                
                document.getElementById("app-title").textContent = schema.system_name || "Mock App";
                
                const roleSelect = document.getElementById("role-select");
                roleSelect.innerHTML = '<option value="">Guest (Public)</option>';
                const roles = schema.auth?.roles || [];
                roles.forEach(role => {
                    const option = document.createElement("option");
                    option.value = role;
                    option.textContent = role.toUpperCase();
                    if (role === activeRole) option.selected = true;
                    roleSelect.appendChild(option);
                });
                
                renderNavigation();
                
                const views = getAccessibleViews();
                if (views.length > 0) {
                    navigateTo(views[0].id);
                }
            } catch (err) {
                showToast("Failed to load application schema.", "danger");
            }
        }
        
        function changeRole(role) {
            activeRole = role;
            localStorage.setItem("activeRole", role);
            showToast(`Switched active role to: ${role || 'Guest'}`, "info");
            
            renderNavigation();
            const views = getAccessibleViews();
            if (views.length > 0) {
                if (!views.some(v => v.id === activeView)) {
                    navigateTo(views[0].id);
                } else {
                    navigateTo(activeView);
                }
            } else {
                document.getElementById("main-view").innerHTML = `
                    <div class="glass-panel" style="text-align:center; padding: 3rem;">
                        <h3>Access Denied</h3>
                        <p style="color:var(--text-secondary); margin-top:0.5rem;">You do not have access to any components with your current role.</p>
                    </div>
                `;
            }
        }
        
        function getAccessibleViews() {
            if (!schema.ui || !schema.ui.components) return [];
            const childIds = new Set();
            schema.ui.components.forEach(c => {
                c.children.forEach(cid => childIds.add(cid));
            });
            const rootViews = schema.ui.components.filter(c => !childIds.has(c.id));
            return rootViews.filter(v => isComponentAccessible(v.id));
        }
        
        function isComponentAccessible(componentId) {
            if (schema.auth && schema.auth.rbac_mappings) {
                let isRestricted = false;
                let hasAccess = false;
                for (const [role, components] of Object.entries(schema.auth.rbac_mappings)) {
                    if (components.includes(componentId)) {
                        isRestricted = true;
                        if (role === activeRole) hasAccess = true;
                    }
                }
                if (isRestricted) return hasAccess;
            }
            return true;
        }
        
        function renderNavigation() {
            const sidebar = document.getElementById("sidebar-nav");
            sidebar.innerHTML = "";
            const views = getAccessibleViews();
            views.forEach(view => {
                const link = document.createElement("div");
                link.className = `nav-link ${view.id === activeView ? 'active' : ''}`;
                link.textContent = view.label || view.id;
                link.onclick = () => navigateTo(view.id);
                sidebar.appendChild(link);
            });
        }
        
        function navigateTo(viewId) {
            activeView = viewId;
            document.querySelectorAll(".nav-link").forEach(link => {
                const component = schema.ui.components.find(c => c.id === viewId);
                if (link.textContent === (component?.label || viewId)) {
                    link.classList.add("active");
                } else {
                    link.classList.remove("active");
                }
            });
            renderView(viewId);
        }
        
        function renderView(viewId) {
            const mainView = document.getElementById("main-view");
            mainView.innerHTML = "";
            const viewComponent = schema.ui.components.find(c => c.id === viewId);
            if (!viewComponent) return;
            
            const container = document.createElement("div");
            container.className = "grid-layout";
            
            if (viewComponent.children && viewComponent.children.length > 0) {
                viewComponent.children.forEach(childId => {
                    const childCol = document.createElement("div");
                    childCol.className = "col-6";
                    const childComp = schema.ui.components.find(c => c.id === childId);
                    if (childComp) {
                        if (childComp.type === "table") childCol.className = "col-12";
                        if (childComp.type === "form") childCol.className = "col-6";
                        if (childComp.type === "card") childCol.className = "col-3";
                        renderComponent(childId, childCol);
                        container.appendChild(childCol);
                    }
                });
            } else {
                const mainCol = document.createElement("div");
                mainCol.className = "col-12";
                renderComponent(viewId, mainCol);
                container.appendChild(mainCol);
            }
            mainView.appendChild(container);
        }
        
        function renderComponent(componentId, parentElement) {
            const comp = schema.ui.components.find(c => c.id === componentId);
            if (!comp || !isComponentAccessible(componentId)) return;
            
            const panel = document.createElement("div");
            panel.className = "glass-panel";
            
            if (comp.type === "card") {
                panel.classList.add("stat-card");
                panel.innerHTML = `
                    <span class="stat-label">${comp.label || comp.id}</span>
                    <span class="stat-value" id="val-${comp.id}">--</span>
                `;
                parentElement.appendChild(panel);
                loadCardData(comp, panel);
            }
            else if (comp.type === "table") {
                panel.innerHTML = `
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
                        <h3 style="font-size:1.1rem; font-weight:600;">${comp.label || comp.id}</h3>
                        <span style="font-size:0.75rem; color:var(--text-secondary);" id="count-${comp.id}">0 items</span>
                    </div>
                    <div class="table-container">
                        <table class="data-table">
                            <thead id="thead-${comp.id}"></thead>
                            <tbody id="tbody-${comp.id}">
                                <tr><td colspan="100%" style="text-align:center; color:var(--text-secondary);">Loading...</td></tr>
                            </tbody>
                        </table>
                    </div>
                `;
                parentElement.appendChild(panel);
                loadTableData(comp, panel);
            }
            else if (comp.type === "form") {
                panel.innerHTML = `
                    <h3 class="form-title">${comp.label || 'Submit Details'}</h3>
                    <form id="form-${comp.id}" onsubmit="handleFormSubmit(event, '${comp.id}')">
                        <div id="fields-${comp.id}"></div>
                        <button class="btn" type="submit">Submit</button>
                    </form>
                `;
                parentElement.appendChild(panel);
                renderFormFields(comp, panel);
            }
            else {
                panel.innerHTML = `
                    <h3 style="margin-bottom:0.5rem;">${comp.label || comp.id}</h3>
                    <p style="color:var(--text-secondary); font-size:0.9rem;">Widget type: ${comp.type}</p>
                `;
                parentElement.appendChild(panel);
            }
        }
        
        async function makeRequest(url, method = "GET", body = null) {
            const headers = { "Content-Type": "application/json" };
            if (activeRole) headers["X-User-Role"] = activeRole;
            
            const options = { method, headers };
            if (body) options.body = JSON.stringify(body);
            
            const res = await fetch(url, options);
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `HTTP Error ${res.status}`);
            }
            return res.json();
        }
        
        async function loadCardData(comp, panelNode) {
            const root = panelNode || document;
            const valEl = root.querySelector(`#val-${comp.id}`);
            if (!valEl) return;
            const apiPath = comp.api_bindings?.fetch || comp.api_bindings?.value;
            if (!apiPath) { valEl.textContent = "12"; return; }
            try {
                const data = await makeRequest(apiPath);
                if (Array.isArray(data)) valEl.textContent = data.length;
                else if (typeof data === "object" && data !== null) valEl.textContent = data.count || data.value || data.total || 0;
                else valEl.textContent = data;
            } catch (err) {
                valEl.textContent = "Locked";
                valEl.style.color = "var(--danger)";
            }
        }
        
        async function loadTableData(comp, panelNode) {
            const root = panelNode || document;
            const thead = root.querySelector(`#thead-${comp.id}`);
            const tbody = root.querySelector(`#tbody-${comp.id}`);
            const countEl = root.querySelector(`#count-${comp.id}`);
            if (!tbody) return;
            const apiPath = comp.api_bindings?.fetch;
            if (!apiPath) {
                tbody.innerHTML = '<tr><td style="text-align:center; color:var(--text-secondary);">No API config.</td></tr>';
                return;
            }
            try {
                const data = await makeRequest(apiPath);
                if (!Array.isArray(data) || data.length === 0) {
                    tbody.innerHTML = '<tr><td style="text-align:center; color:var(--text-secondary);">No records.</td></tr>';
                    if (countEl) countEl.textContent = "0 items";
                    return;
                }
                if (countEl) countEl.textContent = `${data.length} item${data.length === 1 ? '' : 's'}`;
                const columns = Object.keys(data[0]);
                if (thead) thead.innerHTML = `<tr>${columns.map(col => `<th>${col}</th>`).join('')}</tr>`;
                tbody.innerHTML = data.map(item => `
                    <tr>${columns.map(col => {
                        const val = item[col];
                        if (col === "status" || col === "priority") {
                            const bClass = val === "completed" || val === "active" || val === "high" ? "success" : "warning";
                            return `<td><span class="badge ${bClass}">${val}</span></td>`;
                        }
                        return `<td>${val === null ? '-' : val}</td>`;
                    }).join('')}</tr>
                `).join('');
            } catch (err) {
                tbody.innerHTML = `<tr><td style="text-align:center; color:var(--danger); font-weight:600;">Access Denied (${err.message})</td></tr>`;
                if (countEl) countEl.textContent = "0 items";
            }
        }
        
        function renderFormFields(comp, panelNode) {
            const root = panelNode || document;
            const container = root.querySelector(`#fields-${comp.id}`);
            if (!container) return;
            container.innerHTML = "";
            const apiPath = comp.api_bindings?.submit || comp.api_bindings?.post;
            if (!apiPath) return;
            
            let fields = ["name", "email", "status"];
            const pathParts = apiPath.split("/").filter(x => x);
            const resource = pathParts[0] || "records";
            const dbTable = schema.db?.tables.find(t => t.name === resource);
            if (dbTable) {
                fields = dbTable.fields.filter(f => f.name !== dbTable.primary_key).map(f => f.name);
            }
            fields.forEach(field => {
                const group = document.createElement("div");
                group.className = "form-group";
                const label = document.createElement("label");
                label.className = "form-label";
                label.textContent = field.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
                const input = document.createElement("input");
                input.className = "form-control";
                input.name = field;
                input.id = `input-${comp.id}-${field}`;
                if (field.includes("email")) input.type = "email";
                else if (field.includes("date")) input.type = "date";
                else if (field.includes("price") || field.includes("quantity") || field.includes("score")) input.type = "number";
                else input.type = "text";
                group.appendChild(label);
                group.appendChild(input);
                container.appendChild(group);
            });
        }
        
        async function handleFormSubmit(event, compId) {
            event.preventDefault();
            const comp = schema.ui.components.find(c => c.id === compId);
            if (!comp) return;
            const apiPath = comp.api_bindings?.submit || comp.api_bindings?.post;
            if (!apiPath) return;
            
            const form = document.getElementById(`form-${compId}`);
            const formData = new FormData(form);
            const body = {};
            formData.forEach((value, key) => {
                const inputEl = document.getElementById(`input-${compId}-${key}`);
                if (inputEl.type === "number") body[key] = Number(value);
                else body[key] = value;
            });
            try {
                await makeRequest(apiPath, "POST", body);
                showToast("Record successfully created!", "success");
                form.reset();
                schema.ui.components.forEach(c => {
                    if (c.type === "table" && c.api_bindings?.fetch === apiPath) {
                        const tablePanel = document.querySelector(`.glass-panel:has(#tbody-${c.id})`) || document.getElementById(`tbody-${c.id}`)?.closest('.glass-panel');
                        loadTableData(c, tablePanel);
                    }
                    if (c.type === "card" && (c.api_bindings?.fetch === apiPath || c.api_bindings?.value === apiPath)) {
                        const cardPanel = document.querySelector(`.glass-panel:has(#val-${c.id})`) || document.getElementById(`val-${c.id}`)?.closest('.glass-panel');
                        loadCardData(c, cardPanel);
                    }
                });
            } catch (err) {
                showToast(`Submission failed: ${err.message}`, "danger");
            }
        }
        
        function showToast(message, type = "success") {
            const container = document.getElementById("toast-container");
            const toast = document.createElement("div");
            toast.className = "toast";
            toast.style.borderLeftColor = type === "success" ? "var(--success)" : type === "danger" ? "var(--danger)" : "var(--accent-cyan)";
            toast.textContent = message;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.animation = "slideIn 0.3s ease reverse forwards";
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }
        
        window.onload = init;
    </script>
</body>
</html>"""
        return html_content

    port_str = os.environ.get("PORT", "8000")
    try:
        port = int(port_str)
    except ValueError:
        port = 8000
    print(f"Starting mock server on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)

def main():
    schema_path = os.environ.get("SCHEMA_PATH", "/app/schema.json")
    result = {
        "valid": True,
        "errors": []
    }

    try:
        with open(schema_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        result["valid"] = False
        result["errors"].append(f"Schema file not found at {schema_path}")
        print(json.dumps(result))
        sys.exit(1)
    except json.JSONDecodeError as e:
        result["valid"] = False
        result["errors"].append(f"Invalid JSON syntax in schema: {str(e)}")
        print(json.dumps(result))
        sys.exit(1)

    # If SERVE mode is set, run live server
    if os.environ.get("SERVE") == "true":
        try:
            config = SystemConfig.model_validate(data)
            run_live_server(data)
            sys.exit(0)
        except Exception as e:
            result["valid"] = False
            result["errors"].append(f"Validation Error in SERVE mode: {str(e)}")
            print(json.dumps(result))
            sys.exit(1)

    # 1. Pydantic level structure & consistency check
    try:
        config = SystemConfig.model_validate(data)
    except Exception as e:
        result["valid"] = False
        result["errors"].append(f"Pydantic Validation Error: {str(e)}")
        print(json.dumps(result))
        sys.exit(0)

    # 2. SQLite Database schema execution check
    db_errors = test_db_schema(config.db)
    if db_errors:
        result["valid"] = False
        result["errors"].extend(db_errors)

    # 3. FastAPI route generation & authorization check
    api_errors = test_api_routes(config.api, config.auth)
    if api_errors:
        result["valid"] = False
        result["errors"].extend(api_errors)

    print(json.dumps(result, indent=2))
    sys.exit(0)

if __name__ == "__main__":
    main()
