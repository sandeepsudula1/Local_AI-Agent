import os
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from agents.knowledge.retrieval_agent import handle_retrieval

ROOT = os.path.dirname(os.path.dirname(__file__))
DOCS_PATH = os.path.join(ROOT, 'data', 'documents')
VECTOR_STORE_PATH = os.path.join(ROOT, 'data', 'vector_store')
EMBEDDING_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
MODEL_NAME = 'gemma:7b'
THRESHOLD = 1.5

# load documents (same as smart_agent)
documents = []
if os.path.exists(DOCS_PATH):
    for file in os.listdir(DOCS_PATH):
        full_path = os.path.join(DOCS_PATH, file)
        if file.lower().endswith('.pdf'):
            loader = PyPDFLoader(full_path)
            for doc in loader.load():
                doc.metadata['source'] = file
                documents.append(doc)
        elif file.lower().endswith('.csv'):
            import pandas as pd
            df = pd.read_csv(full_path)
            for _, row in df.iterrows():
                row_text = ', '.join([f"{col}: {row[col]}" for col in df.columns])
                documents.append(Document(page_content=row_text, metadata={'source': file}))

print(f"Document chunks found: {len(documents)}")

# setup embeddings and vector store
emb = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL, model_kwargs={"device": "cpu"})
vector_db = None
if os.path.exists(VECTOR_STORE_PATH) and any(os.scandir(VECTOR_STORE_PATH)):
    print('Loading persisted vector store...')
    try:
        vector_db = Chroma(persist_directory=VECTOR_STORE_PATH, embedding_function=emb)
        print('Loaded persisted vector DB.')
    except Exception as e:
        print('Failed to load persisted vector DB:', e)

if vector_db is None:
    print('Building vector store from documents...')
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=80)
    chunks = splitter.split_documents(documents)
    vector_db = Chroma.from_documents(chunks, emb, persist_directory=VECTOR_STORE_PATH)
    try:
        vector_db.persist()
    except Exception:
        pass
    print('Vector store built and persisted.')

# test queries (broad list)
queries = [
    'Who supervised Sandeep during the internship?',
    'What tools were used for load testing?',
    'What is the duration of the internship?',
    'List projects Sandeep worked on before joining UTV',
    'What features were implemented in the Local AI Agent project?',
    'What is in company_data.csv',
    'What does the Year column in company_data.csv represent?',
    'Summarize sandeep_internship_work.pdf in one sentence',
    'When did the product launch appear in company_data.csv?',
    'What is the revenue growth mentioned in the documents?',
    'How many employees are listed in company_data.csv?',
    'What is the Role of Sandeep?'
]

print('\nRunning retrieval tests:\n')
failures = []
for q in queries:
    ans, src = handle_retrieval(q, vector_db, THRESHOLD, MODEL_NAME)
    print('Q:', q)
    print('A:', ans)
    print('Source:', src)
    print('---')
    if not ans or ans is None:
        failures.append((q, ans, src))

print('\nTests complete. Failures count:', len(failures))
if failures:
    print('Failed queries:')
    for f in failures:
        print('-', f[0])

