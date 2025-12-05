import json
import os
import chromadb 
from rag_engine import add_case_to_db 
from dotenv import load_dotenv

# Load API keys
load_dotenv()

# [설정] rag_engine.py와 정확히 일치시켰습니다.
DB_PATH = "./chroma_db"               # 경로 일치
COLLECTION_NAME = "korean_legal_cases" # 이름 일치 (중요!)

def get_existing_ids():
    """
    ChromaDB에 이미 저장된 데이터의 ID 목록을 가져옵니다.
    """
    try:
        client = chromadb.PersistentClient(path=DB_PATH)
        collection = client.get_collection(name=COLLECTION_NAME)
        
        # 저장된 모든 ID 가져오기 (메타데이터 로드 없이 ID만 가져와서 빠름)
        existing_data = collection.get(include=[]) 
        return set(existing_data['ids']) 
        
    except Exception:
        # DB가 없거나 컬렉션이 아직 안 만들어졌으면 빈 집합 반환
        return set()

def ingest_local_data():
    file_path = "real_cases.json"
    
    print(f"📂 Opening {file_path}...")
    
    try:
        # 1. JSON 파일 로드
        with open(file_path, 'r', encoding='utf-8') as f:
            cases = json.load(f)
        
        total_cases = len(cases)
        print(f"📊 Found {total_cases} cases in JSON.")

        # 2. 이미 저장된 ID 목록 조회
        print("🔍 Checking existing data in ChromaDB...")
        existing_ids = get_existing_ids()
        print(f"   ↳ {len(existing_ids)} cases already exist in DB.")

        # 3. 데이터 적재 시작
        new_count = 0
        skip_count = 0
        
        print("\n🚀 Starting ingestion...")
        
        for case in cases:
            case_id = str(case['id']) # ID 문자열 변환
            
            # [CHECK] 이미 DB에 있는 ID라면 건너뜀 (스킵 로직)
            if case_id in existing_ids:
                skip_count += 1
                continue

            # 새로운 데이터만 처리
            print(f"   📥 Processing: {case_id} - {case['title']}")
            
            # 제목과 본문을 합쳐서 검색 품질 향상
            full_text = f"[{case['title']}] {case['text']}"
            
            add_case_to_db(
                case_id=case_id,
                text=full_text,
                metadata=case['meta']
            )
            new_count += 1
            
        print("\n" + "="*40)
        print(f"✅ Ingestion Complete!")
        print(f"   - Total found in JSON: {total_cases}")
        print(f"   - Newly added: {new_count}")
        print(f"   - Skipped (Duplicates): {skip_count}")
        print("="*40)
        
    except FileNotFoundError:
        print("❌ Error: real_cases.json not found.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    ingest_local_data()