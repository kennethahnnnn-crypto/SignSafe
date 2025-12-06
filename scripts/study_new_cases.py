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
LAW_USER_ID = os.environ.get("LAW_USER_ID") # .env에 kennethahnnnn 있어야 함
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY")
PINECONE_KEY = os.environ.get("PINECONE_API_KEY")

# 설정 확인
if not LAW_USER_ID:
    # 혹시 .env에 없으면 하드코딩된 값이라도 시도
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
    """최신 사기 판례를 검색하고 Pinecone에 학습시킵니다."""
    print(f"🔍 '{LAW_USER_ID}' 계정으로 '사기' 관련 최신 판례를 검색합니다...")
    
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {
        "OC": LAW_USER_ID,
        "target": "prec",
        "type": "XML",
        "display": 10,     # 최근 10개만 공부 (매일 돌린다고 가정)
        "sort": "date",    # 최신순
        "query": "사기"    # [핵심] 사기 관련 판례만 집중 학습
    }
    
    try:
        response = requests.get(search_url, params=params)
        # HTML 에러 페이지가 오면 차단된 것임
        if "<html" in response.text.lower():
            print("❌ API 접속 거부됨 (IP 차단 또는 ID 오류).")
            return

        root = ET.fromstring(response.content)
        items = root.findall(".//prec")
        
        if not items:
            print("📭 새로운 판례가 없습니다.")
            return

        print(f"🚀 {len(items)}개의 최신 판례를 발견! 학습 시작...")
        
        vectors = []
        for item in items:
            case_id = item.find("판례일련번호").text
            title = item.find("사건명").text
            date = item.find("선고일자").text
            case_num = item.find("사건번호").text
            
            print(f"   📖 읽는 중: {title} ({date})")
            
            # 상세 내용 가져오기
            detail_text = fetch_case_detail(case_id)
            if not detail_text:
                print("      ↳ 내용 없음, 스킵.")
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
                        "source": f"대법원 판례 {case_num}",
                        "date": date
                    }
                })
            
            time.sleep(1) # 서버 부하 방지

        # Pinecone에 업로드
        if vectors:
            index.upsert(vectors)
            print(f"✅ 학습 완료! {len(vectors)}개의 지식이 추가되었습니다.")
            
    except Exception as e:
        print(f"❌ 오류 발생: {e}")

if __name__ == "__main__":
    study_new_cases()