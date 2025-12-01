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
API_KEY = os.environ.get("GEMINI_API_KEY", "PASTE_YOUR_KEY_HERE_IF_LOCAL")
if API_KEY:
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')

# --- MODELS ---
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

# --- ROUTES ---

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

@app.route('/review', methods=['POST'])
@login_required
def review():
    prompt_content = []
    
    if 'text' in request.form and request.form['text'].strip():
        prompt_content.append(f"CONTRACT TEXT:\n{request.form['text']}\n")

    if 'files' in request.files:
        files = request.files.getlist('files')
        for file in files:
            if file.filename == '': continue
            filename = file.filename.lower()
            try:
                if filename.endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic')):
                    img = Image.open(file)
                    prompt_content.append(img)
                elif filename.endswith('.pdf'):
                    reader = PdfReader(file)
                    text = ""
                    for page in reader.pages: text += page.extract_text() + "\n"
                    prompt_content.append(f"\n[PDF CONTENT]:\n{text}")
                elif filename.endswith('.docx'):
                    doc = Document(file)
                    text = "\n".join([para.text for para in doc.paragraphs])
                    prompt_content.append(f"\n[DOCX CONTENT]:\n{text}")
            except: pass

    if not prompt_content: return jsonify({"error": "분석할 내용이 없습니다."}), 400

    base_prompt = """
    You are a highly experienced Korean Contract Lawyer (변호사). 
    Review the provided contract materials (Images, PDFs, Text) as ONE complete document.
    
    CRITICAL INSTRUCTIONS:
    1. **EXHAUSTIVE SEARCH:** Find EVERY SINGLE clause that poses a risk. Do not limit the count.
    2. **LOCATION TRACKING:** You MUST identify WHERE the clause is (e.g., "제5조 2항", "Page 1"). If unsure, write "위치 확인 필요".
    3. **LANGUAGE:** All output MUST be in natural KOREAN (한국어).
    4. **FORMAT:** Return ONLY ONE valid JSON object. Do not add extra text.
    
    OUTPUT JSON (No Markdown):
    {
        "title": "Short title (e.g. '강남 오피스텔 임대차 계약')",
        "score": 75,
        "score_comment": "One sentence summary of risk.",
        "analysis": [
            {
                "location": "제3조 (보증금)", 
                "type": "위험", 
                "original": "Original text",
                "reason": "Why is this dangerous? (Korean)",
                "fix": "Fair rewrite (Korean)"
            },
            {
                "location": "특약사항",
                "type": "주의",
                "original": "Original text",
                "reason": "Reason (Korean)",
                "fix": "Rewrite (Korean)"
            }
        ]
    }
    """
    prompt_content.append(base_prompt)
    
    try:
        response = model.generate_content(prompt_content)
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        
        # --- CRITICAL JSON CLEANING LOGIC ---
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

# --- ADMIN PANEL ROUTE (Full Version) ---
@app.route('/admin/users')
@login_required
def admin_users():
    # Only 'admin@clausemate.app' can see this
    if current_user.email != 'admin@clausemate.app':
        return "<h3>🚫 Access Denied: Admins Only</h3>", 403
    
    users = User.query.all()
    contracts = Contract.query.all()
    
    html = f"""
    <body style='font-family:sans-serif; padding:40px; max-width:800px; margin:0 auto;'>
        <h1>👥 Admin Panel</h1>
        <p><b>Total Users:</b> {len(users)} | <b>Total Contracts Analyzed:</b> {len(contracts)}</p>
        <hr>
        <h3>User List</h3>
        <table border='1' cellpadding='10' style='border-collapse:collapse; width:100%;'>
            <tr style='background:#f0f0f0;'><th>ID</th><th>Name</th><th>Email</th><th>Contracts Analyzed</th></tr>
    """
    for u in users:
        c_count = Contract.query.filter_by(user_id=u.id).count()
        html += f"<tr><td>{u.id}</td><td>{u.name}</td><td>{u.email}</td><td>{c_count}</td></tr>"
    
    html += "</table><br><a href='/dashboard'>← Back to Dashboard</a></body>"
    return html

# --- MARKETING ROUTES ---
@app.route('/log_ab', methods=['POST'])
def log_ab():
    data = request.json
    log = Analytics(variant=data.get('variant'), event_type=data.get('event'))
    db.session.add(log)
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
    
    views_a = Analytics.query.filter_by(variant='A', event_type='view').count()
    clicks_a = Analytics.query.filter_by(variant='A', event_type='click').count()
    views_b = Analytics.query.filter_by(variant='B', event_type='view').count()
    clicks_b = Analytics.query.filter_by(variant='B', event_type='click').count()
    
    conv_a = round((clicks_a / views_a * 100), 2) if views_a > 0 else 0
    conv_b = round((clicks_b / views_b * 100), 2) if views_b > 0 else 0
    
    return f"<h1>A (Fear): {conv_a}% | B (Speed): {conv_b}%</h1>"

# --- IMMORTAL ADMIN & SEED SCRIPT ---
with app.app_context():
    db.create_all()
    
    # 1. Create/Restore Admin
    target_email = 'admin@clausemate.app'
    if not User.query.filter_by(email=target_email).first():
        admin_user = User(
            email=target_email,
            name='Admin',
            password=generate_password_hash('1234', method='scrypt')
        )
        db.session.add(admin_user)
        print(f"✅ Admin Restored: {target_email}")
    
    # 2. Create Polls
    poll_data = [('toxic', '☠️ 독소조항'), ('terms', '🤯 어려운 용어'), ('money', '💸 돈 떼일까 봐')]
    for pid, label in poll_data:
        if not db.session.get(Poll, pid): 
            db.session.add(Poll(id=pid, label=label, count=10))
            
    db.session.commit()
    print("✅ Database Ready")

if __name__ == '__main__':
    app.run(debug=True, port=5005)