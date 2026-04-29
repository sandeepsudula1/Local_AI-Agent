from __future__ import annotations

from typing import Optional
import ollama
import re

from configs.llm_config import MODEL, TEMPERATURE, MAX_TOKENS


# ---------------------------------------------------------------------------
# Model Management
# ---------------------------------------------------------------------------

def _ensure_model_available(model: str):
    """PART 1: Ensure the requested model is pulled in Ollama."""
    try:
        # Check if model exists
        ollama.show(model)
    except Exception:
        print(f"[LLM] Model '{model}' not found locally. Attempting to pull...")
        try:
            ollama.pull(model)
            print(f"[LLM] Successfully pulled '{model}'.")
        except Exception as e:
            print(f"[LLM] Error pulling model: {e}. Falling back to default behavior.")


# ---------------------------------------------------------------------------
# Control Layer: Normalization & Disambiguation
# ---------------------------------------------------------------------------

def _normalize_topic(topic: str) -> str:
    """Normalize short terms into clear entities."""
    mapping = {
        "IPL": "Indian Premier League",
        "CPU": "Central Processing Unit",
        "OS": "Operating System",
        "AI": "Artificial Intelligence",
    }
    return mapping.get(topic.upper(), topic)


def _handle_ambiguity(user_input: str, active_topic: str) -> Optional[str]:
    """Checks for ambiguous terms and asks for clarification if context is missing."""
    low = user_input.lower().strip()
    if low == "ipl":
        if "cricket" in active_topic.lower() or "sports" in active_topic.lower():
            return None
        return "The term 'IPL' can refer to the **Indian Premier League (Cricket)** or **Intense Pulsed Light (Skin treatment)**. Which one are you referring to?"
    if low == "cpu":
        return "Are you asking about the **Central Processing Unit (Computer Processor)** or something else?"
    return None


# ---------------------------------------------------------------------------
# Intelligence Layer: Classification & Prompting
# ---------------------------------------------------------------------------

def _classify_query(user_input: str) -> str:
    """Classify query into EXPLANATION, FACTUAL, or AMBIGUOUS."""
    low = user_input.lower()
    if low in ["ipl", "cpu", "os", "ram"]:
        return "AMBIGUOUS"
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", low))
    has_fact_kw = any(kw in low for kw in ["who won", "winner", "result", "capital of", "born in"])
    if has_year or has_fact_kw:
        return "FACTUAL"
    return "EXPLANATION"


def build_general_prompt(user_input: str, query_type: str) -> list[dict]:
    """PART 3: ADJUST PROMPTING FOR 3B."""
    # PART 6: DEBUG LOGS
    print(f"[LLM] Using model: {MODEL}")
    print(f"[LLM] Temperature: {TEMPERATURE}")
    print(f"[QUERY TYPE] {query_type}")
    
    if query_type == "FACTUAL":
        system_prompt = (
            "You are a highly accurate assistant.\n"
            "Answer precisely and directly. Do not guess.\n"
            "If unsure, say: 'I am not fully sure about this fact.'\n"
            "Ensure any years or entities mentioned are strictly correct."
        )
    elif query_type == "EXPLANATION":
        system_prompt = (
            "You are a helpful and detailed assistant.\n"
            "Provide thorough, detailed explanations with clear steps or reasoning.\n"
            "Stay on topic and be comprehensive."
        )
    else:
        system_prompt = "You are a helpful assistant. Answer directly and precisely."
    
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ]


# ---------------------------------------------------------------------------
# Verification Layer: Post-Generation Controls
# ---------------------------------------------------------------------------

def _validate_response_light(query: str, response: str) -> bool:
    """PART 5: ADD BASIC FACT SAFETY (LIGHT CHECK).
    
    Ensure answer includes correct year reference if present in query.
    """
    low_q = query.lower()
    low_r = response.lower()
    
    years_in_q = re.findall(r"\b(19|20)\d{2}\b", low_q)
    for y in years_in_q:
        if y not in low_r:
            print(f"[VERIFY] Year {y} missing in answer.")
            return False
    return True


# ---------------------------------------------------------------------------
# Main Logic
# ---------------------------------------------------------------------------



def handle_general(
    user_input: str,
    model_name: str = MODEL, # PART 1 & 2: Default to Config
    temperature: float = TEMPERATURE,
    num_predict: int = MAX_TOKENS,
    memory=None,
    **kwargs
) -> Optional[str]:
    """Main entry point for GENERAL mode (Optimized for 3B)."""
    

    # 2. Classification
    q_type = _classify_query(user_input)
    
    # 3. Disambiguation
    if q_type == "AMBIGUOUS":
        clarification = _handle_ambiguity(user_input, "")
        if clarification:
            return clarification

    # 4. LLM Call (PART 4: REDUCE RETRIES)
    # Ensure model is available
    _ensure_model_available(model_name)
    
    messages = build_general_prompt(user_input, q_type)
    
    # PART 6: DEBUG LOG
    print(f"[LLM] Using model: {MODEL}")
    response = ollama.chat(
        model=MODEL,
        options={"temperature": temperature, "num_predict": num_predict},
        messages=messages,
    )
    response_text = response.get("message", {}).get("content", "").strip()

    # 5. Light Post-Response Validation (PART 5)
    # PART 4: No retries, no validation loops
    # Validation is now purely informative in logs (if kept) or removed.
    # For now, we just return the response_text directly as requested.

    return response_text


def handle_general_ai(*args, **kwargs):
    return handle_general(*args, **kwargs)


def build_file_prompt(ui, fc): return []
def build_graph_prompt(ui, ctx): return []
def normalize_query(text): return text.strip()
