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
    
    # --- [추가 기능] 챗봇용 대화 함수 ---
# rag_engine.py 의 ask_lawyer 함수 전체 수정

def ask_lawyer(query_text, context_text=""):
    """
    사용자의 질문(query_text)과 현재 계약서 내용(context_text)을 바탕으로 답변을 생성합니다.
    """
    try:
        # 1. 질문과 관련된 판례 검색 (RAG)
        relevant_cases = search_precedents(query_text, n_results=3)
        
        # 2. 참고 자료 정리
        case_summary = ""
        if relevant_cases:
            case_summary = "\n<관련_판례_데이터베이스>\n"
            for idx, case in enumerate(relevant_cases, 1):
                case_summary += f"{idx}. {case['text']} (출처: {case['meta']['source']})\n"
            case_summary += "</관련_판례_데이터베이스>\n"
        else:
            case_summary = "\n<관련_판례_없음>\n일반적인 대한민국 법률 원칙 적용\n"

        # 3. 프롬프트 구성 (말투 교정 적용)
        prompt = f"""
        당신은 사용자의 법률 문제를 돕는 AI 파트너 'ClauseMate'입니다.
        아래 정보를 바탕으로 사용자의 질문에 답변하세요.

        ---
        [현재 검토 중인 계약서 조항 또는 맥락]
        {context_text}
        ---
        {case_summary}
        ---

        사용자 질문: "{query_text}"

        [답변 스타일 가이드라인 - 엄격 준수]
        1. **직접적인 답변:** "안녕하세요", "변호사로서 답변드립니다" 같은 **상투적인 인사말이나 자기소개를 절대 하지 마세요.** 바로 본론부터 답변하세요.
        2. **근거 기반:** <관련_판례_데이터베이스>의 내용을 논리적 근거로 사용하되, 법률 용어는 이해하기 쉽게 풀어서 설명하세요.
        3. **자연스러운 인용:** 판례를 언급할 땐 "관련 판례(2023도...)에 따르면"과 같이 자연스럽게 문장에 녹여내세요.
        4. **구조화:** 답변이 길어질 경우 가독성을 위해 불릿 포인트나 번호를 사용하세요.
        5. **톤앤매너:** 정중하지만 딱딱하지 않게, 전문적이지만 친절하게 한국어로 답변하세요.
        """

        # 4. Gemini에게 질문
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        
        return response.text

    except Exception as e:
        print(f"❌ 챗봇 에러: {e}")
        return "죄송합니다. 답변을 생성하는 도중 오류가 발생했습니다."