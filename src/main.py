import os
import sys
import argparse
import json
import time
from dotenv import load_dotenv

# Add project root to sys.path so we can import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agents.crew import build_compiler_crew
from src.runtime.evaluator import RuntimeEvaluator

def main():
    # Load environment variables from .env file
    load_dotenv()

    parser = argparse.ArgumentParser(description="prompt2go: AI Engineer Compiler Pipeline")
    parser.add_argument(
        "--prompt", "-p",
        type=str,
        required=True,
        help="Natural language prompt describing the web application / system requirements."
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="output_schema.json",
        help="Path where the final compiled JSON schema will be saved."
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=os.environ.get("MISTRAL_MODEL_NAME", "mistral/mistral-large-latest"),
        help="Mistral AI model to use (default: mistral/mistral-large-latest)."
    )
    parser.add_argument(
        "--serve", "-s",
        action="store_true",
        help="Launch a live mock server presenting the compiled application UI and REST backend."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to run the live mock server on (default: 8000)."
    )

    args = parser.parse_args()

    # Verify API key is set
    if not os.environ.get("MISTRAL_API_KEY"):
        print("Error: MISTRAL_API_KEY environment variable is not set.", file=sys.stderr)
        print("Please create a .env file or export MISTRAL_API_KEY=your-key", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("      prompt2go: Starting AI Compiler Pipeline")
    print("=" * 60)
    print(f"Prompt: {args.prompt}")
    print(f"Model:  {args.model}")
    print(f"Output: {args.output}")
    if args.serve:
        print(f"Serve:  Enabled on port {args.port}")
    print("-" * 60)

    try:
        from crewai import LLM
        llm = LLM(model=args.model)
    except Exception as e:
        print(f"Warning: Could not initialize CrewAI LLM: {e}. Defaulting to CrewAI default LLM.")
        llm = None

    # Build the crew
    crew = build_compiler_crew(llm=llm)

    print("Compiling system specifications (this may take a few minutes)...")
    try:
        # Run CrewAI sequential process
        result = crew.kickoff(inputs={"prompt": args.prompt})
        
        result_str = str(result).strip()
        
        # Clean markdown wrappers
        if result_str.startswith("```json"):
            result_str = result_str.split("```json", 1)[1].rsplit("```", 1)[0].strip()
        elif result_str.startswith("```"):
            result_str = result_str.split("```", 1)[1].rsplit("```", 1)[0].strip()
            
        result_str = result_str.strip()

        # Parse it to verify it is valid JSON
        try:
            schema_data = json.loads(result_str)
            
            # Save final compiled schema
            with open(args.output, "w") as f:
                json.dump(schema_data, f, indent=2)
                
            print("-" * 60)
            print(f"Success! Final validated schema compiled and saved to: {args.output}")
            print("=" * 60)
            
            # Start Live Mock Server if requested
            if args.serve:
                print("\n" + "=" * 60)
                print("      STARTING MOCK APPLICATION LIVE SERVER")
                print("=" * 60)
                
                evaluator = RuntimeEvaluator(os.getcwd())
                server = evaluator.serve_schema(schema_data, port=args.port)
                
                print(f"Server is running! Open your browser and navigate to:")
                print(f"\n      >>> http://localhost:{args.port} <<<\n")
                print("Press Ctrl+C to terminate the application and release the port.")
                print("=" * 60)
                
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nShutting down mock application live server...")
                finally:
                    server.stop()
                    print("Cleanup complete. Exiting.")
                    
        except json.JSONDecodeError as je:
            print("-" * 60)
            print(f"Error: Final output was not valid JSON: {je}", file=sys.stderr)
            print(f"Raw Output: {result_str}", file=sys.stderr)
            print("Saving raw output to compilation_failed_output.txt", file=sys.stderr)
            with open("compilation_failed_output.txt", "w") as f:
                f.write(result_str)
            sys.exit(1)

    except Exception as e:
        print(f"Compilation pipeline failed with error: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
