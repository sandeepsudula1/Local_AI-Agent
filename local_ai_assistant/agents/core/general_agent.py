from __future__ import annotations

from typing import Optional

import ollama


def handle_general(
    user_input: str,
    model_name: str,
    temperature: float = 0.7,
    num_predict: int = 250,
    system_extra: str = "",
) -> Optional[str]:

    system_prompt = (
        "You are a smart, friendly personal AI assistant (like Siri or Google Assistant) "
        "running fully offline. You answer conversationally and concisely. "
        "For simple questions give a short direct answer (1-3 sentences). "
        "For factual questions be accurate. "
        "Never say you cannot help — always try your best."
    )
    if system_extra:
        system_prompt += system_extra

    response = ollama.chat(
        model=model_name,
        options={
            "temperature": temperature,
            "num_predict": num_predict,
        },
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_input
            }
        ]
    )

    return response["message"]["content"]
