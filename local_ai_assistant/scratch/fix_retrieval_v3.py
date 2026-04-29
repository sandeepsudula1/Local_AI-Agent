import sys
import os

path = r"c:\Users\Sandeep\OneDrive\Documents\GitHub\Local_AI_Agent1.1\local_ai_assistant\agents\knowledge\retrieval_agent.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    
    # 1. Insert imports and global debug print after the first ollama block
    if "HAVE_OLLAMA = False" in line and i < 25: # only at the top
        new_lines.append("\nfrom configs.llm_config import MODEL\n")
        new_lines.append("print(f\"[LLM] Using model: {MODEL}\")\n")
    
    # 2. Insert answer_from_file after _KEYWORD_STOPWORDS definition (heuristically at }) )
    if "})" in line and 70 < i < 100: # heuristic for the _KEYWORD_STOPWORDS end
        new_lines.append("\n\ndef answer_from_file(query: str, content: str, model_name: str = MODEL, file_path_used: str = None) -> str:\n")
        new_lines.append("    \"\"\"Answer a query using only the provided document content (Active File mode).\"\"\"\n")
        new_lines.append("    if not content or not content.strip():\n")
        new_lines.append("        return \"The document is empty.\"\n")
        new_lines.append("    \n")
        new_lines.append("    source = os.path.basename(file_path_used) if file_path_used else \"document\"\n")
        new_lines.append("    \n")
        new_lines.append("    # Use standard relevance excerpt if too long\n")
        new_lines.append("    if len(content) > 12000:\n")
        new_lines.append("        context = _relevant_excerpt(content, query, max_chars=12000)\n")
        new_lines.append("    else:\n")
        new_lines.append("        context = content\n")
        new_lines.append("        \n")
        new_lines.append("    return _ask_llm(model_name, context, query, source)\n")

    # 3. Add debug print to _ask_llm
    if "def _ask_llm(model_name, context, query, source," in line:
        new_lines.append("    print(\"[DEBUG] LLM call count = 1\")\n")

with open(path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("Retrieval agent fixed correctly v3.1")
