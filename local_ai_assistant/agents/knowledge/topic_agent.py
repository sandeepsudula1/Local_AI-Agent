import ollama
from configs.llm_config import MODEL

def handle_topics(documents, model_name):

    all_text = "\n\n".join([doc.page_content for doc in documents])
    limited_text = all_text[:8000]

    print(f"[LLM] Using model: {MODEL}")
    response = ollama.chat(
        model=MODEL,
        options={
            "temperature": 0.2,
            "num_predict": 300
        },
        messages=[
            {
                "role": "system",
                "content": "Identify and group the main topics covered across all documents. Use bullet points."
            },
            {
                "role": "user",
                "content": limited_text
            }
        ]
    )

    return response["message"]["content"]
