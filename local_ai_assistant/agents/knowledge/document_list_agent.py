import os

# Always resolve relative to the project root, not the CWD
_AGENT_KN_DIR = os.path.dirname(os.path.abspath(__file__))     # agents/knowledge
_PROJECT_ROOT  = os.path.dirname(os.path.dirname(_AGENT_KN_DIR))  # project root
_DEFAULT_DOCS_PATH = os.path.join(_PROJECT_ROOT, "data", "documents")

# Only show user-created document files; exclude system/internal files
_DOCUMENT_EXTENSIONS = {'.pdf', '.csv', '.txt', '.docx', '.doc', '.xlsx', '.xls', '.png', '.jpg', '.jpeg'}


def list_all_documents(base_path=None):
    if base_path is None:
        base_path = _DEFAULT_DOCS_PATH
    if not os.path.exists(base_path):
        return "No documents folder found."

    files = [
        f for f in sorted(os.listdir(base_path))
        if os.path.isfile(os.path.join(base_path, f))
        and os.path.splitext(f)[1].lower() in _DOCUMENT_EXTENSIONS
    ]
    if not files:
        return "No documents found."

    result = [f"• {f}" for f in files]
    return "Here are your documents:\n" + "\n".join(result)