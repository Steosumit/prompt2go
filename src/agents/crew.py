from crewai import Agent, Task, Crew, Process
from src.agents.tools import (
    validate_json_syntax,
    validate_schema_consistency,
    trigger_docker_validation,
    ask_user_question
)

def build_compiler_crew(llm=None) -> Crew:
    """
    Builds and returns the CrewAI crew containing the compiler agents and tasks.
    """
    # 1. Define Agents
    intent_extractor = Agent(
        role="Intent Extractor",
        goal="Extract system requirements and technical specifications from a user prompt, asking clarifying questions if critical information is missing.",
        backstory=(
            "You are an expert systems analyst. Your job is to read user requests, extract "
            "concrete data fields, API routes, user roles, and UI component trees. If any crucial "
            "details are missing or highly ambiguous, use the `ask_user_question` tool to ask the user "
            "for clarification, rather than just guessing. Only ask if really necessary to design a correct system."
        ),
        tools=[ask_user_question],
        verbose=True,
        llm=llm
    )

    system_architect = Agent(
        role="System Architect",
        goal="Design a comprehensive, consistent system architecture based on requirements.",
        backstory=(
            "You are a senior system architect. You design clean relational database tables, "
            "REST API endpoints, role-based access rules, and corresponding UI layouts. You ensure "
            "that every API route maps to a DB action, and every UI action maps to an API endpoint."
        ),
        verbose=True,
        llm=llm
    )

    schema_generator = Agent(
        role="Schema Generator",
        goal="Translate system architectures into valid, strict JSON configurations matching the SystemConfig schema.",
        backstory=(
            "You are a detailed schema engineer. You translate system designs into strict JSON schemas. "
            "You ensure that all fields (DB, API, UI, Auth) are formatted correctly and represent a "
            "fully specified web application config."
        ),
        verbose=True,
        llm=llm
    )

    repair_specialist = Agent(
        role="Refinement and Repair Specialist",
        goal="Validate schemas and iteratively repair any syntax, consistency, or runtime errors.",
        backstory=(
            "You are a QA and compiler engineer. You run validation tools on generated JSON schemas. "
            "If any errors are found (JSON syntax, cross-layer inconsistencies, or SQLite compilation "
            "and FastAPI route execution crashes in the sandbox), you locate the exact failure in the "
            "schema, fix it, and re-run tests until it is fully verified and clean."
        ),
        tools=[validate_json_syntax, validate_schema_consistency, trigger_docker_validation],
        verbose=True,
        llm=llm
    )

    # 2. Define Tasks
    task_extract = Task(
        description=(
            "Extract requirements and specifications from this product idea prompt: '{prompt}'.\n"
            "Identify the database tables, API routes, roles, and UI elements needed.\n"
            "If the prompt is missing key details or contains major ambiguities, use your `ask_user_question` "
            "tool to ask the user. Output a markdown spec doc listing all requirements."
        ),
        expected_output="A structured markdown requirements specification document.",
        agent=intent_extractor
    )

    task_design = Task(
        description=(
            "Using the requirements spec, design the complete system architecture.\n"
            "Detail all DB tables (with fields, types, and primary/foreign keys), REST API endpoints "
            "(paths, methods, request/response models, auth required), user roles, and UI layout "
            "(components, their parents, and API/state bindings)."
        ),
        expected_output="A detailed architecture design document with cross-layer mappings.",
        agent=system_architect
    )

    task_generate = Task(
        description=(
            "Translate the architecture design into a single JSON schema matching the SystemConfig model.\n"
            "Double-check that you include the top-level keys: 'system_name', 'db', 'api', 'ui', 'auth'.\n"
            "Make sure it is outputted as a raw JSON string."
        ),
        expected_output="A single JSON string containing the complete SystemConfig schema.",
        agent=schema_generator
    )

    task_repair = Task(
        description=(
            "Validate and polish the generated JSON schema using your tools.\n"
            "First, check JSON syntax with `validate_json_syntax`.\n"
            "Second, verify cross-layer consistency with `validate_schema_consistency`.\n"
            "Third, execute runtime test compilation and tests in the sandbox with `trigger_docker_validation`.\n"
            "If any tool returns validation errors, fix the JSON schema and test it again.\n"
            "Repeat this loop until all validation tools report that the schema is 100% valid.\n"
            "Your final output MUST be the raw, validated JSON schema string only."
        ),
        expected_output="A single valid and verified JSON schema string matching the SystemConfig model.",
        agent=repair_specialist
    )

    # 3. Create Crew
    compiler_crew = Crew(
        agents=[intent_extractor, system_architect, schema_generator, repair_specialist],
        tasks=[task_extract, task_design, task_generate, task_repair],
        process=Process.sequential,
        verbose=True
    )

    return compiler_crew
