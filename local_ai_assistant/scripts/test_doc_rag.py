import os
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

DOCS_PATH = os.path.join("data", "documents")

# load documents similarly to smart_agent.py
documents = []
if os.path.exists(DOCS_PATH):
    for file in os.listdir(DOCS_PATH):
        full_path = os.path.join(DOCS_PATH, file)
        if file.endswith('.pdf'):
            loader = PyPDFLoader(full_path)
            for doc in loader.load():
                doc.metadata['source'] = file
                documents.append(doc)
        elif file.endswith('.csv'):
            import pandas as pd
            df = pd.read_csv(full_path)
            for _, row in df.iterrows():
                row_text = ", ".join([f"{col}: {row[col]}" for col in df.columns])
                documents.append(Document(page_content=row_text, metadata={"source": file}))
        else:
            try:
                from PIL import Image
                import pytesseract
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    extracted = pytesseract.image_to_string(Image.open(full_path))
                    documents.append(Document(page_content=extracted, metadata={"source": file}))
            except Exception:
                pass

print(f"Loaded {len(documents)} document chunks.")
if not documents:
    print("No documents found in data/documents.")
    raise SystemExit(0)

# find target file
target = 'sandeep_internship_work.pdf'
by_source = [d for d in documents if d.metadata.get('source') == target]
if not by_source:
    print(f"File {target} not specifically found; using first document chunk's source instead.")
    target = documents[0].metadata.get('source')
    by_source = [d for d in documents if d.metadata.get('source') == target]

print('Document source to test:', target)
print('Number of chunks for this source:', len(by_source))
print('\nExcerpt (first chunk):\n')
print(by_source[0].page_content[:1000])

# Try to use summary_agent if available
try:
    from agents.knowledge.summary_agent import handle_summary
    print('\nCalling handle_summary on the selected document chunks...')
    summary = handle_summary(by_source, 'gemma:7b')
    print('\nSummary result:\n')
    print(summary)
except Exception as e:
    print('\nCould not call handle_summary (ollama may be unavailable). Falling back to extractive summary.')
    text = '\n'.join([d.page_content for d in by_source])
    # simple extractive fallback: first 3 paragraphs
    paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    fallback = '\n\n'.join(paras[:3])
    print('\nFallback summary (first 3 paragraphs):\n')
    print(fallback)
