import os
import google.generativeai as genai
from pinecone import Pinecone
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 1. API 키 설정
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")

# 키가 없는 경우 에러 방지 (로그만 출력)
if not GOOGLE_API_KEY or not PINECONE_API_KEY:
    print("⚠️ 경고: API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")

# 2. 초기화
genai.configure(api_key=GOOGLE_API_KEY)

# Pinecone 연결 (새로운 패키지 문법 적용)
try:
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index_name = "legal-cases"  # 아까 만든 인덱스 이름과 일치해야 함
    index = pc.Index(index_name)
except Exception as e:
    print(f"❌ Pinecone 연결 실패: {e}")

def search_precedents(query_text, n_results=3):
    """
    사용자의 질문(query_text)을 받아 Pinecone에서 유사한 판례를 검색합니다.
    """
    try:
        # 1. 질문을 벡터(숫자)로 변환
        # (문서를 넣을 때와 똑같은 모델을 써야 찾을 수 있습니다)
        query_vector = genai.embed_content(
            model="models/text-embedding-004",
            content=query_text,
            task_type="retrieval_query"
        )['embedding']
        
        # 2. Pinecone 검색
        search_response = index.query(
            vector=query_vector,
            top_k=n_results,
            include_metadata=True # 텍스트 내용도 같이 가져옴
        )
        
        # 3. 결과 정리 (앱에서 쓰기 편한 형태로 변환)
        results = []
        for match in search_response['matches']:
            # metadata가 없을 경우를 대비해 get 사용
            meta = match.get('metadata', {})
            results.append({
                "text": meta.get('text', '내용 없음'),
                "meta": {"source": meta.get('source', 'Unknown')}
            })
            
        return results
        
    except Exception as e:
        print(f"❌ 검색 에러: {str(e)}")
        # 에러 발생 시 빈 리스트 반환 (서버 멈춤 방지)
        return []