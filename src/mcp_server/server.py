import os
import sys
import json
from typing import Any, Dict
from mcp.server.fastmcp import FastMCP

# Add project root to sys.path so we can import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.schemas.models import SystemConfig
from src.runtime.evaluator import RuntimeEvaluator

# Create FastMCP server
mcp = FastMCP("prompt2go-validator")

@mcp.tool()
def validate_json_syntax(json_str: str) -> str:
    """
    Validates that a string is syntactically valid JSON.
    Returns a JSON string containing the result status and any syntax errors.
    """
    try:
        parsed = json.loads(json_str)
        return json.dumps({
            "valid": True,
            "message": "JSON syntax is valid.",
            "data_preview": str(parsed)[:200] + "..." if len(str(parsed)) > 200 else str(parsed)
        }, indent=2)
    except json.JSONDecodeError as e:
        return json.dumps({
            "valid": False,
            "error_type": "JSONDecodeError",
            "message": str(e),
            "line": e.lineno,
            "column": e.colno
        }, indent=2)

@mcp.tool()
def validate_schema_consistency(schema_json_str: str) -> str:
    """
    Parses the schema JSON string against the Pydantic SystemConfig model.
    This runs strict types verification and cross-layer consistency validation
    (e.g., verifying DB tables reference valid tables/fields, role verification,
    and UI bindings verification).
    """
    try:
        data = json.loads(schema_json_str)
    except json.JSONDecodeError as e:
        return json.dumps({
            "valid": False,
            "error_type": "JSONDecodeError",
            "message": f"Cannot run consistency checks due to syntax error: {str(e)}"
        }, indent=2)

    try:
        # Pydantic model validation + model validator triggers
        config = SystemConfig.model_validate(data)
        return json.dumps({
            "valid": True,
            "message": "Pydantic schema structure and cross-layer consistency checks passed successfully!"
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "valid": False,
            "error_type": "ValidationError",
            "message": str(e)
        }, indent=2)

@mcp.tool()
def trigger_docker_validation(schema_json_str: str, workspace_dir: str = ".") -> str:
    """
    Triggers full runtime validation of the schema in a sandboxed Docker container
    (or local fallback subprocess if Docker is not available).
    This creates actual SQLite database tables and spins up FastAPI routes to
    physically execute query/auth scenarios against the generated spec.
    """
    try:
        data = json.loads(schema_json_str)
    except json.JSONDecodeError as e:
        return json.dumps({
            "valid": False,
            "errors": [f"Cannot run Docker validation due to JSON syntax error: {str(e)}"]
        }, indent=2)

    # Use absolute path for workspace directory to avoid CWD mismatch issues
    abs_workspace = os.path.abspath(workspace_dir)
    evaluator = RuntimeEvaluator(abs_workspace)
    
    try:
        result = evaluator.evaluate_schema(data)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({
            "valid": False,
            "errors": [f"Runtime evaluation exception: {str(e)}"]
        }, indent=2)

import os
if __name__ == "__main__":
    mcp.run()
