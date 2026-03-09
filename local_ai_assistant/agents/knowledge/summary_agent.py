import ollama

def handle_summary(documents, model_name):

    # Take small portion from each document (balanced sampling)
    collected_text = []

    for doc in documents:
        content = doc.page_content.strip()
        if content:
            collected_text.append(content[:500])  # take first 500 chars per doc

    # Limit total size
    combined_text = "\n\n".join(collected_text[:30])  # limit to 30 docs

    response = ollama.chat(
        model=model_name,
        options={
            "temperature": 0.0,
            "num_predict": 250
        },
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a strict document summarizer. "
                    "Write a SHORT summary (5 to 8 lines maximum) covering ONLY the key themes explicitly stated in the provided documents. "
                    "Mention each document's main topic in one sentence. "
                    "Do NOT include any information that is not explicitly present in the provided text. "
                    "Do NOT infer, speculate, or hallucinate any details not found in the context. "
                    "Do NOT use headers, bullet points, or lengthy explanations. "
                    "Plain prose only. Be brief."
                )
            },
            {
                "role": "user",
                "content": (
                    "DOCUMENTS TO SUMMARIZE:\n\n"
                    + combined_text
                    + "\n\n[END OF DOCUMENTS]\n\n"
                    "Summarize ONLY the content above. "
                    "Do not include any facts, figures, or predictions not explicitly stated in the documents above."
                )
            }
        ]
    )

    return response["message"]["content"]