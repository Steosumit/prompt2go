import os
import sys
import json
import time
import argparse
from typing import Dict, Any, List
from dotenv import load_dotenv
from langchain_community.callbacks.manager import get_openai_callback

# Add workspace dir to sys.path so we can import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.agents.crew import build_compiler_crew
from src.runtime.evaluator import RuntimeEvaluator

def run_evaluation():
    load_dotenv()
    os.environ["NON_INTERACTIVE"] = "true"

    parser = argparse.ArgumentParser(description="prompt2go Pipeline Evaluation Runner")
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=2,
        help="Limit the number of prompts to evaluate (default: 2, set to 0 to run all)."
    )
    parser.add_argument(
        "--ids",
        type=str,
        help="Comma-separated list of prompt IDs to run specifically, e.g. 1,11,15"
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=os.environ.get("MISTRAL_MODEL_NAME", "mistral/mistral-large-latest"),
        help="Mistral AI model to use (default: mistral/mistral-large-latest)."
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="evaluation_results.json",
        help="Path to save evaluation JSON results."
    )

    args = parser.parse_args()

    # Load dataset
    dataset_path = os.path.join(os.path.dirname(__file__), "dataset.json")
    try:
        with open(dataset_path, "r") as f:
            all_prompts = json.load(f)
    except FileNotFoundError:
        print(f"Error: dataset.json not found at {dataset_path}", file=sys.stderr)
        sys.exit(1)

    # Filter prompts
    selected_prompts = []
    if args.ids:
        ids_to_run = [int(x.strip()) for x in args.ids.split(",") if x.strip().isdigit()]
        selected_prompts = [p for p in all_prompts if p["id"] in ids_to_run]
    elif args.limit > 0:
        selected_prompts = all_prompts[:args.limit]
    else:
        selected_prompts = all_prompts

    if not selected_prompts:
        print("No prompts selected for evaluation.")
        sys.exit(0)

    # Verify API Key
    if not os.environ.get("MISTRAL_API_KEY"):
        print("Error: MISTRAL_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print(f"       prompt2go: Evaluation Suite ({len(selected_prompts)} prompts)")
    print("=" * 70)
    print(f"Model: {args.model}")
    print("-" * 70)

    # Initialize evaluator
    workspace_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    evaluator = RuntimeEvaluator(workspace_dir)

    results = []
    summary = {
        "total_run": 0,
        "success_count": 0,
        "failed_count": 0,
        "total_tokens_used": 0,
        "total_cost_usd": 0.0,
        "total_latency_seconds": 0.0,
        "failures_by_type": {
            "JSON Syntax": 0,
            "Consistency": 0,
            "Runtime Error": 0
        }
    }

    try:
        from crewai import LLM
        llm = LLM(model=args.model)
    except Exception as e:
        print(f"Warning: Could not initialize CrewAI LLM: {e}. Defaulting to default crew LLM.")
        llm = None

    # Build the compiler crew
    crew = build_compiler_crew(llm=llm)

    for item in selected_prompts:
        print(f"\n[ID {item['id']}] Category: {item['category']}")
        print(f"Prompt: {item['prompt']}")
        print("Compiling...")

        start_time = time.time()
        
        # Track LLM token usage and cost
        with get_openai_callback() as cb:
            try:
                # Kick off pipeline
                crew_output = crew.kickoff(inputs={"prompt": item["prompt"]})
                
                # Parse output
                output_str = str(crew_output).strip()
                if output_str.startswith("```json"):
                    output_str = output_str.split("```json", 1)[1].rsplit("```", 1)[0].strip()
                elif output_str.startswith("```"):
                    output_str = output_str.split("```", 1)[1].rsplit("```", 1)[0].strip()
                
                output_str = output_str.strip()
                
                # Step 1: JSON syntax check
                try:
                    parsed_json = json.loads(output_str)
                    syntax_ok = True
                    syntax_err = None
                except json.JSONDecodeError as je:
                    syntax_ok = False
                    syntax_err = f"JSON Decode Error: {str(je)}"
                    parsed_json = None

                # Run validation
                if syntax_ok and parsed_json:
                    # Let the evaluator do consistency + runtime evaluation
                    eval_res = evaluator.evaluate_schema(parsed_json)
                    valid = eval_res.get("valid", False)
                    errors = eval_res.get("errors", [])
                else:
                    valid = False
                    errors = [syntax_err]
                    eval_res = {"valid": False, "errors": [syntax_err]}

            except Exception as pipeline_err:
                valid = False
                errors = [f"Pipeline exception: {str(pipeline_err)}"]
                eval_res = {"valid": False, "errors": errors}
                output_str = ""
                syntax_ok = False

            latency = time.time() - start_time
            
            tokens = cb.total_tokens
            cost = cb.total_cost

        # Determine failure type
        fail_type = None
        if not valid:
            if not syntax_ok:
                fail_type = "JSON Syntax"
            elif any("ValidationError" in str(e) or "consistency" in str(e).lower() for e in errors):
                fail_type = "Consistency"
            else:
                fail_type = "Runtime Error"

        # Update metrics
        summary["total_run"] += 1
        if valid:
            summary["success_count"] += 1
        else:
            summary["failed_count"] += 1
            if fail_type:
                summary["failures_by_type"][fail_type] += 1
        
        summary["total_tokens_used"] += tokens
        summary["total_cost_usd"] += cost
        summary["total_latency_seconds"] += latency

        result_item = {
            "id": item["id"],
            "category": item["category"],
            "prompt": item["prompt"],
            "success": valid,
            "failure_type": fail_type,
            "errors": errors,
            "latency_seconds": round(latency, 2),
            "tokens_used": tokens,
            "cost_usd": round(cost, 6)
        }
        results.append(result_item)

        print(f"Result: {'SUCCESS' if valid else 'FAILED'}")
        if not valid:
            print(f"Failure Type: {fail_type}")
            print(f"Errors: {errors}")
        print(f"Latency: {latency:.2f}s | Tokens: {tokens} | Cost: ${cost:.6f}")
        print("-" * 50)

    # Save results
    output_data = {
        "summary": summary,
        "results": results
    }
    
    with open(args.output, "w") as f:
        json.dump(output_data, f, indent=2)

    # Print summary report
    print("\n" + "=" * 70)
    print("                        EVALUATION SUMMARY")
    print("=" * 70)
    print(f"Total Evaluated:        {summary['total_run']}")
    print(f"Success Count:          {summary['success_count']} ({summary['success_count']/summary['total_run']*100:.1f}%)")
    print(f"Failed Count:           {summary['failed_count']}")
    if summary['failed_count'] > 0:
        print("  Failures by type:")
        for ft, fc in summary["failures_by_type"].items():
            print(f"    - {ft}: {fc}")
    print(f"Total Latency:          {summary['total_latency_seconds']:.2f}s (avg: {summary['total_latency_seconds']/summary['total_run']:.2f}s/prompt)")
    print(f"Total Token Usage:      {summary['total_tokens_used']} (avg: {summary['total_tokens_used']/summary['total_run']:.1f}/prompt)")
    print(f"Total Cost:             ${summary['total_cost_usd']:.6f} (avg: ${summary['total_cost_usd']/summary['total_run']:.6f}/prompt)")
    print("=" * 70)

    # Generate Markdown report
    md_report_path = os.path.join(workspace_dir, "evaluation_report.md")
    with open(md_report_path, "w") as f:
        f.write("# prompt2go AI Compiler Evaluation Report\n\n")
        f.write(f"**Execution Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Model**: `{args.model}`\n\n")
        
        f.write("## Summary Metrics\n\n")
        f.write("| Metric | Value |\n")
        f.write("| --- | --- |\n")
        f.write(f"| Total Prompts Evaluated | {summary['total_run']} |\n")
        f.write(f"| Compile Success Rate | {summary['success_count'] / summary['total_run'] * 100:.1f}% ({summary['success_count']}/{summary['total_run']}) |\n")
        f.write(f"| Total LLM Tokens Used | {summary['total_tokens_used']} |\n")
        f.write(f"| Total LLM Cost (USD) | ${summary['total_cost_usd']:.6f} |\n")
        f.write(f"| Total Latency (Seconds) | {summary['total_latency_seconds']:.2f}s |\n")
        f.write(f"| Average Latency per Prompt | {summary['total_latency_seconds'] / summary['total_run']:.2f}s |\n\n")
        
        f.write("## Failure Breakdown\n\n")
        f.write("| Failure Type | Count |\n")
        f.write("| --- | --- |\n")
        for ft, fc in summary["failures_by_type"].items():
            f.write(f"| {ft} | {fc} |\n")
        f.write("\n")
        
        f.write("## Detailed Results\n\n")
        f.write("| ID | Category | Prompt Preview | Success | Failure Type | Latency (s) | Cost ($) |\n")
        f.write("| --- | --- | --- | --- | --- | --- | --- |\n")
        for r in results:
            preview = r["prompt"][:40] + "..." if len(r["prompt"]) > 40 else r["prompt"]
            success_str = "✅ Yes" if r["success"] else "❌ No"
            fail_type_str = r["failure_type"] if r["failure_type"] else "-"
            f.write(f"| {r['id']} | {r['category']} | {preview} | {success_str} | {fail_type_str} | {r['latency_seconds']}s | ${r['cost_usd']:.6f} |\n")

    print(f"\nMarkdown report written to: {md_report_path}")

if __name__ == "__main__":
    run_evaluation()
