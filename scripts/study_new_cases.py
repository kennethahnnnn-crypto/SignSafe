import os
import sys
import time
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from pinecone import Pinecone
from dotenv import load_dotenv

# --- [1. 환경 설정] ---
# 스크립트 위치 기준으로 .env 파일 찾기
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
env_path = os.path.join(root_dir, ".env")
load_dotenv(dotenv_path=env_path)

# API 키 및 설정 가져오기
LAW_USER_ID = os.environ.get("LAW_USER_ID")
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY")
PINECONE_KEY = os.environ.get("PINECONE_API_KEY")

# 설정 확인
if not LAW_USER_ID:
    LAW_USER_ID = "kennethahnnnn" 
    print("⚠️ .env에서 ID를 못 찾아 기본 ID를 사용합니다.")

if not GOOGLE_KEY or not PINECONE_KEY:
    print("❌ 구글 또는 파인콘 API 키가 없습니다.")
    exit()

# AI & DB 초기화
genai.configure(api_key=GOOGLE_KEY)
pc = Pinecone(api_key=PINECONE_KEY)
index = pc.Index("legal-cases")

# --- [2. 핵심 함수들] ---

def get_embedding(text):
    """구글 임베딩 생성 (에러 시 None 반환)"""
    try:
        return genai.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_document"
        )['embedding']
    except Exception as e:
        print(f"      ⚠️ 임베딩 실패: {e}")
        return None

def fetch_case_detail(case_id):
    """판례 상세 내용(판결요지/전문) 가져오기"""
    url = "https://www.law.go.kr/DRF/lawService.do"
    params = {
        "OC": LAW_USER_ID,
        "target": "prec",
        "ID": case_id,
        "type": "XML"
    }
    try:
        res = requests.get(url, params=params)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            
            # 1순위: 판결요지 (핵심 내용)
            summary = root.find("판결요지")
            if summary is not None and summary.text:
                return summary.text.replace('<br/>', '\n')
            
            # 2순위: 판례내용 (전문)
            content = root.find("판례내용")
            if content is not None and content.text:
                return content.text.replace('<br/>', '\n')
                
    except Exception:
        pass
    return None

def study_new_cases():
    """
    [업그레이드] 
    여러 법률 키워드를 순회하며 최신 판례를 검색하고 Pinecone에 학습시킵니다.
    """
    
    # ---------------------------------------------------------
    # [설정] 공부시킬 주제들을 여기에 추가하세요!
    # ---------------------------------------------------------
    keywords = [
        # [기존 핵심]
        "사기", "지적재산권", "저작권", "손해배상", "비밀유지", "근로기준법",
        
        # [신규 추가 - 계약 분쟁의 핵심]
        "채무불이행",  # 계약 위반 시 책임
        "계약해제",    # 계약 파기 조건
        "부당이득",    # 돈 돌려받기
        
        # [신규 추가 - 갑질/공정거래 방지]
        "하도급",      # 프리랜서/하청 보호
        "불공정거래",  # 독소조항 무효화 근거
        "약관규제",    # 깨알 같은 글씨로 된 불공정 약관 잡기
        
        # [신규 추가 - IT/스타트업]
        "경업금지",    # 이직/창업 금지 조항의 효력
        "용역계약",    # 개발/디자인 외주 분쟁
        "개인정보"     # 데이터 유출 책임
    ]
    
    print(f"📚 학습 시작! 총 {len(keywords)}가지 주제를 순찰합니다: {keywords}")

    # 주제별 반복문 시작
    for keyword in keywords:
        print(f"\n🔍 주제: '{keyword}' 관련 최신 판례 검색 중...")
        
        search_url = "https://www.law.go.kr/DRF/lawSearch.do"
        params = {
            "OC": LAW_USER_ID,
            "target": "prec",
            "type": "XML",
            "display": 5,      # 각 주제별로 최신 5개씩만 (너무 많이 가져오면 과부하)
            "sort": "date",    # 최신순
            "query": keyword   # [변경] 리스트에 있는 키워드가 동적으로 들어갑니다.
        }
        
        try:
            response = requests.get(search_url, params=params)
            
            if "<html" in response.text.lower():
                print(f"   ❌ '{keyword}' 검색 실패: API 접속 거부됨.")
                continue # 다음 키워드로 넘어감

            root = ET.fromstring(response.content)
            items = root.findall(".//prec")
            
            if not items:
                print(f"   📭 '{keyword}' 관련 새로운 판례 없음.")
                continue

            print(f"   🚀 {len(items)}개의 판례 발견. 분석 시작...")
            
            vectors = []
            for item in items:
                case_id = item.find("판례일련번호").text
                title = item.find("사건명").text
                date = item.find("선고일자").text
                case_num = item.find("사건번호").text
                
                # 상세 내용 가져오기
                detail_text = fetch_case_detail(case_id)
                if not detail_text:
                    continue
                    
                # 텍스트 합치기
                full_text = f"[{title}] {case_num}\n{detail_text}"
                
                # 임베딩 & 데이터 포장
                embedding = get_embedding(full_text)
                if embedding:
                    vectors.append({
                        "id": str(case_id),
                        "values": embedding,
                        "metadata": {
                            "text": full_text[:9000], # 길이 제한
                            "source": f"대법원 판례 {case_num} ({keyword})", # 키워드 태그 추가
                            "date": date
                        }
                    })
                
                time.sleep(0.5) # 개별 판례 처리 간 딜레이

            # Pinecone에 업로드
            if vectors:
                index.upsert(vectors)
                print(f"   ✅ '{keyword}' 주제 학습 완료! ({len(vectors)}개 저장됨)")
            
            # 다음 주제로 넘어가기 전 예의상 딜레이
            time.sleep(2) 
            
        except Exception as e:
            print(f"   ❌ '{keyword}' 처리 중 오류 발생: {e}")

    print("\n🎉 모든 주제에 대한 학습이 종료되었습니다.")

if __name__ == "__main__":
    study_new_cases()