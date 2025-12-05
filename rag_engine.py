# --- [Render 배포를 위한 SQLite 패치] ---
# 로컬 개발환경이나 배포 환경에 따라 pysqlite3가 필요할 수 있음
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass
# ----------------------------------------

import os
from dotenv import load_dotenv
# ... (이하 기존 코드) ...
load_dotenv()                   # <--- Add this (It loads the .env file immediately)

import chromadb
import google.generativeai as genai
from chromadb.utils import embedding_functions

# 1. Configure Gemini
# Ensure you have your API Key set in your environment or replace 'os.environ...' with the actual key for testing
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

# 2. Initialize ChromaDB
# This creates a folder named 'chroma_db' in your project folder
chroma_client = chromadb.PersistentClient(path="./chroma_db")

# 3. Custom Embedding Function (Connects Chroma to Gemini)
class GeminiEmbeddingFunction(chromadb.EmbeddingFunction):
    def __call__(self, input: list[str]) -> list[list[float]]:
        model = "models/text-embedding-004"
        return [
            genai.embed_content(
                model=model,
                content=text,
                task_type="retrieval_document",
                title="Korean Case Law"
            )["embedding"]
            for text in input
        ]

# 4. Create or Connect to the Collection
collection = chroma_client.get_or_create_collection(
    name="korean_legal_cases",
    embedding_function=GeminiEmbeddingFunction()
)

def add_case_to_db(case_id, text, metadata):
    """Call this to save a law/case."""
    collection.add(
        ids=[case_id],
        documents=[text],
        metadatas=[metadata]
    )

def search_precedents(query_text, n_results=3):
    """Finds the top 3 relevant laws."""
    results = collection.query(
        query_texts=[query_text],
        n_results=n_results
    )
    
    # Organize the messy result into a clean list
    retrieved_data = []
    if results['documents']:
        for i in range(len(results['documents'][0])):
            retrieved_data.append({
                "text": results['documents'][0][i],
                "meta": results['metadatas'][0][i]
            })
            
    return retrieved_data