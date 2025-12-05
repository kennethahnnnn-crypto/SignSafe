import json
import os
import time
import google.generativeai as genai
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

# --- [설정] ---
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY")
PINECONE_KEY = os.environ.get("PINECONE_API_KEY") # .env에 추가했으면 자동으로 가져옴

if not GOOGLE_KEY or not PINECONE_KEY:
    print("❌ API 키가 없습니다. .env 파일을 확인해주세요.")
    exit()

genai.configure(api_key=GOOGLE_KEY)
pc = Pinecone(api_key=PINECONE_KEY)
index_name = "legal-cases"
index = pc.Index(index_name)

# --- [재시도 로직이 포함된 임베딩 함수] ---
def get_embedding_with_retry(text, retries=5, delay=2):
    """구글 서버가 500 에러를 뱉으면 잠시 쉬었다가 재시도합니다."""
    for attempt in range(retries):
        try:
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text,
                task_type="retrieval_document"
            )
            return result['embedding']
        except Exception as e:
            if "500" in str(e) or "Internal" in str(e):
                print(f"      ⚠️ 구글 서버 불안정... {delay}초 후 재시도 ({attempt+1}/{retries})")
                time.sleep(delay)
                delay *= 2 # 대기 시간을 2배로 늘림 (2초 -> 4초 -> 8초...)
            else:
                raise e # 500 에러가 아니면 그냥 에러 발생시킴
    raise Exception("재시도 횟수 초과: 구글 서버 응답 없음")

def ingest_data():
    # 경로 수정: data 폴더 안에 있다면 "data/real_cases.json"
    json_path = "data/real_cases.json" 
    
    # 파일이 루트에 있는지 data 폴더에 있는지 확인
    if not os.path.exists(json_path):
        json_path = "real_cases.json"
        
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            cases = json.load(f)
    except FileNotFoundError:
        print("❌ 'real_cases.json' 파일을 찾을 수 없습니다!")
        return

    print(f"🚀 총 {len(cases)}개의 판례 업로드 재개...")
    
    batch_size = 30
    vectors = []
    
    for i, case in enumerate(cases):
        try:
            full_text = f"[{case['title']}] {case['text']}"
            
            # [수정] 재시도 함수 사용
            embedding_vector = get_embedding_with_retry(full_text)
            
            # [수정] 메타데이터 크기 제한 (40KB 에러 방지)
            safe_text = full_text[:9000] 
            
            vector_data = {
                "id": str(case['id']),
                "values": embedding_vector,
                "metadata": {
                    "text": safe_text,
                    "source": case['meta'].get('source', 'Unknown')
                }
            }
            vectors.append(vector_data)
            
            if len(vectors) >= batch_size:
                index.upsert(vectors)
                print(f"   ✅ {i+1} / {len(cases)} 완료")
                vectors = []
                time.sleep(0.5) 
                
        except Exception as e:
            # 치명적인 에러만 출력하고 다음으로 넘어감
            print(f"   ❌ 최종 실패 (ID: {case.get('id')}): {e}")

    if vectors:
        index.upsert(vectors)
        print(f"   ✅ 최종 완료! 모든 데이터 업로드 끝.")

if __name__ == "__main__":
    ingest_data()