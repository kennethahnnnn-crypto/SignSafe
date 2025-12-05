import os
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai
from PIL import Image
from pypdf import PdfReader
from docx import Document
from dotenv import load_dotenv

# [RAG 통합 1] 검색 엔진 가져오기
# (같은 폴더에 rag_engine.py와 chroma_db 폴더가 있어야 합니다)
from rag_engine import search_precedents 

load_dotenv() # .env 파일 로드

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False 
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 
app.config['SECRET_KEY'] = 'ClauseMateSecretKey'

# --- DATABASE SETUP ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clausemate.db' 
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- AI SETUP ---
API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
if API_KEY:
    genai.configure(api_key=API_KEY)
    # [참고] 모델명은 최신 안정화 버전인 2.5-flash를 추천합니다.
    model = genai.GenerativeModel('gemini-2.5-flash') 

# --- MODELS (기존과 동일) ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))
    name = db.Column(db.String(100))

class Contract(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    title = db.Column(db.String(200))
    score = db.Column(db.Integer)
    summary = db.Column(db.String(500))
    full_analysis = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Analytics(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    variant = db.Column(db.String(10)) 
    event_type = db.Column(db.String(20))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Poll(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    label = db.Column(db.String(100))
    count = db.Column(db.Integer, default=0)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- ROUTES (기본 라우트 동일) ---

@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/about')
def about(): return render_template('about.html')

@app.route('/privacy')
def privacy(): return render_template('privacy.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('로그인 실패: 이메일이나 비밀번호를 확인하세요.')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        name = request.form.get('name')
        password = request.form.get('password')
        if User.query.filter_by(email=email).first():
            flash('이미 존재하는 이메일입니다.')
            return redirect(url_for('register'))
        new_user = User(email=email, name=name, password=generate_password_hash(password, method='scrypt'))
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    contracts = Contract.query.filter_by(user_id=current_user.id).order_by(Contract.created_at.desc()).all()
    return render_template('dashboard.html', name=current_user.name, contracts=contracts)

# --- [RAG 통합 2] Review 기능 대폭 업그레이드 ---
@app.route('/review', methods=['POST'])
@login_required
def review():
    prompt_content = []
    extracted_text_for_rag = ""  # RAG 검색용 텍스트 저장소
    
    # 1. 텍스트 입력 처리
    if 'text' in request.form and request.form['text'].strip():
        text_input = request.form['text']
        prompt_content.append(f"CONTRACT TEXT:\n{text_input}\n")
        extracted_text_for_rag += text_input + "\n"

    # 2. 파일 입력 처리 (PDF/DOCX/Image)
    if 'files' in request.files:
        files = request.files.getlist('files')
        for file in files:
            if file.filename == '': continue
            filename = file.filename.lower()
            try:
                if filename.endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic')):
                    img = Image.open(file)
                    prompt_content.append(img)
                    # 이미지는 텍스트 추출이 어려우므로 RAG 검색에서는 제외 (OCR 필요 시 별도 추가)
                elif filename.endswith('.pdf'):
                    reader = PdfReader(file)
                    pdf_text = ""
                    for page in reader.pages: 
                        pdf_text += page.extract_text() + "\n"
                    prompt_content.append(f"\n[PDF CONTENT]:\n{pdf_text}")
                    extracted_text_for_rag += pdf_text
                elif filename.endswith('.docx'):
                    doc = Document(file)
                    docx_text = "\n".join([para.text for para in doc.paragraphs])
                    prompt_content.append(f"\n[DOCX CONTENT]:\n{docx_text}")
                    extracted_text_for_rag += docx_text
            except Exception as e: 
                print(f"File processing error: {e}")

    if not prompt_content: return jsonify({"error": "분석할 내용이 없습니다."}), 400

    # 3. [핵심] RAG: 판례 데이터베이스 검색
    print("🔍 Searching Precedents using RAG Engine...")
    
    # 텍스트가 너무 길면 검색 정확도가 떨어지므로 앞부분 2000자만 사용해 검색 (키워드 추출 효과)
    query_text = extracted_text_for_rag[:2000] if extracted_text_for_rag else "계약서 일반 검토"
    relevant_cases = search_precedents(query_text, n_results=3)
    
    # 검색된 판례를 프롬프트에 넣을 문자열로 변환
    precedents_context = ""
    if relevant_cases:
        precedents_context = "\n[RELEVANT LEGAL PRECEDENTS FROM DATABASE]\n"
        for idx, case in enumerate(relevant_cases, 1):
            precedents_context += f"{idx}. {case['text']} (Source: {case['meta']['source']})\n"
        print(f"   ✅ Found {len(relevant_cases)} precedents.")
    else:
        print("   ❌ No precedents found.")
        precedents_context = "\n[NO SPECIFIC PRECEDENTS FOUND - APPLY GENERAL KOREAN LAW]\n"

    # 4. 프롬프트 작성 (판례 근거 추가)
    base_prompt = f"""
    You are a highly experienced Korean Contract Lawyer (변호사). 
    Review the provided contract materials (Images, PDFs, Text) as ONE complete document.
    
    {precedents_context}
    
    CRITICAL INSTRUCTIONS:
    1. **USE PRECEDENTS:** If any clause contradicts the [RELEVANT LEGAL PRECEDENTS] provided above, mark it as 'CRITICAL RISK' and cite the source.
    2. **EXHAUSTIVE SEARCH:** Find EVERY SINGLE clause that poses a risk.
    3. **LOCATION TRACKING:** Identify WHERE the clause is (e.g., "제5조 2항").
    4. **LANGUAGE:** All output MUST be in natural KOREAN (한국어).
    5. **FORMAT:** Return ONLY ONE valid JSON object.
    
    OUTPUT JSON (No Markdown):
    {{
        "title": "Short title",
        "score": 75,
        "score_comment": "One sentence summary.",
        "analysis": [
            {{
                "location": "제X조", 
                "type": "위험", 
                "original": "text",
                "reason": "Why is this dangerous? (Cite precedent if applicable)",
                "fix": "Rewrite suggestion"
            }}
        ]
    }}
    """
    prompt_content.append(base_prompt)
    
    try:
        response = model.generate_content(prompt_content)
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        
        start = clean_json.find('{')
        end = clean_json.rfind('}') + 1
        final_json_str = clean_json[start:end]
        
        data = json.loads(final_json_str)
        
        new_contract = Contract(
            user_id=current_user.id,
            title=data.get('title', '무제 계약서'),
            score=data.get('score', 0),
            summary=data.get('score_comment', ''),
            full_analysis=final_json_str
        )
        db.session.add(new_contract)
        db.session.commit()
        
        return final_json_str
        
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": f"분석 오류: {str(e)}"}), 500

# --- [RAG 통합 3] 챗봇 API 라우트 추가 ---
@app.route('/chat_api', methods=['POST'])
@login_required
def chat_api():
    """프론트엔드에서 JS로 호출할 챗봇 엔드포인트"""
    try:
        data = request.json
        user_question = data.get('message')
        if not user_question: return jsonify({"response": "질문을 입력해주세요."})

        # 1. RAG 검색
        relevant_cases = search_precedents(user_question)
        
        # 2. Context 구성
        context = "\n".join([f"- {c['text']} (출처: {c['meta']['source']})" for c in relevant_cases])
        
        # 3. 답변 생성
        chat_prompt = f"""
        당신은 한국 법률 전문가 AI입니다. 아래 판례/법률 정보를 바탕으로 사용자의 질문에 답하세요.
        
        [참고 정보]
        {context}
        
        [질문]
        {user_question}
        
        답변 시 '참고 정보'에 있는 내용을 근거로 들고, 출처를 명시하세요.
        """
        response = model.generate_content(chat_prompt)
        return jsonify({"response": response.text})
        
    except Exception as e:
        return jsonify({"response": f"오류가 발생했습니다: {str(e)}"})

# --- ADMIN PANEL & ETC (기존 유지) ---
@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.email != 'admin@clausemate.app':
        return "<h3>🚫 Access Denied: Admins Only</h3>", 403
    users = User.query.all()
    contracts = Contract.query.all()
    html = f"""<body style='padding:40px;'><h1>Admin</h1><p>Users: {len(users)} | Contracts: {len(contracts)}</p></body>"""
    return html

@app.route('/log_ab', methods=['POST'])
def log_ab():
    data = request.json
    db.session.add(Analytics(variant=data.get('variant'), event_type=data.get('event')))
    db.session.commit()
    return jsonify({"status": "logged"})

@app.route('/vote', methods=['POST'])
def vote():
    option_id = request.json.get('option')
    item = db.session.get(Poll, option_id)
    if item:
        item.count += 1
        db.session.commit()
    total = db.session.query(db.func.sum(Poll.count)).scalar() or 1
    results = [{"id": p.id, "percent": round((p.count / total) * 100), "count": p.count} for p in Poll.query.all()]
    return jsonify(results)

@app.route('/stats')
@login_required
def stats():
    if current_user.email != 'admin@clausemate.app': return "Access Denied", 403
    return "<h1>Stats Placeholder</h1>"

# --- DB INIT ---
with app.app_context():
    db.create_all()
    if not User.query.filter_by(email='admin@clausemate.app').first():
        db.session.add(User(email='admin@clausemate.app', name='Admin', password=generate_password_hash('1234', method='scrypt')))
    poll_data = [('toxic', '☠️ 독소조항'), ('terms', '🤯 어려운 용어'), ('money', '💸 돈 떼일까 봐')]
    for pid, label in poll_data:
        if not db.session.get(Poll, pid): db.session.add(Poll(id=pid, label=label, count=10))
    db.session.commit()

if __name__ == '__main__':
    app.run(debug=True, port=5005)