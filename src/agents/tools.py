import json
import os
import sys
from crewai.tools import tool
from src.mcp_server.server import (
    validate_json_syntax as mcp_validate_json_syntax,
    validate_schema_consistency as mcp_validate_schema_consistency,
    trigger_docker_validation as mcp_trigger_docker_validation
)

@tool("validate_json_syntax")
def validate_json_syntax(json_str: str) -> str:
    """
    Validates that a string is syntactically valid JSON.
    Input should be the raw JSON string.
    Returns a status report with syntax correctness details.
    """
    return mcp_validate_json_syntax(json_str)

@tool("validate_schema_consistency")
def validate_schema_consistency(schema_json_str: str) -> str:
    """
    Validates that the schema JSON string is type-safe and consistent across all layers
    (Database, API, UI, Auth).
    Input should be the complete schema JSON string.
    Returns details on any cross-layer violations.
    """
    return mcp_validate_schema_consistency(schema_json_str)

@tool("trigger_docker_validation")
def trigger_docker_validation(schema_json_str: str) -> str:
    """
    Spins up the Docker runtime sandbox (or local fallback subprocess) to compile
    and execute tests against the generated schema.
    Input should be the complete schema JSON string.
    Returns real compilation errors, SQLite execution issues, and FastAPI route test errors.
    """
    # Use current working directory as the workspace directory
    workspace_dir = os.getcwd()
    return mcp_trigger_docker_validation(schema_json_str, workspace_dir=workspace_dir)

@tool("ask_user_question")
def ask_user_question(question: str) -> str:
    """
    Asks the user a follow-up question to resolve ambiguities or get clarification on requirements.
    Input should be a clear, specific question.
    Returns the user's response.
    """
    # Check if we are running in a non-interactive context (like evaluation)
    if not sys.stdin.isatty() or os.environ.get("NON_INTERACTIVE") == "true":
        return "No interactive user input is available. Please make a reasonable design assumption and continue."
    
    print(f"\n[CLARIFICATION REQUIRED] {question}")
    try:
        user_response = input("Your answer: ")
        return user_response
    except Exception as e:
        return f"Error reading user response: {str(e)}. Please make a reasonable assumption."

