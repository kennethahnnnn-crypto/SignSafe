import os
import sys
import time
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from pinecone import Pinecone
from dotenv import load_dotenv

# --- [설정] ---
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
load_dotenv(dotenv_path=os.path.join(root_dir, ".env"))

LAW_USER_ID = os.environ.get("LAW_USER_ID", "kennethahnnnn")
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY")
PINECONE_KEY = os.environ.get("PINECONE_API_KEY")

genai.configure(api_key=GOOGLE_KEY)
pc = Pinecone(api_key=PINECONE_KEY)
index = pc.Index("legal-cases")

def get_embedding(text):
    try:
        return genai.embed_content(
            model="models/text-embedding-004", content=text, task_type="retrieval_document"
        )['embedding']
    except:
        return None

def fetch_case_detail(case_id):
    """상세 내용 가져오기 (요약 및 전문)"""
    url = "https://www.law.go.kr/DRF/lawService.do"
    params = {"OC": LAW_USER_ID, "target": "prec", "ID": case_id, "type": "XML"}
    try:
        res = requests.get(url, params=params)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            # 판결요지 우선, 없으면 전문
            summary = root.find("판결요지")
            if summary is not None and summary.text: return summary.text.replace('<br/>', '\n')
            content = root.find("판례내용")
            if content is not None and content.text: return content.text.replace('<br/>', '\n')
    except: pass
    return None

def seed_history_data():
    # 1. 과거 데이터를 긁어올 주제 설정 (사기, 지적재산권, 계약 해지, 손해배상, 비밀유지, 근로기준법, 투자금반환, 주주간계약, 신주인수, 주식매수선택권, 사해행위, 용역비, 지체상금, 부당이득, 위약벌, 전직금지, 영업비밀, 업무상배임, 특허권침해, 채무불이행, 소프트웨어개발, 투자금, 저작권법, 동업계약, 기타 등등)
    target_keyword = "근로기준법" 
    
    # 2. 얼마나 과거까지 갈 것인가? (페이지 당 20건 x 50페이지 = 1,000건)
    # 20년치를 다 긁으려면 페이지를 100~200까지 늘려야 할 수도 있습니다.
    start_page = 1
    end_page = 100 

    print(f"🕰️ '{target_keyword}' 주제로 과거 데이터 여행을 시작합니다. (Page {start_page} ~ {end_page})")

    for page in range(start_page, end_page + 1):
        print(f"\n📄 [Page {page}] 검색 중...")
        
        search_url = "https://www.law.go.kr/DRF/lawSearch.do"
        params = {
            "OC": LAW_USER_ID,
            "target": "prec",
            "type": "XML",
            "display": 20,     # 한 페이지에 20개씩
            "page": page,      # <--- 핵심: 페이지를 넘깁니다!
            "sort": "date",    # 날짜순 정렬 (페이지가 뒤로 갈수록 옛날 것)
            "query": target_keyword
        }

        try:
            response = requests.get(search_url, params=params)
            root = ET.fromstring(response.content)
            items = root.findall(".//prec")
            
            if not items:
                print("   📭 더 이상 판례가 없습니다. 수집 종료.")
                break

            vectors = []
            for item in items:
                case_id = item.find("판례일련번호").text
                title = item.find("사건명").text
                date = item.find("선고일자").text # 예: 2015.05.21
                case_num = item.find("사건번호").text
                
                print(f"   📥 수집: {title} ({date})")
                
                detail_text = fetch_case_detail(case_id)
                if not detail_text: continue
                
                full_text = f"[{title}] {case_num}\n{detail_text}"
                embedding = get_embedding(full_text)
                
                if embedding:
                    vectors.append({
                        "id": str(case_id),
                        "values": embedding,
                        "metadata": {
                            "text": full_text[:9000],
                            "source": f"대법원 판례 {case_num} ({target_keyword})",
                            "date": date # 나중에 연도별 필터링 가능
                        }
                    })
                time.sleep(0.2) # 너무 빠르면 차단되니 살짝 딜레이

            if vectors:
                index.upsert(vectors)
                print(f"   ✅ Page {page} 완료! ({len(vectors)}개 저장)")

        except Exception as e:
            print(f"   ❌ 에러 발생: {e}")
            time.sleep(5) # 에러 나면 5초 쉬었다가 다음 페이지

if __name__ == "__main__":
    seed_history_data()