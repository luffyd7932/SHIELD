from flask import Flask, request, render_template, redirect, session, flash, jsonify, url_for
from flask_sqlalchemy import SQLAlchemy
import openai

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your_secret_key'  # Use environment variable for secret key
db = SQLAlchemy(app)

api_key = ""
openai.api_key = api_key

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

    def __init__(self, name, email, password):
        self.name = name
        self.email = email
        self.password = password
    
    def check_password(self, password):
        return self.password == password

class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text_box_1 = db.Column(db.String(100), nullable=False)
    text_box_2 = db.Column(db.String(1500), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('entries', lazy=True))

class VerificationStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sms_id = db.Column(db.Integer, db.ForeignKey('entry.id'), nullable=False)
    content = db.Column(db.String(1500), nullable=False)
    verification_status = db.Column(db.String(100), nullable=False)
    entry = db.relationship('Entry', backref=db.backref('verification_status', lazy=True))

class RiskScore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_name = db.Column(db.String(100), unique=True, nullable=False)
    score = db.Column(db.Integer, nullable=False, default=0)


@app.before_request
def create_tables():
    db.create_all()

def generate_response(prompt):
    prompt_with_intro = "Rate the SMS from 1 to 5 where 1 is SAFE and 5 is FRAUD. " + prompt
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt_with_intro},
            ],
        )
        return response['choices'][0]['message']['content']
    except Exception as e:
        print(f"Error generating response: {e}")
        return "Error generating response"

def classify_response(response_text):
    if any(num in response_text for num in ["4", "5"]):
        return "FRAUD"
    elif any(num in response_text for num in ["1", "2", "3"]):
        return "SAFE"
    else:
        return "Could not Identify"

def verify_sender(sender_name):
    prompt = f"{sender_name} Give the Indian SMS Sender name. Only give the operator and brand or service no other words."
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=20
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"Error verifying sender: {e}")
        return "Error verifying sender"
    
def normalize_risk_score(score):
    max_score = 5
    normalized_score = min(max_score, (score / (score + 1)) * max_score)
    return round(normalized_score, 2)

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    name = request.form['name']
    email = request.form['email']
    password = request.form['password']

    if User.query.filter_by(email=email).first():
        flash('Email address already registered')
        return redirect('/')

    new_user = User(name=name, email=email, password=password)
    db.session.add(new_user)
    db.session.commit()
    flash('Account created successfully! Please log in.')
    return redirect('/')

@app.route('/login', methods=['POST'])
def login():
    email = request.form['email']
    password = request.form['password']

    user = User.query.filter_by(email=email).first()
    
    if user and user.check_password(password):
        session['email'] = user.email
        return redirect('/dashboard')
    else:
        flash('Invalid credentials')
        return redirect('/')
    
@app.route('/dashboard')
def dashboard():
    if 'email' in session:
        user = User.query.filter_by(email=session['email']).first()
        
        # Count total entries for the user
        entries = Entry.query.filter_by(user_id=user.id).all()
        total_entries = len(entries)
        
        # Count safe entries for the user
        safe_count = VerificationStatus.query.filter(VerificationStatus.entry.has(user_id=user.id), VerificationStatus.verification_status.contains("SAFE")).count()
        
        # Count fraud entries for the user
        fraud_count = VerificationStatus.query.filter(VerificationStatus.entry.has(user_id=user.id), VerificationStatus.verification_status.contains("FRAUD")).count()
        
        return render_template('dashboard.html', user=user, total_entries=total_entries, safe_count=safe_count, fraud_count=fraud_count)
    return redirect('/')



@app.route('/sms', methods=['GET', 'POST'])
def sms():
    if 'email' in session:
        user = User.query.filter_by(email=session['email']).first()
        if request.method == 'POST':
            text_box_1 = request.form['text_box_1']
            text_box_2 = request.form['text_box_2']
            if text_box_1 and text_box_2:
                sender_verification = verify_sender(text_box_1)
                gpt_response = generate_response(text_box_2)
                classification = classify_response(gpt_response)
                
                new_entry = Entry(text_box_1=text_box_1, text_box_2=text_box_2, user_id=user.id)
                db.session.add(new_entry)
                db.session.commit()
                
                verification_status = VerificationStatus(
                    sms_id=new_entry.id,
                    content=text_box_2,
                    verification_status=f"{classification}, {sender_verification}"
                )
                db.session.add(verification_status)
                db.session.commit()

                risk_score = RiskScore.query.filter_by(sender_name=text_box_1).first()
                if not risk_score:
                    risk_score = RiskScore(sender_name=text_box_1, score=0)
                    db.session.add(risk_score)
                    db.session.commit()

                normalized_score = normalize_risk_score(risk_score.score)

                return jsonify(success=True, classification=classification, sender_verification=sender_verification, risk_score=normalized_score)
        return render_template('sms.html', user=user)
    return redirect('/')

@app.route('/report', methods=['POST'])
def report():
    sender_name = request.form['sender_name']
    risk_score = RiskScore.query.filter_by(sender_name=sender_name).first()
    if risk_score:
        risk_score.score += 1
        db.session.commit()
    else:
        risk_score = RiskScore(sender_name=sender_name, score=1)
        db.session.add(risk_score)
        db.session.commit()
    
    normalized_score = normalize_risk_score(risk_score.score)
    
    return jsonify(success=True, new_risk_score=normalized_score)


@app.route('/view_entries_sms')
def view_entries_sms():
    if 'email' in session:
        user = User.query.filter_by(email=session['email']).first()
        if user:
            entries = VerificationStatus.query.join(Entry).filter(Entry.user_id == user.id).order_by(Entry.id.desc()).all()
            return render_template('view_entries_sms.html', entries=entries)
    return redirect('/')

@app.route('/logout')
def logout():
    session.pop('email', None)
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)
