import os
import json
import subprocess
import shutil
import tempfile
from typing import Dict, Any

class RuntimeEvaluator:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.docker_dir = os.path.join(workspace_dir, "docker")
        self.image_tag = "prompt2go-validator:latest"
        self._docker_available = None

    def check_docker_available(self) -> bool:
        """Check if Docker CLI and daemon are available."""
        if self._docker_available is not None:
            return self._docker_available

        try:
            res = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
            self._docker_available = (res.returncode == 0)
        except Exception:
            self._docker_available = False

        return self._docker_available

    def build_docker_image(self) -> bool:
        """Build the validator docker image."""
        if not self.check_docker_available():
            return False

        print(f"Building Docker image '{self.image_tag}'...")
        try:
            res = subprocess.run(
                ["docker", "build", "-t", self.image_tag, "."],
                cwd=self.docker_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to build Docker image: {e.stderr}")
            return False

    def evaluate_schema(self, schema_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate a schema using Docker sandbox, falling back to local Python run
        if Docker is not available.
        """
        tmp_dir = os.path.join(self.workspace_dir, ".tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        
        fd, schema_file_path = tempfile.mkstemp(suffix=".json", dir=tmp_dir)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(schema_data, f, indent=2)

            if self.check_docker_available():
                self.build_docker_image()
                abs_schema_path = os.path.abspath(schema_file_path)
                
                cmd = [
                    "docker", "run", "--rm",
                    "-v", f"{abs_schema_path}:/app/schema.json:ro",
                    self.image_tag
                ]
                
                try:
                    res = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False
                    )
                    
                    if res.returncode == 0:
                        try:
                            return json.loads(res.stdout)
                        except json.JSONDecodeError:
                            return {
                                "valid": False,
                                "errors": [f"Failed to parse Docker output as JSON: {res.stdout}"]
                            }
                    else:
                        err_msg = res.stderr or res.stdout
                        return {
                            "valid": False,
                            "errors": [f"Docker container execution error: {err_msg}"]
                        }
                except Exception as e:
                    print(f"Docker evaluation crashed: {str(e)}. Falling back to local execution.")
            
            return self._evaluate_locally(schema_file_path)

        finally:
            if os.path.exists(schema_file_path):
                os.remove(schema_file_path)

    def _evaluate_locally(self, schema_file_path: str) -> Dict[str, Any]:
        """Fallback evaluation running test_runner.py directly in local python env."""
        test_runner_path = os.path.join(self.docker_dir, "test_runner.py")
        local_venv_python = os.path.join(self.workspace_dir, ".venv", "bin", "python")
        python_exec = local_venv_python if os.path.exists(local_venv_python) else "python3"
        
        env = os.environ.copy()
        env["SCHEMA_PATH"] = schema_file_path
        
        try:
            res = subprocess.run(
                [python_exec, test_runner_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                check=False
            )
            
            if res.returncode == 0 or res.stdout:
                try:
                    return json.loads(res.stdout)
                except json.JSONDecodeError:
                    return {
                        "valid": False,
                        "errors": [f"Failed to parse local test runner output: {res.stdout}. Stderr: {res.stderr}"]
                    }
            else:
                return {
                    "valid": False,
                    "errors": [f"Local test runner failed with exit code {res.returncode}. Stderr: {res.stderr}"]
                }
        except Exception as e:
            return {
                "valid": False,
                "errors": [f"Local execution error: {str(e)}"]
            }

    def serve_schema(self, schema_data: Dict[str, Any], port: int = 8000) -> Any:
        """
        Starts the compiled schema as a live mock application (FastAPI + HTML frontend).
        Exposes it on the specified host port.
        """
        tmp_dir = os.path.join(self.workspace_dir, ".tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        
        schema_file_path = os.path.join(tmp_dir, "server_schema.json")
        with open(schema_file_path, "w") as f:
            json.dump(schema_data, f, indent=2)

        class ServerProcess:
            def __init__(self, proc, is_docker=False, container_name=None, schema_path=None):
                self.proc = proc
                self.is_docker = is_docker
                self.container_name = container_name
                self.schema_path = schema_path

            def stop(self):
                if self.is_docker and self.container_name:
                    print(f"Stopping Docker container '{self.container_name}'...")
                    subprocess.run(["docker", "stop", self.container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run(["docker", "rm", self.container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif self.proc:
                    print("Stopping local mock server process...")
                    self.proc.terminate()
                    self.proc.wait()
                
                if self.schema_path and os.path.exists(self.schema_path):
                    try:
                        os.remove(self.schema_path)
                    except Exception:
                        pass

        if self.check_docker_available():
            self.build_docker_image()
            abs_schema_path = os.path.abspath(schema_file_path)
            container_name = f"prompt2go-server-{port}"
            
            # Stop existing container if any
            subprocess.run(["docker", "stop", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "rm", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            cmd = [
                "docker", "run", "-d",
                "--name", container_name,
                "-p", f"{port}:8000",
                "-e", "SERVE=true",
                "-v", f"{abs_schema_path}:/app/schema.json:ro",
                self.image_tag
            ]
            
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                return ServerProcess(None, is_docker=True, container_name=container_name, schema_path=schema_file_path)
            except Exception as e:
                print(f"Failed to start Docker container: {e}. Falling back to local execution.")

        # Local fallback Popen serving
        test_runner_path = os.path.join(self.docker_dir, "test_runner.py")
        local_venv_python = os.path.join(self.workspace_dir, ".venv", "bin", "python")
        python_exec = local_venv_python if os.path.exists(local_venv_python) else "python3"
        
        env = os.environ.copy()
        env["SCHEMA_PATH"] = schema_file_path
        env["SERVE"] = "true"
        env["PORT"] = str(port)
        
        # Start local subprocess in background
        try:
            proc = subprocess.Popen(
                [python_exec, test_runner_path],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return ServerProcess(proc, is_docker=False, schema_path=schema_file_path)
        except Exception as e:
            if os.path.exists(schema_file_path):
                os.remove(schema_file_path)
            raise RuntimeError(f"Failed to start local mock server: {str(e)}")
