import streamlit as st
import os
import sys
import json
import zipfile
import io
import time
from io import StringIO

# Add project root to sys.path for local imports
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
from crewai import LLM
from src.agents.crew import build_compiler_crew

# Force non-interactive execution for agents
os.environ["NON_INTERACTIVE"] = "true"

# Page configuration
st.set_page_config(
    page_title="prompt2go - AI Application Compiler",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load environment variables
load_dotenv()

# Custom CSS for modern glassmorphism design
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap');
    
    .stApp {
        background-color: #080b11;
        color: #f3f4f6;
        font-family: 'Outfit', sans-serif;
    }
    
    .header-container {
        background: rgba(15, 23, 42, 0.45);
        border: 1px solid rgba(255, 255, 255, 0.08);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        padding: 2.5rem;
        border-radius: 20px;
        margin-bottom: 2rem;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        background-image: radial-gradient(circle at top right, rgba(139, 92, 246, 0.15), transparent 400px),
                          radial-gradient(circle at bottom left, rgba(6, 182, 212, 0.1), transparent 400px);
    }
    .header-title {
        font-size: 2.8rem;
        font-weight: 800;
        background: linear-gradient(135deg, #06b6d4 0%, #8b5cf6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
        letter-spacing: -0.03em;
    }
    .header-subtitle {
        font-size: 1.1rem;
        color: #9ca3af;
        font-weight: 400;
        line-height: 1.6;
    }
    
    div.stButton > button {
        background: linear-gradient(135deg, #06b6d4 0%, #8b5cf6 100%) !important;
        color: white !important;
        font-weight: 600 !important;
        border: none !important;
        padding: 0.75rem 2rem !important;
        border-radius: 10px !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 4px 15px rgba(139, 92, 246, 0.3) !important;
        width: 100% !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    div.stButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 20px rgba(6, 182, 212, 0.4) !important;
    }
    
    .stCodeBlock {
        border-radius: 12px !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
    }
    
    section[data-testid="stSidebar"] {
        background-color: #0c1017 !important;
        border-right: 1px solid rgba(255, 255, 255, 0.05) !important;
    }
    
    .logo-badge {
        background: linear-gradient(135deg, #06b6d4 0%, #8b5cf6 100%);
        padding: 0.25rem 0.75rem;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.75rem;
        text-transform: uppercase;
        color: white;
        display: inline-block;
        margin-bottom: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)

# Stdout redirection class to stream compilation progress
class StreamlitStdout:
    def __init__(self, container):
        self.container = container
        self.buffer = StringIO()
        
    def write(self, text):
        self.buffer.write(text)
        val = self.buffer.getvalue()
        lines = val.splitlines()
        last_lines = "\n".join(lines[-25:])
        self.container.code(last_lines, language="text")
        
    def flush(self):
        pass

# Helper to build a ZIP archive of generated files
def build_zip_archive(src_dir):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(src_dir):
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, src_dir)
                zip_file.write(file_path, rel_path)
    return zip_buffer.getvalue()

# Header
st.markdown("""
<div class="header-container">
    <div class="logo-badge">AI Code Compiler</div>
    <div class="header-title">prompt2go</div>
    <div class="header-subtitle">
        Translate natural language application descriptions into containerized, physical web applications featuring a FastAPI backend, SQLite database schemas, and a glassmorphism HTML dashboard.
    </div>
</div>
""", unsafe_allow_html=True)

# API Key check
api_key = os.environ.get("MISTRAL_API_KEY")
if not api_key and "MISTRAL_API_KEY" in st.secrets:
    api_key = st.secrets["MISTRAL_API_KEY"]

# Sidebar settings
st.sidebar.markdown("### ⚙️ Compiler Configuration")

if not api_key:
    api_key_input = st.sidebar.text_input("Mistral API Key", type="password", help="Enter your Mistral API Key to authorize LLM operations.")
    if api_key_input:
        api_key = api_key_input
        os.environ["MISTRAL_API_KEY"] = api_key
else:
    st.sidebar.success("🔑 API Key configured.")

model = st.sidebar.selectbox(
    "Compilation LLM Model",
    ["mistral/mistral-large-latest", "mistral/mistral-medium-latest", "mistral/open-mixtral-8x22b"],
    help="Select the model to run the multi-agent system specification and validation crew."
)

output_schema_name = st.sidebar.text_input("Output Schema File", value="output_schema.json")

# Main Page Form
st.markdown("### 📝 Describe Your Web Application")
prompt = st.text_area(
    "Application Description & Specifications",
    height=150,
    placeholder="Example: Create a task management system with tables tasks (id, title, description, status, due_date) and members (id, name, email). Roles: admin (full access to both tables), user (full access to tasks and read-only to members).",
    help="Define the tables, fields, constraints, routes, roles, and UI structure."
)

if st.button("🚀 Compile Codebase"):
    if not api_key:
        st.error("Please configure your Mistral API Key in the sidebar or via secrets before compiling.")
    elif not prompt.strip():
        st.error("Please enter a description for the web application.")
    else:
        # Live status updates
        status_box = st.status("🏗️ Starting Compilation Pipeline...", expanded=True)
        stdout_container = status_box.empty()
        
        # Build the crew and start execution
        old_stdout = sys.stdout
        sys.stdout = StreamlitStdout(stdout_container)
        
        try:
            llm = LLM(model=model)
            crew = build_compiler_crew(llm=llm)
            
            status_box.update(label="🤖 Running Multi-Agent requirements analysis and validation...", state="running")
            result = crew.kickoff(inputs={"prompt": prompt})
            
            # Reset stdout
            sys.stdout = old_stdout
            
            result_str = str(result).strip()
            
            # Clean markdown JSON wraps
            if result_str.startswith("```json"):
                result_str = result_str.split("```json", 1)[1].rsplit("```", 1)[0].strip()
            elif result_str.startswith("```"):
                result_str = result_str.split("```", 1)[1].rsplit("```", 1)[0].strip()
            
            # Parse result
            schema_data = json.loads(result_str)
            
            # Write JSON output
            with open(output_schema_name, "w") as f:
                json.dump(schema_data, f, indent=2)
                
            status_box.update(label="💻 Compiling physical FastAPI application and database files...", state="running")
            
            output_dir = "generated_app"
            compile_schema(schema_data, output_dir)
            
            status_box.update(label="🎉 Application Successfully Compiled!", state="complete", expanded=False)
            st.success(f"System specification successfully written to **{output_schema_name}** and codebase generated in **/generated_app**.")
            
            # Code view and downloads
            st.markdown("### 📂 Generated Codebase")
            
            col1, col2 = st.columns([3, 1])
            with col2:
                # Zip and download button
                zip_data = build_zip_archive(output_dir)
                st.download_button(
                    label="📦 Download Complete App ZIP",
                    data=zip_data,
                    file_name="generated_app.zip",
                    mime="application/zip",
                    use_container_width=True
                )
                
                # Show instructions
                st.markdown("""
                #### 🚀 How to Run Locally
                1. Extract the downloaded ZIP archive.
                2. Install dependencies:
                   ```bash
                   pip install -r requirements.txt
                   ```
                3. Launch the FastAPI server:
                   ```bash
                   uvicorn main:app --reload
                   ```
                4. Open your browser at **http://localhost:8000** to interact with the live dashboard.
                """)
            
            with col1:
                # File Tabs preview
                files = {
                    "main.py": ("python", "main.py"),
                    "static/index.html": ("html", "static/index.html"),
                    "Dockerfile": ("dockerfile", "Dockerfile"),
                    "requirements.txt": ("text", "requirements.txt")
                }
                
                tab_names = list(files.keys())
                tabs = st.tabs(tab_names)
                
                for tab, (lang, rel_path) in zip(tabs, files.values()):
                    full_path = os.path.join(output_dir, rel_path)
                    if os.path.exists(full_path):
                        with open(full_path, "r", encoding="utf-8") as f:
                            code_content = f.read()
                        with tab:
                            st.code(code_content, language=lang)
                            st.download_button(
                                label=f"📥 Download {os.path.basename(rel_path)}",
                                data=code_content,
                                file_name=os.path.basename(rel_path),
                                mime="text/plain",
                                key=f"dl_{rel_path.replace('/', '_')}"
                            )
                            
        except Exception as e:
            sys.stdout = old_stdout
            status_box.update(label="❌ Compilation Failed!", state="error")
            st.error(f"Error occurred during compiling: {str(e)}")
