from flask import Flask, render_template, request, redirect, url_for
import json, os, re, unicodedata
from PIL import Image
from google import genai
import uuid
from datetime import datetime
from flask import session
import random
from flask import jsonify
import fitz  # PyMuPDF
from flask import flash
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from threading import Lock

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")

# Cấu hình thư mục upload
UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GOOGLE_API_KEYS"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GOOGLE_API_KEYS"].split(",")[0].strip()

GLOBAL_BACK_BUTTON_HTML = """
<style>
    .global-back-button {
        position: fixed !important;
        top: 16px !important;
        left: 16px !important;
        z-index: 2147483000;
        display: inline-flex !important;
        align-items: center;
        justify-content: center;
        width: auto !important;
        max-width: max-content !important;
        min-height: 40px !important;
        margin: 0 !important;
        padding: 0 14px !important;
        color: #1d4ed8 !important;
        background: rgba(255, 255, 255, 0.92) !important;
        border: 1px solid rgba(37, 99, 235, 0.35) !important;
        border-radius: 8px !important;
        box-shadow: 0 10px 24px rgba(37, 99, 235, 0.18) !important;
        font: 600 14px/1.2 Arial, sans-serif !important;
        text-decoration: none !important;
        cursor: pointer !important;
        backdrop-filter: blur(8px);
    }

    .global-back-button:hover {
        background: #ffffff !important;
        box-shadow: 0 14px 30px rgba(37, 99, 235, 0.24) !important;
    }

    @media (max-width: 560px) {
        .global-back-button {
            top: 10px !important;
            left: 10px !important;
            min-height: 36px !important;
            padding: 0 11px !important;
            font-size: 13px !important;
        }
    }
</style>
<button type="button" class="global-back-button" onclick="if (window.history.length > 1) { window.history.back(); } else { window.location.href = '/'; }">← Quay lại</button>
"""


@app.after_request
def add_global_back_button(response):
    if response.status_code != 200 or response.direct_passthrough:
        return response

    if request.path == "/":
        return response

    content_type = response.headers.get("Content-Type", "")
    if "text/html" not in content_type:
        return response

    html = response.get_data(as_text=True)
    existing_navigation_markers = (
        "global-back-button",
        "back-link",
        "back-button",
        "back-btn",
        "btn-back",
        "btn-home",
        "header-btn",
        "Quay lại",
        "Quay về",
        "Trang chủ",
    )

    if "</body>" not in html or any(marker in html for marker in existing_navigation_markers):
        return response

    html = html.replace("</body>", f"{GLOBAL_BACK_BUTTON_HTML}\n</body>", 1)
    response.set_data(html)
    response.headers["Content-Length"] = str(len(response.get_data()))
    return response

api_key = os.environ.get("GOOGLE_API_KEY")  # ← SỬA DÒNG NÀY
if not api_key:  
    raise ValueError(" Thiếu GOOGLE_API_KEY trong file .env")


def get_google_api_keys():
    keys = []
    multi_key_value = os.environ.get("GOOGLE_API_KEYS", "")
    single_key_value = os.environ.get("GOOGLE_API_KEY", "")

    for raw_key in multi_key_value.split(","):
        key = raw_key.strip()
        if key:
            keys.append(key)

    single_key = single_key_value.strip()
    if single_key and single_key not in keys:
        keys.append(single_key)

    return keys


class RotatingGeminiModel:
    def __init__(self, model_name, api_keys):
        self.model_name = model_name
        self.api_keys = api_keys
        self.current_key_index = 0
        self.lock = Lock()

    def _normalized_model_name(self):
        if self.model_name.startswith("models/"):
            return self.model_name.split("/", 1)[1]
        return self.model_name

    def _is_limit_error(self, error):
        status_code = getattr(error, "code", None)
        status_name = getattr(error, "status", "")
        message = str(error).lower()

        return (
            status_code in (429, 503)
            or "resource_exhausted" in status_name.lower()
            or "quota" in message
            or "rate limit" in message
            or "429" in message
            or "403" in message
            or "permission denied" in message
            or "consumer_suspended" in message
            or "api key not valid" in message
        )

    def _set_current_key(self, key_index):
        with self.lock:
            self.current_key_index = key_index % len(self.api_keys)

    def generate_content(self, *args, **kwargs):
        last_error = None
        total_keys = len(self.api_keys)

        with self.lock:
            start_key_index = self.current_key_index

        if args:
            contents = args[0]
            if len(args) > 1:
                raise TypeError("generate_content accepts one positional contents argument")
        else:
            contents = kwargs.pop("contents")

        for attempt in range(total_keys):
            key_index = (start_key_index + attempt) % total_keys
            api_key = self.api_keys[key_index]

            client = genai.Client(api_key=api_key)

            try:
                response = client.models.generate_content(
                    model=self._normalized_model_name(),
                    contents=contents,
                    **kwargs
                )
                self._set_current_key(key_index)
                return response
            except Exception as error:
                last_error = error
                if total_keys == 1 or not self._is_limit_error(error):
                    raise

                self._set_current_key(key_index + 1)

        raise last_error


GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "models/gemini-2.5-flash")
GOOGLE_API_KEYS = get_google_api_keys()
model = RotatingGeminiModel(GEMINI_MODEL, GOOGLE_API_KEYS)
analysis_model = model




CLASS_ACTIVITY_FILE = os.path.join('data', 'class_activities.json')
CLASS_ACTIVITY_IMAGES = os.path.join('static', 'class_activity_uploads')

# Tạo thư mục nếu chưa có
os.makedirs(os.path.dirname(CLASS_ACTIVITY_FILE), exist_ok=True)
os.makedirs(CLASS_ACTIVITY_IMAGES, exist_ok=True)
# Định nghĩa các extension được phép
#############

# ==========================================
# HỆ THỐNG KIỂM TRA CÓ GÌ PHẢI LO
# ==========================================

import hashlib
from werkzeug.security import generate_password_hash, check_password_hash
import mammoth  # Để đọc file .docx

# File paths
EXAM_USERS_FILE = os.path.join('data', 'exam_system_users.json')
EXAM_LESSONS_FILE = os.path.join('data', 'exam_system_lessons.json')
EXAM_EXAMS_FILE = os.path.join('data', 'exam_system_exams.json')
EXAM_SUBMISSIONS_FILE = os.path.join('data', 'exam_system_submissions.json')


# Helper functions
def load_exam_users():
    try:
        with open(EXAM_USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"students": [], "teachers": []}


def save_exam_users(data):
    with open(EXAM_USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_exam_lessons():
    try:
        with open(EXAM_LESSONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_exam_lessons(data):
    with open(EXAM_LESSONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_exam_exams():
    try:
        with open(EXAM_EXAMS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_exam_exams(data):
    with open(EXAM_EXAMS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_exam_submissions():
    try:
        with open(EXAM_SUBMISSIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_exam_submissions(data):
    with open(EXAM_SUBMISSIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------- AUTHENTICATION ----------------
@app.route('/exam_system/student_register', methods=['GET', 'POST'])
def exam_student_register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        class_name = request.form.get('class_name', '').strip()
        email = request.form.get('email', '').strip()

        if not all([username, password, full_name, class_name]):
            flash('Vui lòng nhập đầy đủ thông tin!', 'error')
            return redirect(url_for('exam_student_register'))

        users = load_exam_users()
        if any(s['username'] == username for s in users['students']):
            flash('Tên đăng nhập đã tồn tại!', 'error')
            return redirect(url_for('exam_student_register'))

        new_student = {
            'id': str(uuid.uuid4()),
            'username': username,
            'password': generate_password_hash(password),
            'full_name': full_name,
            'class': class_name,
            'email': email,
            'created_at': datetime.now().strftime("%d/%m/%Y %H:%M")
        }
        users['students'].append(new_student)
        save_exam_users(users)

        flash('Đăng ký thành công! Hãy đăng nhập.', 'success')
        return redirect(url_for('exam_student_login'))

    return render_template('exam_system/auth/student_register.html')


@app.route('/exam_system/student_login', methods=['GET', 'POST'])
def exam_student_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        users = load_exam_users()
        student = next(
            (s for s in users['students'] if s['username'] == username), None)

        if student and check_password_hash(student['password'], password):
            session['exam_user_type'] = 'student'
            session['exam_user_id'] = student['id']
            session['exam_user_name'] = student['full_name']
            flash('Đăng nhập thành công!', 'success')
            return redirect(url_for('student_dashboard'))
        else:
            flash('Sai tên đăng nhập hoặc mật khẩu!', 'error')

    return render_template('exam_system/auth/student_login.html')


@app.route('/exam_system/teacher_login', methods=['GET', 'POST'])
def exam_teacher_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        users = load_exam_users()
        teacher = next(
            (t for t in users['teachers'] if t['username'] == username), None)

        if teacher:
            # Kiểm tra xem password có phải hash không
            teacher_password = teacher['password']

            # Nếu password bắt đầu bằng 'pbkdf2:', 'scrypt:', 'bcrypt:' thì là hash
            if teacher_password.startswith(('pbkdf2:', 'scrypt:', 'bcrypt:')):
                # So sánh dạng hash
                if check_password_hash(teacher_password, password):
                    session['exam_user_type'] = 'teacher'
                    session['exam_user_id'] = teacher['id']
                    session['exam_user_name'] = teacher['full_name']
                    session['exam_subject'] = teacher.get('subject', 'Chung')
                    flash('Đăng nhập thành công!', 'success')
                    return redirect(url_for('teacher_dashboard'))
            else:
                # So sánh plain text
                if teacher_password == password:
                    session['exam_user_type'] = 'teacher'
                    session['exam_user_id'] = teacher['id']
                    session['exam_user_name'] = teacher['full_name']
                    session['exam_subject'] = teacher.get('subject', 'Chung')
                    flash('Đăng nhập thành công!', 'success')
                    return redirect(url_for('teacher_dashboard'))

        flash('Sai tên đăng nhập hoặc mật khẩu!', 'error')

    return render_template('exam_system/auth/teacher_login.html')


@app.route('/exam_system/logout')
def exam_logout():
    session.pop('exam_user_type', None)
    session.pop('exam_user_id', None)
    session.pop('exam_user_name', None)
    session.pop('exam_subject', None)
    flash('Đã đăng xuất!', 'info')
    return redirect(url_for('exam_student_login'))


# ---------------- TEACHER ROUTES ----------------
@app.route('/exam_system/teacher/dashboard')
def teacher_dashboard():
    if session.get('exam_user_type') != 'teacher':
        flash('Vui lòng đăng nhập với tư cách giáo viên!', 'error')
        return redirect(url_for('exam_teacher_login'))

    teacher_id = session.get('exam_user_id')
    lessons = [l for l in load_exam_lessons() if l['teacher_id'] == teacher_id]
    exams = [e for e in load_exam_exams() if e['teacher_id'] == teacher_id]

    return render_template('exam_system/teacher/dashboard.html',
                           lessons=lessons,
                           exams=exams)


@app.route('/exam_system/teacher/create_lesson', methods=['GET', 'POST'])
def teacher_create_lesson():
    if session.get('exam_user_type') != 'teacher':
        return redirect(url_for('exam_teacher_login'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        content = request.form.get('content', '').strip()
        subject = request.form.get('subject', '').strip()
        grade = request.form.get('grade', '').strip()

        attachments = []
        files = request.files.getlist('attachments')
        for f in files:
            if f and f.filename:
                filename = f"{uuid.uuid4()}_{secure_filename(f.filename)}"
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                attachments.append(filename)

        new_lesson = {
            'id': str(uuid.uuid4()),
            'title': title,
            'description': description,
            'content': content,
            'attachments': attachments,
            'teacher_id': session.get('exam_user_id'),
            'created_at': datetime.now().strftime("%d/%m/%Y %H:%M"),
            'subject': subject,
            'grade': grade
        }

        lessons = load_exam_lessons()
        lessons.insert(0, new_lesson)
        save_exam_lessons(lessons)

        flash('Đã tạo bài giảng!', 'success')
        return redirect(url_for('teacher_dashboard'))

    return render_template('exam_system/teacher/create_lesson.html')


@app.route('/exam_system/teacher/create_exam', methods=['GET', 'POST'])
def teacher_create_exam():
    if session.get('exam_user_type') != 'teacher':
        return redirect(url_for('exam_teacher_login'))

    if request.method == 'POST':
        exam_type = request.form.get('exam_type')
        if exam_type == 'multiple_choice':
            return redirect(url_for('teacher_create_multiple_choice'))
        elif exam_type == 'essay':
            return redirect(url_for('teacher_create_essay'))

    return render_template('exam_system/teacher/create_exam.html')


@app.route('/exam_system/teacher/create_multiple_choice',
           methods=['GET', 'POST'])
def teacher_create_multiple_choice():
    if session.get('exam_user_type') != 'teacher':
        return redirect(url_for('exam_teacher_login'))

    if request.method == 'POST':
        if 'word_file' in request.files:
            word_file = request.files['word_file']
            if word_file and word_file.filename.endswith('.docx'):
                # Đọc nội dung Word
                word_content = mammoth.extract_raw_text(word_file).value

                # Dùng AI parse thành JSON
                prompt = f"""Đây là nội dung đề trắc nghiệm từ file Word:

{word_content}

Hãy chuyển đổi thành JSON với format:
{{
  "questions": [
    {{
      "id": 1,
      "question": "Câu hỏi",
      "options": ["A. Đáp án 1", "B. Đáp án 2", "C. Đáp án 3", "D. Đáp án 4"],
      "correct_answer": "A",
      "explanation": "Giải thích"
    }}
  ]
}}

CHỈ TRẢ VỀ JSON, KHÔNG THÊM TEXT KHÁC."""

                try:
                    response = model.generate_content([prompt])
                    ai_json = response.text.replace('```json',
                                                    '').replace('```',
                                                                '').strip()
                    questions_data = json.loads(ai_json)

                    # Lưu vào session để preview
                    session['preview_questions'] = questions_data

                    return render_template(
                        'exam_system/teacher/preview_questions.html',
                        questions=questions_data['questions'])
                except Exception as e:
                    flash(f'Lỗi khi parse file: {str(e)}', 'error')

        # Nếu confirm từ preview
        if request.form.get('confirm') == 'yes':
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            time_limit = request.form.get('time_limit', '0')
            subject = request.form.get('subject', '').strip()
            grade = request.form.get('grade', '').strip()

            questions_json = request.form.get('questions_json')
            questions = json.loads(questions_json)

            new_exam = {
                'id': str(uuid.uuid4()),
                'title': title,
                'description': description,
                'type': 'multiple_choice',
                'teacher_id': session.get('exam_user_id'),
                'created_at': datetime.now().strftime("%d/%m/%Y %H:%M"),
                'time_limit': int(time_limit),
                'subject': subject,
                'grade': grade,
                'status': 'active',
                'questions': questions
            }

            exams = load_exam_exams()
            exams.insert(0, new_exam)
            save_exam_exams(exams)

            flash('Đã tạo đề trắc nghiệm!', 'success')
            return redirect(url_for('teacher_dashboard'))

    return render_template('exam_system/teacher/create_multiple_choice.html')


@app.route('/exam_system/teacher/create_essay', methods=['GET', 'POST'])
def teacher_create_essay():
    if session.get('exam_user_type') != 'teacher':
        return redirect(url_for('exam_teacher_login'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        time_limit = request.form.get('time_limit', '0')
        subject = request.form.get('subject', '').strip()
        grade = request.form.get('grade', '').strip()

        # Lấy các câu hỏi tự luận
        questions = []
        i = 0
        while True:
            q_text = request.form.get(f'question_{i}')
            if not q_text:
                break
            points = request.form.get(f'points_{i}', '10')
            suggested = request.form.get(f'suggested_{i}', '')

            questions.append({
                'id': i + 1,
                'question': q_text,
                'points': int(points),
                'suggested_answer': suggested
            })
            i += 1

        new_exam = {
            'id': str(uuid.uuid4()),
            'title': title,
            'description': description,
            'type': 'essay',
            'teacher_id': session.get('exam_user_id'),
            'created_at': datetime.now().strftime("%d/%m/%Y %H:%M"),
            'time_limit': int(time_limit),
            'subject': subject,
            'grade': grade,
            'status': 'active',
            'essay_questions': questions
        }

        exams = load_exam_exams()
        exams.insert(0, new_exam)
        save_exam_exams(exams)

        flash('Đã tạo đề tự luận!', 'success')
        return redirect(url_for('teacher_dashboard'))

    return render_template('exam_system/teacher/create_essay.html')


@app.route('/exam_system/teacher/view_submissions/<exam_id>')
def teacher_view_submissions(exam_id):
    if session.get('exam_user_type') != 'teacher':
        return redirect(url_for('exam_teacher_login'))

    exam = next((e for e in load_exam_exams() if e['id'] == exam_id), None)
    if not exam:
        flash('Không tìm thấy đề!', 'error')
        return redirect(url_for('teacher_dashboard'))

    submissions = [
        s for s in load_exam_submissions() if s['exam_id'] == exam_id
    ]
    users = load_exam_users()

    # Ghép thông tin học sinh
    for sub in submissions:
        student = next(
            (s for s in users['students'] if s['id'] == sub['student_id']),
            None)
        sub['student_name'] = student['full_name'] if student else 'Unknown'
        sub['student_class'] = student.get('class', '') if student else ''

    return render_template('exam_system/teacher/view_submissions.html',
                           exam=exam,
                           submissions=submissions)


@app.route('/exam_system/teacher/view_submission/<submission_id>')
def teacher_view_submission(submission_id):
    if session.get('exam_user_type') != 'teacher':
        return redirect(url_for('exam_teacher_login'))

    submission = next(
        (s for s in load_exam_submissions() if s['id'] == submission_id), None)
    if not submission:
        flash('Không tìm thấy bài làm!', 'error')
        return redirect(url_for('teacher_dashboard'))

    exam = next(
        (e for e in load_exam_exams() if e['id'] == submission['exam_id']),
        None)
    users = load_exam_users()
    student = next(
        (s for s in users['students'] if s['id'] == submission['student_id']),
        None)

    return render_template('exam_system/teacher/view_submission_detail.html',
                           submission=submission,
                           exam=exam,
                           student=student)


@app.route('/exam_system/teacher/delete_exam/<exam_id>', methods=['POST'])
def teacher_delete_exam(exam_id):
    if session.get('exam_user_type') != 'teacher':
        return redirect(url_for('exam_teacher_login'))

    exams = load_exam_exams()
    exams = [e for e in exams if e['id'] != exam_id]
    save_exam_exams(exams)

    flash('Đã xóa đề kiểm tra!', 'success')
    return redirect(url_for('teacher_dashboard'))


# ---------------- STUDENT ROUTES ----------------
@app.route('/exam_system/student/dashboard')
def student_dashboard():
    if session.get('exam_user_type') != 'student':
        flash('Vui lòng đăng nhập với tư cách học sinh!', 'error')
        return redirect(url_for('exam_student_login'))

    lessons = load_exam_lessons()
    exams = [e for e in load_exam_exams() if e['status'] == 'active']

    return render_template('exam_system/student/dashboard.html',
                           lessons=lessons,
                           exams=exams)


@app.route('/exam_system/student/view_lesson/<lesson_id>')
def student_view_lesson(lesson_id):
    if session.get('exam_user_type') != 'student':
        return redirect(url_for('exam_student_login'))

    lesson = next((l for l in load_exam_lessons() if l['id'] == lesson_id),
                  None)
    if not lesson:
        flash('Không tìm thấy bài giảng!', 'error')
        return redirect(url_for('student_dashboard'))

    return render_template('exam_system/student/view_lesson.html',
                           lesson=lesson)


@app.route('/exam_system/student/take_exam/<exam_id>', methods=['GET', 'POST'])
def student_take_exam(exam_id):
    if session.get('exam_user_type') != 'student':
        return redirect(url_for('exam_student_login'))

    exam = next((e for e in load_exam_exams() if e['id'] == exam_id), None)
    if not exam:
        flash('Không tìm thấy đề!', 'error')
        return redirect(url_for('student_dashboard'))

    if request.method == 'POST':
        student_id = session.get('exam_user_id')
        time_taken = request.form.get('time_taken', '0')

        submission_id = str(uuid.uuid4())

        if exam['type'] == 'multiple_choice':
            answers = {}
            for q in exam['questions']:
                ans = request.form.get(f"q_{q['id']}")
                answers[str(q['id'])] = ans

            # Chấm điểm trắc nghiệm
            correct_count = 0
            detailed_results = []
            for q in exam['questions']:
                student_ans = answers.get(str(q['id']))
                is_correct = (student_ans == q['correct_answer'])
                if is_correct:
                    correct_count += 1

                detailed_results.append({
                    'question_id': q['id'],
                    'question': q['question'],
                    'is_correct': is_correct,
                    'student_answer': student_ans,
                    'correct_answer': q['correct_answer'],
                    'explanation': q.get('explanation', '')
                })

            score = round((correct_count / len(exam['questions'])) * 10, 2)

            # AI feedback
            prompt = f"""Học sinh làm đúng {correct_count}/{len(exam['questions'])} câu trắc nghiệm.

Hãy đưa ra:
1. Nhận xét chung về kết quả
2. Phân tích điểm mạnh/yếu
3. Lời khuyên cải thiện

Trả lời ngắn gọn, khuyến khích."""

            try:
                response = model.generate_content([prompt])
                ai_feedback = clean_ai_output(response.text)
            except:
                ai_feedback = "Không có nhận xét từ AI."

            submission = {
                'id': submission_id,
                'exam_id': exam_id,
                'student_id': student_id,
                'submitted_at': datetime.now().strftime("%d/%m/%Y %H:%M"),
                'time_taken': int(time_taken),
                'answers': answers,
                'score': score,
                'ai_feedback': ai_feedback,
                'detailed_results': detailed_results
            }

        elif exam['type'] == 'essay':
            essay_answers = {}
            for q in exam['essay_questions']:
                ans = request.form.get(f"essay_{q['id']}", '').strip()
                essay_answers[str(q['id'])] = ans

            # Chấm điểm tự luận bằng AI
            total_points = 0
            detailed_results = []

            for q in exam['essay_questions']:
                student_ans = essay_answers.get(str(q['id']), '')

                prompt = f"""Đây là câu hỏi tự luận:

Câu hỏi: {q['question']}
Điểm tối đa: {q['points']}
Đáp án gợi ý: {q.get('suggested_answer', 'Không có')}

Câu trả lời của học sinh:
{student_ans}

Hãy chấm điểm (0-{q['points']}) và nhận xét ngắn gọn.
Format: ĐIỂM: X/{q['points']}
NHẬN XÉT: ..."""

                try:
                    response = model.generate_content([prompt])
                    feedback = clean_ai_output(response.text)

                    # Trích xuất điểm
                    import re
                    match = re.search(r'ĐIỂM:\s*(\d+\.?\d*)', feedback)
                    q_score = float(match.group(1)) if match else 0
                except:
                    feedback = "Không chấm được."
                    q_score = 0

                total_points += q_score
                detailed_results.append({
                    'question_id': q['id'],
                    'question': q['question'],
                    'student_answer': student_ans,
                    'points': q['points'],
                    'score': q_score,
                    'feedback': feedback
                })

            max_points = sum(q['points'] for q in exam['essay_questions'])
            score = round((total_points / max_points) * 10, 2)

            submission = {
                'id': submission_id,
                'exam_id': exam_id,
                'student_id': student_id,
                'submitted_at': datetime.now().strftime("%d/%m/%Y %H:%M"),
                'time_taken': int(time_taken),
                'essay_answers': essay_answers,
                'score': score,
                'ai_feedback': f"Tổng điểm: {total_points}/{max_points}",
                'detailed_results': detailed_results
            }

        submissions = load_exam_submissions()
        submissions.insert(0, submission)
        save_exam_submissions(submissions)

        flash('Đã nộp bài!', 'success')
        return redirect(
            url_for('student_view_result', submission_id=submission_id))

    return render_template('exam_system/student/take_exam.html', exam=exam)


@app.route('/exam_system/student/view_result/<submission_id>')
def student_view_result(submission_id):
    if session.get('exam_user_type') != 'student':
        return redirect(url_for('exam_student_login'))

    submission = next(
        (s for s in load_exam_submissions() if s['id'] == submission_id), None)
    if not submission:
        flash('Không tìm thấy bài làm!', 'error')
        return redirect(url_for('student_dashboard'))

    exam = next(
        (e for e in load_exam_exams() if e['id'] == submission['exam_id']),
        None)

    return render_template('exam_system/student/view_result.html',
                           submission=submission,
                           exam=exam)


@app.route('/exam_system/student/my_submissions')
def student_my_submissions():
    if session.get('exam_user_type') != 'student':
        return redirect(url_for('exam_student_login'))

    student_id = session.get('exam_user_id')
    submissions = [
        s for s in load_exam_submissions() if s['student_id'] == student_id
    ]

    exams = load_exam_exams()
    for sub in submissions:
        exam = next((e for e in exams if e['id'] == sub['exam_id']), None)
        sub['exam_title'] = exam['title'] if exam else 'Unknown'

    return render_template('exam_system/student/my_submissions.html',
                           submissions=submissions)


#################
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'pdf'}


def allowed_file(filename):
    """Kiểm tra file có extension hợp lệ không"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text_from_pdf(pdf_path):
    """Trích xuất text từ file PDF"""
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except Exception as e:
        return f"Lỗi khi đọc PDF: {str(e)}"


####
### shl
#################
def load_class_activities():
    """Load danh sách các phiên sinh hoạt lớp"""
    try:
        with open(CLASS_ACTIVITY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_class_activities(data):
    """Lưu danh sách sinh hoạt lớp"""
    with open(CLASS_ACTIVITY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route('/class_activity', methods=['GET'])
def class_activity():
    """Trang chính - Danh sách các phiên sinh hoạt"""
    activities = load_class_activities()
    return render_template('class_activity.html', activities=activities)


@app.route('/class_activity/new', methods=['GET', 'POST'])
def new_class_activity():
    """Tạo phiên sinh hoạt mới"""
    if request.method == 'POST':
        week_name = request.form.get('week_name', '').strip()
        description = request.form.get('description', '').strip()

        if not week_name:
            flash('Vui lòng nhập tên tuần sinh hoạt!', 'error')
            return redirect(url_for('new_class_activity'))

        activity_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")

        new_activity = {
            'id': activity_id,
            'week_name': week_name,
            'description': description,
            'created_at': timestamp,
            'status': 'collecting',  # collecting, analyzed
            'groups': {
                'to_1': [],
                'to_2': [],
                'to_3': [],
                'to_4': [],
                'giao_vien': []
            },
            'ai_analysis': None
        }

        activities = load_class_activities()
        activities.insert(0, new_activity)
        save_class_activities(activities)

        flash('Đã tạo phiên sinh hoạt mới!', 'success')
        return redirect(
            url_for('class_activity_detail', activity_id=activity_id))

    return render_template('new_class_activity.html')


###
def load_chat_messages(activity_id):
    """Load tin nhắn chat của một phiên sinh hoạt"""
    try:
        with open(CLASS_CHAT_FILE, 'r', encoding='utf-8') as f:
            all_chats = json.load(f)
            return all_chats.get(activity_id, [])
    except FileNotFoundError:
        return []


def save_chat_message(activity_id, message_data):
    """Lưu tin nhắn chat mới"""
    try:
        with open(CLASS_CHAT_FILE, 'r', encoding='utf-8') as f:
            all_chats = json.load(f)
    except FileNotFoundError:
        all_chats = {}

    if activity_id not in all_chats:
        all_chats[activity_id] = []

    all_chats[activity_id].append(message_data)

    with open(CLASS_CHAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_chats, f, ensure_ascii=False, indent=2)


@app.route('/class_activity/<activity_id>/chat', methods=['GET'])
def class_activity_chat(activity_id):
    """Trang chat ẩn danh của lớp"""
    activities = load_class_activities()
    activity = next((a for a in activities if a['id'] == activity_id), None)

    if not activity:
        flash('Không tìm thấy phiên sinh hoạt!', 'error')
        return redirect(url_for('class_activity'))

    messages = load_chat_messages(activity_id)

    return render_template('class_activity_chat.html',
                           activity=activity,
                           messages=messages)


@app.route('/class_activity/<activity_id>/chat/send', methods=['POST'])
def send_chat_message(activity_id):
    """Gửi tin nhắn chat"""
    activities = load_class_activities()
    activity = next((a for a in activities if a['id'] == activity_id), None)

    if not activity:
        return jsonify({'success': False, 'error': 'Activity not found'}), 404

    data = request.get_json()
    message_text = data.get('message', '').strip()
    nickname = data.get('nickname', '').strip()

    if not message_text:
        return jsonify({'success': False, 'error': 'Message is empty'}), 400

    if not nickname:
        nickname = 'Ẩn danh'

    # Tạo message data
    message_data = {
        'id': str(uuid.uuid4()),
        'nickname': nickname,
        'message': message_text,
        'timestamp': datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        'avatar_color': generate_avatar_color(nickname)
    }

    save_chat_message(activity_id, message_data)

    return jsonify({'success': True, 'message': message_data})


@app.route('/class_activity/<activity_id>/chat/messages', methods=['GET'])
def get_chat_messages(activity_id):
    """Lấy danh sách tin nhắn (API cho auto-refresh)"""
    messages = load_chat_messages(activity_id)
    return jsonify({'success': True, 'messages': messages})


def generate_avatar_color(nickname):
    """Tạo màu avatar dựa trên nickname"""
    colors = [
        '#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8', '#F7DC6F',
        '#BB8FCE', '#85C1E2', '#F8B500', '#52B788', '#E63946', '#457B9D'
    ]
    # Hash nickname để lấy màu cố định cho mỗi nickname
    hash_value = sum(ord(c) for c in nickname)
    return colors[hash_value % len(colors)]


#####
@app.route('/class_activity/<activity_id>', methods=['GET', 'POST'])
def class_activity_detail(activity_id):
    """Chi tiết phiên sinh hoạt - Upload ảnh cho từng tổ"""
    activities = load_class_activities()
    activity = next((a for a in activities if a['id'] == activity_id), None)

    if not activity:
        flash('Không tìm thấy phiên sinh hoạt!', 'error')
        return redirect(url_for('class_activity'))

    if request.method == 'POST':
        group_name = request.form.get('group_name')
        uploaded_files = request.files.getlist('images')

        if not group_name or group_name not in activity['groups']:
            flash('Tổ không hợp lệ!', 'error')
            return redirect(
                url_for('class_activity_detail', activity_id=activity_id))

        if not uploaded_files or all(f.filename == '' for f in uploaded_files):
            flash('Vui lòng chọn ít nhất 1 ảnh!', 'error')
            return redirect(
                url_for('class_activity_detail', activity_id=activity_id))

        # Xử lý từng file
        for uploaded_file in uploaded_files:
            if uploaded_file and uploaded_file.filename != '':
                if not allowed_file(uploaded_file.filename):
                    continue

                # Lưu file
                file_id = str(uuid.uuid4())
                filename = f"{file_id}_{secure_filename(uploaded_file.filename)}"
                file_path = os.path.join(CLASS_ACTIVITY_IMAGES, filename)
                uploaded_file.save(file_path)

                # Thêm vào group
                activity['groups'][group_name].append({
                    'id':
                    file_id,
                    'filename':
                    filename,
                    'uploaded_at':
                    datetime.now().strftime("%d/%m/%Y %H:%M")
                })

        # Cập nhật activity
        for i, a in enumerate(activities):
            if a['id'] == activity_id:
                activities[i] = activity
                break

        save_class_activities(activities)

        flash(f'Đã upload ảnh cho {group_name}!', 'success')
        return redirect(
            url_for('class_activity_detail', activity_id=activity_id))

    return render_template('class_activity_detail.html', activity=activity)


#####
#####
@app.route('/class_activity/<activity_id>/analyze', methods=['POST'])
def analyze_class_activity(activity_id):
    """AI phân tích tất cả báo cáo của các tổ VÀ tạo HTML infographic"""
    activities = load_class_activities()
    activity = next((a for a in activities if a['id'] == activity_id), None)

    if not activity:
        flash('Không tìm thấy phiên sinh hoạt!', 'error')
        return redirect(url_for('class_activity'))

    # Kiểm tra xem có đủ dữ liệu không
    total_images = sum(len(images) for images in activity['groups'].values())
    if total_images == 0:
        flash(
            'Chưa có ảnh nào được upload. Vui lòng upload ảnh trước khi phân tích!',
            'error')
        return redirect(
            url_for('class_activity_detail', activity_id=activity_id))

    try:
        # ========================================
        # BƯỚC 1: PHÂN TÍCH TEXT TỪ ẢNH CÁC TỔ
        # ========================================
        analysis_prompt = [
            f"""Bạn là giáo viên chủ nhiệm đang đánh giá sinh hoạt lớp tuần này.

THÔNG TIN TUẦN SINH HOẠT:
- Tên: {activity['week_name']}
- Mô tả: {activity.get('description', 'Không có')}

NHIỆM VỤ:
1. Phân tích báo cáo của 4 tổ (Tổ 1, 2, 3, 4)
2. Đánh giá từng tổ: điểm mạnh, điểm yếu, cho điểm (0-10)
3. So sánh các tổ và xếp hạng
4. Đối chiếu với báo cáo giáo viên (nếu có)
5. Trích xuất THỜI KHÓA BIỂU từ ảnh (nếu có)
6. Đánh giá các tiêu chí: Ký luật, Nội quy, Chuẩn bị bài, Vệ sinh
7. Đề xuất phương hướng tuần mới CỤ THỂ (4-5 mục tiêu)
8. Khen cá nhân tập thể có điểm số thi đua cao tặng huy hiệu thi đua tuần cho cá nhân và tập thể tổ đó
**LƯU Ý VỀ ĐỒNG PHỤC:**
- CHỈ sử dụng các lựa chọn SAU ĐÂY:
  + "Đồng phục áo trắng, quần tối màu"
  + "Đồng phục thể dục"
  + "Áo khoác mùa đông" (có thể kết hợp với các loại trên)
- KHÔNG được viết "váy",hay bất kỳ từ ngữ nào khác
- VÍ DỤ ĐÚNG: "Đồng phục áo trắng, quần tối màu, áo khoác mùa đông"
- VÍ DỤ SAI: "Áo trắng, váy tối màu"

ĐỊNH DẠNG PHẢN HỒI (JSON) - BẮT BUỘC:
{{
  "tong_quan": "Tổng quan về tuần học...",
  "thoi_khoa_bieu": [
    {{"thu": "Thứ 2", "tiet_1": "Toán", "tiet_2": "Văn", "tiet_3": "Anh", "tiet_4": "Hóa", "tiet_5": "Thể dục", "do_dong_phuc": "Đồng phục áo trắng, quần tối màu"}},
    {{"thu": "Thứ 3", "tiet_1": "Lý", "tiet_2": "Sinh", "tiet_3": "Sử", "tiet_4": "Địa", "tiet_5": "GDCD", "do_dong_phuc": "Đồng phục thể dục"}},
    {{"thu": "Thứ 4", "tiet_1": "Toán", "tiet_2": "Văn", "tiet_3": "Anh", "tiet_4": "Vật lý", "tiet_5": "TD", "do_dong_phuc": "Đồng phục áo trắng, quần tối màu, áo khoác mùa đông"}},
    {{"thu": "Thứ 5", "tiet_1": "Toán", "tiet_2": "Văn", "tiet_3": "Anh", "tiet_4": "Hóa", "tiet_5": "Sinh", "do_dong_phuc": "Đồng phục thể dục, áo khoác mùa đông"}},
    {{"thu": "Thứ 6", "tiet_1": "Toán", "tiet_2": "Văn", "tiet_3": "Anh", "tiet_4": "Sử", "tiet_5": "TD", "do_dong_phuc": "Đồng phục áo trắng, quần tối màu"}}
  ],
  "danh_gia_chi_tiet": {{
    "to_1": {{"diem_manh": "Học tập tốt", "diem_yeu": "Đi trễ", "xep_loai": "Tốt", "diem": 9}},
    "to_2": {{"diem_manh": "Đoàn kết", "diem_yeu": "Chưa tích cực", "xep_loai": "Khá", "diem": 8}},
    "to_3": {{"diem_manh": "Sáng tạo", "diem_yeu": "Vệ sinh chưa tốt", "xep_loai": "Khá", "diem": 7.5}},
    "to_4": {{"diem_manh": "Năng động", "diem_yeu": "Chú ý giờ giấc", "xep_loai": "TB", "diem": 7}}
  }},
  "nhan_xet_tuan_qua": [
    {{"tieu_chi": "Ký luật giờ học", "danh_gia": "Vẫn còn chuyện riêng", "xep_loai": "Khá", "icon": "📚"}},
    {{"tieu_chi": "Nội quy lớp", "danh_gia": "Sai trang phục", "xep_loai": "Trung bình", "icon": "👔"}},
    {{"tieu_chi": "Chuẩn bị bài vở", "danh_gia": "Chưa đầy đủ", "xep_loai": "Cần cải thiện", "icon": "📖"}},
    {{"tieu_chi": "Vệ sinh lớp học", "danh_gia": "Đã cải thiện", "xep_loai": "Tốt", "icon": "🧹"}}
  ],
  "phuong_huong_tuan_moi": [
    "Ôn tập chủ động, chuẩn bị bài trước khi đến lớp",
    "Nghiêm túc tập trung, tham gia phát biểu tích cực",
    "Hoàn thành bài tập đầy đủ, nộp đúng hạn",
    "Khen những học sinh đạt điểm cao tặng huy hiệu cho học sinh đó",
    "Giữ gìn vệ sinh, không xả rác bừa bãi"
  ]
}}

CHỈ TRẢ VỀ JSON, KHÔNG THÊM TEXT KHÁC.

Dưới đây là báo cáo các tổ:
"""
        ]

        # Thêm ảnh của từng tổ
        for group_name, images in activity['groups'].items():
            if images:
                group_display = {
                    'to_1': 'TỔ 1',
                    'to_2': 'TỔ 2',
                    'to_3': 'TỔ 3',
                    'to_4': 'TỔ 4',
                    'giao_vien': 'GIÁO VIÊN'
                }
                analysis_prompt.append(
                    f"\n--- BÁO CÁO {group_display[group_name]} ---")

                for img_data in images:
                    img_path = os.path.join(CLASS_ACTIVITY_IMAGES,
                                            img_data['filename'])
                    if os.path.exists(img_path):
                        img = Image.open(img_path)
                        analysis_prompt.append(img)

        # Gọi Gemini phân tích
        analysis_response = model.generate_content(analysis_prompt)
        ai_analysis = clean_ai_output(analysis_response.text)

        # Parse JSON
        try:
            # Loại bỏ markdown code blocks
            ai_analysis_clean = ai_analysis.replace('```json',
                                                    '').replace('```',
                                                                '').strip()
            analysis_data = json.loads(ai_analysis_clean)
        except Exception as parse_error:
            print(f"JSON Parse Error: {parse_error}")
            print(f"AI Response: {ai_analysis}")
            # Tạo data mẫu nếu parse thất bại
            analysis_data = {
                "tong_quan":
                "Không thể phân tích được dữ liệu từ ảnh.",
                "thoi_khoa_bieu": [{
                    "thu": "Thứ 2",
                    "tiet_1": "Toán",
                    "tiet_2": "Văn",
                    "tiet_3": "Anh",
                    "tiet_4": "Hóa",
                    "tiet_5": "TD"
                }, {
                    "thu": "Thứ 3",
                    "tiet_1": "Lý",
                    "tiet_2": "Sinh",
                    "tiet_3": "Sử",
                    "tiet_4": "Địa",
                    "tiet_5": "GDCD"
                }],
                "nhan_xet_tuan_qua": [{
                    "tieu_chi": "Học tập",
                    "danh_gia": "Tốt",
                    "xep_loai": "Khá",
                    "icon": "✅"
                }],
                "phuong_huong_tuan_moi":
                ["Ôn tập chủ động", "Tham gia phát biểu"]
            }

        # ========================================
        # BƯỚC 2: TẠO HTML INFOGRAPHIC ĐẦY ĐỦ
        # ========================================

        # Build thời khóa biểu HTML
        tkb_html = ""
        for day_info in analysis_data.get('thoi_khoa_bieu', [])[:5]:
            thu = day_info.get('thu', 'Thứ 2')
            tkb_html += f"<tr><td colspan='3' style='background: #2196F3; color: white; font-weight: bold; text-align: center;'>{thu}</td></tr>"
            for i in range(1, 6):
                mon = day_info.get(f'tiet_{i}', '-')
                tkb_html += f"<tr><td style='text-align:center; font-weight:bold;'>{i}</td><td>{mon}</td><td style='text-align:center;'>📚</td></tr>"
            # Thêm info đồng phục nếu có
            do_dp = day_info.get('do_dong_phuc', '')
            if do_dp:
                tkb_html += f"<tr><td colspan='3' style='background:#e3f2fd; text-align:center; padding:8px;'>👔 {do_dp}</td></tr>"

        # Build nhận xét tuần qua
        nhan_xet_html = ""
        for item in analysis_data.get('nhan_xet_tuan_qua', [])[:6]:
            icon = item.get('icon', '✅')
            tieu_chi = item.get('tieu_chi', '')
            danh_gia = item.get('danh_gia', '')
            xep_loai = item.get('xep_loai', '')

            nhan_xet_html += f"""
            <div class="eval-row">
                <div class="eval-icon">{icon}</div>
                <div class="eval-label">{tieu_chi}</div>
                <div class="eval-content">
                    <div>{danh_gia}</div>
                    <span class="eval-badge">{xep_loai}</span>
                </div>
            </div>
            """

        # Build phương hướng tuần mới
        phuong_huong_html = ""
        for item in analysis_data.get('phuong_huong_tuan_moi', [])[:5]:
            phuong_huong_html += f"""
            <div class="goal-item">
                <div class="goal-icon">✅</div>
                <div class="goal-text">{item}</div>
            </div>
            """

        # HTML PROMPT ĐẦY ĐỦ
        html_prompt = f"""Tạo file HTML HOÀN CHỈNH cho infographic kế hoạch tuần học lớp 8A9 - THCS Cẩm Phả.

YÊU CẦU BẮT BUỘC:
- File HTML hoàn chỉnh: <!DOCTYPE html>, <html lang="vi">, <head> với <meta charset="UTF-8">
- Kích thước: 1200px width, chiều cao tự động
- Design 2.5D hiện đại, giống hình mẫu đã gửi
- Background: gradient pastel giống lớp học (#e8d5c4 → #d4b5a0)
- Header: gradient xanh dương (#4facfe → #00f2fe), logo trường, mặt trời icon
- Layout: Grid 2 cột cho phần chính
- Font: 'Segoe UI', sans-serif - hỗ trợ tiếng Việt có dấu
- Thêm CDN: html2canvas từ https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js
- Nút "TẢI XUỐNG ẢNH PNG" với function downloadImage()
- Box có shadow, border-radius, viền màu gradient

CẤU TRÚC CHÍNH:

=== HEADER ===
<div id="infographic" style="width:1200px; background: linear-gradient(135deg, #e8d5c4 0%, #d4b5a0 100%);">
  <div class="header" style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); padding:30px; position:relative;">
    <div class="logo" style="position:absolute; top:20px; left:30px; background:white; width:80px; height:80px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:bold; color:#3a8fd9;">THCS<br>CẨM PHẢ</div>
    <span style="position:absolute; top:20px; left:120px; font-size:60px;">☀️</span>
    <h1 style="text-align:center; color:white; font-size:48px; text-shadow: 3px 3px 6px rgba(0,0,0,0.3); margin-bottom:10px;">KẾ HOẠCH TUẦN HỌC LỚP 8A9</h1>
    <div style="text-align:center; color:white; font-size:32px;">THCS CẨM PHẢ - TUẤN HẠC</div>
    <div style="text-align:center; color:white; font-size:24px; margin-top:10px;">{activity['week_name']}</div>
  </div>

  <div class="content" style="display:grid; grid-template-columns:1fr 1fr; gap:30px; padding:30px;">

    <!-- CỘT TRÁI: THỜI KHÓA BIỂU -->
    <div class="schedule-box" style="background:white; border-radius:15px; padding:20px; box-shadow:0 8px 20px rgba(0,0,0,0.15); border:4px solid #4facfe;">
      <div class="title" style="background:linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); color:white; padding:15px; border-radius:10px; text-align:center; font-size:20px; font-weight:bold; margin-bottom:20px;">📅 THỜI KHÓA BIỂU</div>
      <table style="width:100%; border-collapse:collapse;">
        <tr style="background:#ffd89b; color:white;">
          <th style="border:2px solid #ddd; padding:10px;">Tiết</th>
          <th style="border:2px solid #ddd; padding:10px;">Môn học</th>
          <th style="border:2px solid #ddd; padding:10px;">Icon</th>
        </tr>
        {tkb_html}
      </table>
    </div>

    <!-- CỘT PHẢI: NHẬN XÉT -->
    <div class="eval-box" style="background:white; border-radius:15px; padding:20px; box-shadow:0 8px 20px rgba(0,0,0,0.15); border:4px solid #5ec793;">
      <div class="title" style="background:linear-gradient(135deg, #5ec793 0%, #3da66d 100%); color:white; padding:15px; border-radius:10px; text-align:center; font-size:20px; font-weight:bold; margin-bottom:20px;">📊 NHẬN XÉT SINH HOẠT LỚP TUẦN QUA</div>
      {nhan_xet_html}
    </div>
  </div>

  <!-- PHƯƠNG HƯỚNG TUẦN MỚI (Full width) -->
  <div style="padding:0 30px 30px 30px;">
    <div class="goals-box" style="background:white; border-radius:15px; padding:20px; box-shadow:0 8px 20px rgba(0,0,0,0.15); border:4px solid #f093fb;">
      <div class="title" style="background:linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color:white; padding:15px; border-radius:10px; text-align:center; font-size:24px; font-weight:bold; margin-bottom:20px;">🎯 PHƯƠNG HƯỚNG TUẦN MỚI</div>
      {phuong_huong_html}
    </div>
  </div>
</div>

<button onclick="downloadImage()" style="margin:20px auto; display:block; padding:15px 40px; font-size:18px; background:linear-gradient(135deg, #667eea 0%, #764ba2 100%); color:white; border:none; border-radius:50px; cursor:pointer; font-weight:bold; box-shadow:0 4px 15px rgba(0,0,0,0.2);">⬇️ TẢI XUỐNG ẢNH PNG</button>

<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script>
async function downloadImage() {{
    const element = document.getElementById('infographic');
    const canvas = await html2canvas(element, {{
        scale: 2,
        backgroundColor: '#e8d5c4',
        logging: false,
        useCORS: true
    }});
    const link = document.createElement('a');
    link.download = 'ke-hoach-tuan-hoc.png';
    link.href = canvas.toDataURL('image/png');
    link.click();
}}
</script>

STYLING CSS:
- .eval-row: display:flex; gap:15px; align-items:center; padding:12px; background:#f8f9fa; border-radius:10px; margin-bottom:10px;
- .eval-icon: font-size:32px;
- .eval-label: flex:1; font-weight:600; color:#333;
- .eval-content: display:flex; flex-direction:column; gap:5px;
- .eval-badge: background:linear-gradient(135deg, #ffd89b 0%, #ff9a56 100%); padding:5px 15px; border-radius:20px; color:white; font-weight:bold; align-self:flex-start;
- .goal-item: display:flex; gap:15px; align-items:center; padding:15px; background:#f8f9fa; border-radius:10px; margin-bottom:15px; box-shadow:0 2px 5px rgba(0,0,0,0.1);
- .goal-icon: font-size:32px;
- .goal-text: font-size:18px; font-weight:500;

CHỈ TRẢ VỀ CODE HTML HOÀN CHỈNH, KHÔNG GIẢI THÍCH."""

        # Gọi Gemini tạo HTML
        html_response = model.generate_content([html_prompt])
        html_content = clean_ai_output(html_response.text)

        # Loại bỏ markdown code blocks
        html_content = html_content.replace('```html', '').replace('```',
                                                                   '').strip()

        # Lưu file HTML
        infographic_dir = "static/class_activity_infographics"
        os.makedirs(infographic_dir, exist_ok=True)

        infographic_filename = f"{activity_id}_infographic.html"
        infographic_path = os.path.join(infographic_dir, infographic_filename)

        with open(infographic_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        activity[
            'infographic_html'] = f"/static/class_activity_infographics/{infographic_filename}"

        # ========================================
        # LƯU KẾT QUẢ
        # ========================================
        activity['ai_analysis'] = ai_analysis
        activity['analysis_data'] = analysis_data
        activity['status'] = 'analyzed'
        activity['analyzed_at'] = datetime.now().strftime("%d/%m/%Y %H:%M")

        for i, a in enumerate(activities):
            if a['id'] == activity_id:
                activities[i] = activity
                break

        save_class_activities(activities)

        flash('Đã phân tích và tạo infographic thành công!', 'success')

    except Exception as e:
        flash(f'Lỗi khi phân tích: {str(e)}', 'error')
        import traceback
        print(traceback.format_exc())

    return redirect(url_for('class_activity_result', activity_id=activity_id))


    #################
@app.route('/class_activity/<activity_id>/result')
def class_activity_result(activity_id):
    """Xem kết quả phân tích"""
    activities = load_class_activities()
    activity = next((a for a in activities if a['id'] == activity_id), None)

    if not activity:
        flash('Không tìm thấy phiên sinh hoạt!', 'error')
        return redirect(url_for('class_activity'))

    if activity['status'] != 'analyzed' or not activity.get('ai_analysis'):
        flash('Phiên này chưa được phân tích!', 'error')
        return redirect(
            url_for('class_activity_detail', activity_id=activity_id))

    return render_template('class_activity_result.html', activity=activity)


@app.route('/class_activity/<activity_id>/delete', methods=['POST'])
def delete_class_activity(activity_id):
    """Xóa phiên sinh hoạt"""
    activities = load_class_activities()
    activity = next((a for a in activities if a['id'] == activity_id), None)

    if activity:
        # Xóa các file ảnh
        for group_name, images in activity['groups'].items():
            for img_data in images:
                img_path = os.path.join(CLASS_ACTIVITY_IMAGES,
                                        img_data['filename'])
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                except:
                    pass

        # Xóa activity
        activities = [a for a in activities if a['id'] != activity_id]
        save_class_activities(activities)

        flash('Đã xóa phiên sinh hoạt!', 'success')

    return redirect(url_for('class_activity'))


###############
###
#
# Route cho chatbot
@app.route('/chatbot', methods=['GET', 'POST'])
def chatbot():
    if 'chat_history' not in session:
        session['chat_history'] = []

    response_text = None

    if request.method == 'POST':
        user_message = request.form.get('message', '').strip()
        uploaded_file = request.files.get('file')

        # Đọc dữ liệu từ data.txt
        knowledge_base = ""
        try:
            with open('data.txt', 'r', encoding='utf-8') as f:
                knowledge_base = f.read()
        except FileNotFoundError:
            knowledge_base = "Không tìm thấy file data.txt"

        # Xây dựng prompt chi tiết cho AI
        system_prompt = f"""Bạn là trợ lý AI thông minh hỗ trợ học sinh trong học tập.

KIẾN THỨC CƠ SỞ (từ data.txt):
{knowledge_base}

VAI TRÒ CỦA BẠN:
- Bạn là giáo viên/gia sư AI thân thiện, kiên nhẫn và nhiệt tình
- Hướng dẫn học sinh tự giải quyết vấn đề, phát triển tư duy độc lập
- Phân tích bài làm, hình ảnh bài tập học sinh gửi lên
- KHÔNG đưa ra đáp án trực tiếp - chỉ gợi ý và hướng dẫn cách giải

NGUYÊN TẮC QUAN TRỌNG:
1. KHI HỌC SINH HỎI BÀI (chưa làm):
   - TUYỆT ĐỐI KHÔNG đưa đáp án trực tiếp
   - TUYỆT ĐỐI KHÔNG giải chi tiết từng bước ra kết quả
   - CHỈ hướng dẫn phương pháp, công thức, định lý cần dùng
   - CHỈ gợi ý hướng tư duy, cách tiếp cận bài toán
   - Khuyến khích học sinh tự thực hiện các bước tính toán

2. KHI HỌC SINH GỬI ẢNH BÀI LÀM/ĐỀ TRẮC NGHIỆM:
   - Kiểm tra xem học sinh đã làm bài chưa (có khoanh/viết đáp án không)
   - NẾU ĐÃ LÀM (có đánh dấu/khoanh/ghi đáp án):
     * Chỉ ra câu nào đúng, câu nào sai
     * Giải thích tại sao sai và cách suy nghĩ đúng
     * Hướng dẫn cách cải thiện
   - NẾU CHƯA LÀM (đề trắng, chưa khoanh):
     * TUYỆT ĐỐI KHÔNG cho đáp án
     * CHỈ hướng dẫn kiến thức, phương pháp để giải từng câu
     * Gợi ý cách phân tích, loại trừ đáp án
     * Khuyến khích học sinh tự làm trước

CÁCH TRẢ LỜI:
1. Luôn trả lời bằng tiếng Việt
2. Với câu hỏi chưa làm:
   - "Để giải bài này, em cần biết công thức/định lý..."
   - "Hướng tiếp cận: Bước 1... Bước 2... Em thử làm xem"
   - "Gợi ý: Em hãy chú ý đến... và áp dụng..."

3. Với bài đã làm:
   - "Câu 1: Em làm đúng/sai. Giải thích:..."
   - "Câu 2: Đáp án của em là... nhưng đáp án đúng là... vì..."

4. Với văn/ngữ văn:
   - Gợi ý cách phân tích tác phẩm, nhân vật
   - Hướng dẫn cấu trúc bài văn
   - KHÔNG viết sẵn đoạn văn mẫu

QUY TẮC TRÌNH BÀY:
- KHÔNG dùng **, ***, ##, ###, ````
- Công thức toán viết văn bản thường: (x + 2)/(x - 3) hoặc x^2 + 3x + 2
- Xuống dòng rõ ràng giữa các ý
- Dùng số thứ tự 1. 2. 3. hoặc dấu gạch đầu dòng -
- Giữ văn phong thân thiện, động viên

LƯU Ý:
- Luôn khuyến khích học sinh: "Em hãy thử làm theo hướng dẫn này nhé!"
- Nếu học sinh yêu cầu đáp án trực tiếp, giải thích: "Thầy/cô sẽ hướng dẫn em cách làm để em tự rèn luyện tư duy nhé!"

Hãy ưu tiên sử dụng thông tin từ KIẾN THỨC CƠ SỞ khi trả lời các câu hỏi liên quan.
"""

        try:
            # Xử lý nếu có file đính kèm
            if uploaded_file and uploaded_file.filename != '':
                file_ext = uploaded_file.filename.rsplit('.', 1)[1].lower()

                # Lưu file tạm
                temp_filename = f"temp_{uuid.uuid4()}_{secure_filename(uploaded_file.filename)}"
                temp_path = os.path.join(app.config['UPLOAD_FOLDER'],
                                         temp_filename)
                uploaded_file.save(temp_path)

                # Xử lý theo loại file
                if file_ext == 'pdf':
                    # Đọc text từ PDF
                    pdf_text = extract_text_from_pdf(temp_path)
                    full_prompt = f"{system_prompt}\n\nHọc sinh gửi file PDF với nội dung:\n{pdf_text}\n\nCâu hỏi: {user_message if user_message else 'Hãy phân tích nội dung file này và hướng dẫn cách làm'}"
                    response = model.generate_content([full_prompt])
                    response_text = response.text

                elif file_ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']:
                    # Đọc ảnh
                    img = Image.open(temp_path)
                    full_prompt = f"{system_prompt}\n\nHọc sinh gửi ảnh bài tập/đề thi.\n\nQUAN TRỌNG: Hãy kiểm tra kỹ xem học sinh đã làm bài chưa (có đánh dấu, khoanh tròn, ghi đáp án không).\n- Nếu ĐÃ LÀM: Chấm bài, chỉ ra đúng/sai và giải thích.\n- Nếu CHƯA LÀM: CHỈ hướng dẫn phương pháp, KHÔNG cho đáp án.\n\nCâu hỏi thêm: {user_message if user_message else 'Hãy phân tích và hướng dẫn em'}"
                    response = model.generate_content([img, full_prompt])
                    response_text = response.text

                else:
                    response_text = "Định dạng file không được hỗ trợ. Chỉ chấp nhận ảnh (.png, .jpg, .jpeg) hoặc PDF."

                # Xóa file tạm
                try:
                    os.remove(temp_path)
                except:
                    pass

            else:
                # Chỉ có text message
                if user_message:
                    full_prompt = f"{system_prompt}\n\nHọc sinh hỏi: {user_message}\n\nLƯU Ý: Chỉ hướng dẫn phương pháp, không đưa đáp án trực tiếp."
                    response = model.generate_content([full_prompt])
                    response_text = response.text
                else:
                    response_text = "Vui lòng nhập câu hỏi hoặc gửi file."

            # Làm sạch output
            response_text = clean_ai_output(response_text)

            # Lưu vào lịch sử chat
            session['chat_history'].append({
                'user':
                user_message if user_message else '[Đã gửi file]',
                'bot':
                response_text,
                'timestamp':
                datetime.now().strftime("%H:%M")
            })
            session.modified = True

        except Exception as e:
            response_text = f"Lỗi: {str(e)}"

    return render_template('chatbot.html',
                           chat_history=session.get('chat_history', []),
                           response=response_text)


@app.route('/clear_chat', methods=['POST'])
def clear_chat():
    session['chat_history'] = []
    session.modified = True
    return redirect(url_for('chatbot'))


####
# Thêm vào file Flask


# Route đăng nhập cho chuyên gia
@app.route('/expert_login', methods=['GET', 'POST'])
def expert_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Đọc danh sách chuyên gia từ file
        try:
            with open('experts.json', 'r', encoding='utf-8') as f:
                experts = json.load(f)
        except FileNotFoundError:
            experts = []

        # Kiểm tra đăng nhập
        def expert_password_matches(stored_password, input_password):
            if stored_password.startswith(('pbkdf2:', 'scrypt:', 'bcrypt:')):
                return check_password_hash(stored_password, input_password)
            return stored_password == input_password

        expert = next(
            (e for e in experts
             if e['username'] == username and expert_password_matches(e['password'], password)), None)

        if expert:
            session['expert_logged_in'] = True
            session['expert_name'] = expert['name']
            session['expert_username'] = username
            session['expert_specialty'] = expert.get('specialty', 'Sức khỏe')
            flash('Đăng nhập thành công!', 'success')
            return redirect(url_for('health_support'))
        else:
            flash('Sai tên đăng nhập hoặc mật khẩu!', 'error')

    return render_template('expert_login.html')


@app.route('/expert_logout')
def expert_logout():
    session.pop('expert_logged_in', None)
    session.pop('expert_name', None)
    session.pop('expert_username', None)
    session.pop('expert_specialty', None)
    flash('Đã đăng xuất!', 'info')
    return redirect(url_for('health_support'))


def parse_ai_json_response(raw_text):
    """Parse JSON text returned by the AI, even when wrapped in markdown."""
    if not raw_text:
        raise ValueError("Empty AI response")

    cleaned = raw_text.strip()
    cleaned = re.sub(r'^```json\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^```\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    return json.loads(cleaned)


def fallback_health_triage(question_text):
    """Keyword-based fallback triage used when structured AI output is unavailable."""
    text = unicodedata.normalize('NFKD', question_text.lower())
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))

    critical_keywords = [
        'tu tu', 'muon chet', 'khong muon song', 'muon bien mat',
        'cuu voi', 'bat tinh', 'chay mau', 'tai nan nghiem trong',
        'kho tho', 'co giat', 'uong thuoc qua lieu', 'nhay lau'
    ]
    high_keywords = [
        'tram cam', 'tuyet vong', 'hoang loan', 'khung hoang',
        'bi danh', 'bao luc', 'xam hai', 'lam dung',
        'mat ngu nhieu ngay', 'khong on', 'suy sup'
    ]
    medium_keywords = [
        'stress', 'cang thang', 'lo au', 'buon', 'ap luc',
        'met moi', 'khoc', 'co don', 'bi bat nat', 'so hai'
    ]

    risk_level = 'low'
    if any(keyword in text for keyword in critical_keywords):
        risk_level = 'critical'
    elif any(keyword in text for keyword in high_keywords):
        risk_level = 'high'
    elif any(keyword in text for keyword in medium_keywords):
        risk_level = 'medium'

    needs_escalation = risk_level in {'high', 'critical'}
    summary_map = {
        'low': 'Ca tu van thong thuong, chua thay dau hieu nguy co cao.',
        'medium': 'Hoc sinh co dau hieu can theo doi va dong vien som.',
        'high': 'Co dau hieu bat on tam ly ro, can GVCN/chuyen gia tiep nhan som.',
        'critical': 'Tinh huong co the khan cap, can kich hoat can thiep som ngay.'
    }
    note_map = {
        'low': 'AI fallback khong ghi nhan tu khoa nguy co cao trong noi dung.',
        'medium': 'AI fallback ghi nhan mot so dau hieu cang thang/tam ly.',
        'high': 'AI fallback ghi nhan tu khoa nguy co cao nen de xuat chuyen tuyen.',
        'critical': 'AI fallback ghi nhan tu khoa khan cap nen kich hoat canh bao ngay.'
    }

    if needs_escalation:
        student_notice = (
            'He thong da nhan dien day la ca can ho tro sau hon va '
            'da chuyen canh bao an danh toi GVCN/chuyen gia de ho tro ban som.'
        )
    else:
        student_notice = 'AI da tiep nhan va ho tro ban theo luong tu van thong thuong.'

    return {
        'risk_level': risk_level,
        'needs_escalation': needs_escalation,
        'escalation_target': 'gvcn_expert' if needs_escalation else None,
        'alert_summary': summary_map[risk_level],
        'ai_triage_note': note_map[risk_level],
        'student_notice': student_notice
    }


def triage_health_question(question_text):
    """Use AI to classify risk level and trigger the demo escalation flow."""
    fallback_result = fallback_health_triage(question_text)
    prompt = f"""You are a school safety triage system.

Task:
- Read the student's message.
- Classify risk_level as one of: low, medium, high, critical.
- Set needs_escalation=true for serious mental distress, self-harm, violence, abuse, or severe accident cases.
- escalation_target must be null or "gvcn_expert".

Return ONLY valid JSON with these keys:
{{
  "risk_level": "low|medium|high|critical",
  "needs_escalation": true,
  "escalation_target": "gvcn_expert",
  "alert_summary": "short one-sentence summary",
  "ai_triage_note": "1-2 sentence reason",
  "student_notice": "short reassuring notice saying the system connected the anonymous alert to homeroom teacher/expert when needed"
}}

Student message:
{question_text}
"""

    try:
        response = model.generate_content([prompt])
        triage_result = parse_ai_json_response(response.text)
    except Exception:
        triage_result = fallback_result

    risk_level = str(triage_result.get('risk_level', 'low')).strip().lower()
    if risk_level not in {'low', 'medium', 'high', 'critical'}:
        risk_level = fallback_result['risk_level']

    needs_escalation = bool(triage_result.get('needs_escalation', False))
    if risk_level in {'high', 'critical'}:
        needs_escalation = True

    alert_summary = str(triage_result.get('alert_summary', '')).strip()
    ai_triage_note = str(triage_result.get('ai_triage_note', '')).strip()
    student_notice = str(triage_result.get('student_notice', '')).strip()

    return {
        'risk_level': risk_level,
        'needs_escalation': needs_escalation,
        'escalation_target': 'gvcn_expert' if needs_escalation else None,
        'alert_summary': alert_summary or fallback_result['alert_summary'],
        'ai_triage_note': ai_triage_note or fallback_result['ai_triage_note'],
        'student_notice': student_notice or fallback_result['student_notice']
    }


def build_escalation_support_response(triage_result):
    """Short, safe message shown while the case is being escalated."""
    if triage_result.get('risk_level') == 'critical':
        return (
            "Minh nhan thay day co the la tinh huong khan cap. "
            "Ban hay tim den ngay mot nguoi lon dang tin cay, GVCN, "
            "phu huynh hoac nhan vien y te gan nhat. He thong da kich hoat "
            "ket noi an danh de GVCN/chuyen gia co the ho tro ban som."
        )

    return (
        "He thong nhan thay ban co the dang can ho tro sau hon. "
        "Minh da kich hoat ket noi an danh den GVCN/chuyen gia de "
        "ban duoc ho tro som. Trong luc cho, neu ban cam thay qua tai "
        "hoac khong an toan, hay tim ngay mot nguoi lon dang tin cay o gan ban."
    )


# Route trang tư vấn sức khỏe
@app.route('/health_support', methods=['GET', 'POST'])
def health_support():
    # Load câu hỏi từ file
    try:
        with open('health_questions.json', 'r', encoding='utf-8') as f:
            questions = json.load(f)
    except FileNotFoundError:
        questions = []

    ai_response = None

    if request.method == 'POST':
        student_name = request.form.get('student_name', '').strip()
        question = request.form.get('question', '').strip()
        consult_type = request.form.get('consult_type')  # 'ai' hoặc 'expert'
        is_anonymous = request.form.get(
            'is_anonymous') == 'on'  # Checkbox ẩn danh

        if not student_name or not question:
            flash('Vui lòng nhập đầy đủ thông tin!', 'error')
            return redirect(url_for('health_support'))

        question_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")
        triage_result = triage_health_question(question)
        needs_escalation = triage_result['needs_escalation']

        if needs_escalation:
            is_anonymous = True

        new_question = {
            'id': question_id,
            'student_name': student_name,
            'question': question,
            'consult_type': consult_type,
            'timestamp': timestamp,
            'ai_response': None,
            'expert_responses': [],
            'status': 'pending',  # pending, answered
            'risk_level': triage_result['risk_level'],
            'needs_escalation': needs_escalation,
            'escalation_target': triage_result['escalation_target'],
            'handling_status': 'new' if needs_escalation else None,
            'alert_summary': triage_result['alert_summary'],
            'ai_triage_note': triage_result['ai_triage_note'],
            'student_notice': triage_result['student_notice'],
            'is_anonymous': is_anonymous  # Thêm trường ẩn danh
        }

        # Nếu chọn AI tư vấn
        if needs_escalation:
            new_question['ai_response'] = build_escalation_support_response(
                triage_result)
        elif consult_type == 'ai':
            try:
                # Đọc kiến thức về sức khỏe
                health_knowledge = ""
                try:
                    with open('health_data.txt', 'r', encoding='utf-8') as f:
                        health_knowledge = f.read()
                except FileNotFoundError:
                    health_knowledge = "Không có dữ liệu sức khỏe."

                prompt = f"""Bạn là chuyên gia tư vấn sức khỏe cho học sinh.

KIẾN THỨC VỀ SỨC KHỎE:
{health_knowledge}

VAI TRÒ:
- Tư vấn các vấn đề sức khỏe phổ biến ở học sinh
- Tâm lý học đường, stress, lo âu
- Dinh dưỡng, vận động, giấc ngủ
- Sức khỏe sinh sản (phù hợp lứa tuổi)

QUY TẮC:
1. Trả lời bằng tiếng Việt, thân thiện, dễ hiểu
2. Không thay thế bác sĩ - khuyên gặp bác sĩ nếu nghiêm trọng
3. Đưa lời khuyên phù hợp lứa tuổi học sinh
4. Tôn trọng, không phán xét
5. KHÔNG dùng **, ##, ````

Học sinh hỏi: {question}

Hãy tư vấn chi tiết, có lời khuyên cụ thể."""

                response = model.generate_content([prompt])
                ai_response = clean_ai_output(response.text)
                new_question['ai_response'] = ai_response
                new_question['status'] = 'answered'

            except Exception as e:
                ai_response = f"❌ Lỗi: {str(e)}"
                new_question['ai_response'] = ai_response

        # Lưu câu hỏi
        questions.insert(0, new_question)  # Thêm vào đầu danh sách

        # Giữ tối đa 100 câu hỏi
        if len(questions) > 100:
            questions = questions[:100]

        with open('health_questions.json', 'w', encoding='utf-8') as f:
            json.dump(questions, f, ensure_ascii=False, indent=2)

        flash('Câu hỏi đã được gửi!', 'success')
        if needs_escalation:
            flash(triage_result['student_notice'], 'info')
        return redirect(url_for('health_support'))

    # Kiểm tra xem user có phải chuyên gia không
    is_expert = session.get('expert_logged_in', False)

    # Lọc câu hỏi hiển thị theo quyền
    display_questions = []
    for q in questions:
        q.setdefault('risk_level', 'low')
        q.setdefault('needs_escalation', False)
        q.setdefault('escalation_target', None)
        q.setdefault('handling_status', 'new' if q.get('needs_escalation')
                     else None)
        q.setdefault('alert_summary', None)
        q.setdefault('ai_triage_note', None)
        q.setdefault('student_notice', None)

        if q.get('needs_escalation') and not is_expert:
            continue

        if q.get('is_anonymous', False):
            # Nếu câu hỏi ẩn danh
            if is_expert:
                # Chuyên gia thấy đầy đủ
                display_questions.append(q)
            else:
                # Người khác chỉ thấy câu hỏi đã được trả lời và ẩn thông tin
                if q['status'] == 'answered' and (q.get('ai_response') or
                                                  q.get('expert_responses')):
                    hidden_q = q.copy()
                    hidden_q['student_name'] = 'Ẩn danh'
                    hidden_q[
                        'question'] = '[Câu hỏi riêng tư - chỉ chuyên gia xem được]'
                    display_questions.append(hidden_q)
        else:
            # Câu hỏi công khai - tất cả đều thấy
            display_questions.append(q)

    if is_expert:
        risk_priority = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        display_questions.sort(
            key=lambda q: (
                0 if q.get('needs_escalation') else 1,
                risk_priority.get(q.get('risk_level', 'low'), 3)))

    return render_template('health_support.html',
                           questions=display_questions,
                           is_expert=is_expert,
                           expert_name=session.get('expert_name'))


#######################


# Route chuyên gia trả lời
@app.route('/expert_answer/<question_id>', methods=['POST'])
def expert_answer(question_id):
    if not session.get('expert_logged_in'):
        flash('Bạn cần đăng nhập với tư cách chuyên gia!', 'error')
        return redirect(url_for('expert_login'))

    answer = request.form.get('answer', '').strip()

    if not answer:
        flash('Vui lòng nhập câu trả lời!', 'error')
        return redirect(url_for('health_support'))

    try:
        with open('health_questions.json', 'r', encoding='utf-8') as f:
            questions = json.load(f)
    except FileNotFoundError:
        questions = []

    # Tìm câu hỏi
    question = next((q for q in questions if q['id'] == question_id), None)

    if question:
        expert_response = {
            'expert_name': session.get('expert_name'),
            'specialty': session.get('expert_specialty', 'Sức khỏe'),
            'answer': answer,
            'timestamp': datetime.now().strftime("%d/%m/%Y %H:%M")
        }

        question['expert_responses'].append(expert_response)
        question['status'] = 'answered'
        if question.get('needs_escalation'):
            question['handling_status'] = 'contacted'

        with open('health_questions.json', 'w', encoding='utf-8') as f:
            json.dump(questions, f, ensure_ascii=False, indent=2)

        flash('Đã gửi câu trả lời!', 'success')
    else:
        flash('Không tìm thấy câu hỏi!', 'error')

    return redirect(url_for('health_support'))


@app.route('/health_case_status/<question_id>', methods=['POST'])
def health_case_status(question_id):
    if not session.get('expert_logged_in'):
        flash('Ban can dang nhap voi tu cach GVCN/chuyen gia!', 'error')
        return redirect(url_for('expert_login'))

    new_status = request.form.get('handling_status', '').strip().lower()
    allowed_statuses = {
        'new': 'Moi tiep nhan',
        'contacted': 'Da tiep nhan',
        'monitoring': 'Dang theo doi',
        'closed': 'Da dong ca'
    }

    if new_status not in allowed_statuses:
        flash('Trang thai xu ly khong hop le!', 'error')
        return redirect(url_for('health_support'))

    try:
        with open('health_questions.json', 'r', encoding='utf-8') as f:
            questions = json.load(f)
    except FileNotFoundError:
        questions = []

    question = next((q for q in questions if q['id'] == question_id), None)

    if not question:
        flash('Khong tim thay ca canh bao!', 'error')
        return redirect(url_for('health_support'))

    question['handling_status'] = new_status
    if new_status == 'closed':
        question['status'] = 'answered'

    with open('health_questions.json', 'w', encoding='utf-8') as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

    flash(f"Da cap nhat trang thai: {allowed_statuses[new_status]}", 'success')
    return redirect(url_for('health_support'))


#####


def generate_feedback(text):
    """Tạo feedback từ text bằng AI"""
    try:
        prompt = f"Đây là nội dung bài làm của học sinh:\n\n{text}\n\nHãy phân tích, chỉ ra lỗi sai và đề xuất cải thiện. Trả lời bằng tiếng Việt."
        response = model.generate_content([prompt])
        return response.text
    except Exception as e:
        return f"❌ Lỗi khi tạo feedback: {str(e)}"


def generate_score_feedback(text):
    """Tạo feedback chấm điểm từ text bằng AI"""
    try:
        prompt = f"""Dựa trên bài làm của học sinh sau:

{text}

Hãy chấm điểm theo các tiêu chí sau:
1. Nội dung đầy đủ (0–10)
2. Trình bày rõ ràng (0–10)
3. Kỹ thuật chính xác (0–10)
4. Thái độ học tập (0–10)

Sau đó, tổng kết điểm trung bình và đưa ra nhận xét ngắn gọn. Trả lời bằng tiếng Việt."""
        response = model.generate_content([prompt])
        return response.text
    except Exception as e:
        return f"❌ Lỗi khi chấm điểm: {str(e)}"


def extract_average_from_feedback(feedback: str):
    """
    Thử tìm số điểm trung bình trong chuỗi feedback của AI.
    Ví dụ: 'Tổng điểm trung bình: 8.5' -> 8.5
    Nếu không tìm thấy thì trả về None.
    """
    if not feedback:
        return None
    match = re.search(r'(\d+(\.\d+)?)', feedback)
    if match:
        try:
            return float(match.group(1))
        except:
            return None
    return None


###########


###
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/enter_nickname")
def enter_nickname():
    return render_template("nickname.html")


@app.route("/start_game", methods=["POST"])
def start_game():
    nickname = request.form["nickname"]
    bai = request.form["bai"]
    session["nickname"] = nickname
    session["bai"] = bai
    return redirect("/game")


@app.route("/game")
def game():
    if "nickname" not in session or "bai" not in session:
        return redirect("/enter_nickname")
    return render_template("game.html")


@app.route("/get_questions")
def get_questions():
    bai = session.get("bai", "bai_1")
    with open("questions.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    questions = data.get(bai, [])
    random.shuffle(questions)
    for q in questions:
        random.shuffle(q["options"])
    return jsonify(questions[:20])


@app.route("/submit_score", methods=["POST"])
def submit_score():
    nickname = session.get("nickname")
    bai = session.get("bai")
    score = request.json["score"]

    if not nickname:
        return jsonify({"status": "error", "message": "No nickname found"})
    if not bai:
        return jsonify({"status": "error", "message": "No bai found"})

    if not os.path.exists("scores.json"):
        with open("scores.json", "w", encoding="utf-8") as f:
            json.dump([], f)

    with open("scores.json", "r+", encoding="utf-8") as f:
        scores = json.load(f)
        now = datetime.now().strftime("%d/%m/%Y %H:%M")

        existing = next((s for s in scores
                         if s["nickname"] == nickname and s.get("bai") == bai),
                        None)

        if existing:
            if score > existing["score"]:
                existing["score"] = score
                existing["time"] = now
        else:
            scores.append({
                "nickname": nickname,
                "score": score,
                "time": now,
                "bai": bai
            })

        filtered = [s for s in scores if s.get("bai") == bai]
        top50 = sorted(filtered, key=lambda x: x["score"], reverse=True)[:50]

        others = [s for s in scores if s.get("bai") != bai]
        final_scores = others + top50

        f.seek(0)
        json.dump(final_scores, f, ensure_ascii=False, indent=2)
        f.truncate()

    return jsonify({"status": "ok"})


@app.route("/leaderboard")
def leaderboard():
    bai = session.get("bai")

    if not bai:
        bai = "bai_1"

    if not os.path.exists("scores.json"):
        top5 = []
    else:
        with open("scores.json", "r", encoding="utf-8") as f:
            scores = json.load(f)

        filtered = [s for s in scores if s.get("bai") == bai]
        top5 = sorted(filtered, key=lambda x: x["score"], reverse=True)[:5]

    return render_template("leaderboard.html", players=top5, bai=bai)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/enter_nickname")


# Đường dẫn file dữ liệu
DATA_FOLDER = 'data'
EXAM_FILE = os.path.join(DATA_FOLDER, 'exam_data.json')
PROJECTS_FILE = os.path.join(DATA_FOLDER, 'projects.json')
PROJECT_IMAGES_FILE = os.path.join(DATA_FOLDER, 'project_images.json')
GENERAL_IMAGES_FILE = os.path.join(DATA_FOLDER, 'data.json')


def load_exam(de_id):
    with open(EXAM_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get(de_id)


def load_projects():
    with open(PROJECTS_FILE, 'r', encoding='utf-8') as f:
        projects = json.load(f)

    if not any(p["id"] == "general" for p in projects):
        projects.append({
            "id": "general",
            "title": "Bài tập nhóm",
            "description": "Các nhóm làm bài và nộp tại đây."
        })

    return projects


def load_project_images():
    try:
        with open(PROJECT_IMAGES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}


def save_project_images(data):
    with open(PROJECT_IMAGES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_general_images():
    try:
        with open(GENERAL_IMAGES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []


def save_general_images(data):
    with open(GENERAL_IMAGES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route('/exam/<de_id>')
def exam(de_id):
    questions = load_exam(de_id)
    if not questions:
        return "Không tìm thấy đề thi."
    return render_template('exam.html', questions=questions, de_id=de_id)


@app.route('/projects')
def projects():
    project_list = load_projects()
    return render_template('projects.html', projects=project_list)


@app.route('/submit/<de_id>', methods=['GET', 'POST'])
def submit(de_id):
    if request.method != 'POST':
        return redirect(url_for('exam', de_id=de_id))

    questions = load_exam(de_id)
    if not questions:
        return "Không tìm thấy đề thi."

    correct_count = 0
    total_questions = 0
    feedback = []
    results = []

    for i, q in enumerate(questions.get("multiple_choice", [])):
        user_answer = request.form.get(f"mc_{i}")
        correct = q["answer"]
        total_questions += 1
        if user_answer and user_answer.strip().lower() == correct.strip(
        ).lower():
            correct_count += 1
            results.append({"status": "Đúng", "note": ""})
        else:
            msg = f"Câu {i+1} sai. Đáp án đúng là: {correct}"
            results.append({"status": "Sai", "note": msg})
            feedback.append(msg)

    for i, tf in enumerate(questions.get("true_false", [])):
        for j, correct_tf in enumerate(tf["answers"]):
            user_tf_raw = request.form.get(f"tf_{i}_{j}", "").lower()
            user_tf = user_tf_raw == "true"
            total_questions += 1
            if user_tf == correct_tf:
                correct_count += 1
                results.append({"status": "Đúng", "note": ""})
            else:
                msg = f"Câu {i+1+len(questions['multiple_choice'])}, ý {j+1} sai."
                results.append({"status": "Sai", "note": msg})
                feedback.append(msg)

    detailed_errors = "\n".join(feedback)

    prompt = f"""Học sinh làm đúng {correct_count} / {total_questions} câu.

Danh sách lỗi:
{detailed_errors}

Bạn là giáo viên Toán. Hãy:
1. Nhận xét tổng thể về kết quả (giọng văn tích cực, khích lệ)
2. Phân tích từng lỗi sai: giải thích lý do sai, kiến thức liên quan, cách sửa
3. Đề xuất ít nhất 3 dạng bài tập cụ thể để luyện tập
4. Chấm điểm trên thang 10

QUY TẮC TRÌNH BÀY:
- Công thức toán dùng LaTeX:
  + Inline (trong dòng): $x^2 + 3x + 2$
  + Hiển thị riêng: $$\\sqrt{{x-3}} \\geq 0$$
- Các ký hiệu LaTeX:
  + Căn: \\sqrt{{x}}
  + Phân số: \\frac{{a}}{{b}}
  + Lớn hơn/bằng: \\geq
  + Nhỏ hơn/bằng: \\leq
  + Nhân: \\times
  + Pi: \\pi
- KHÔNG dùng **, ##, ###, ```
- Xuống dòng rõ ràng giữa các ý
- Dùng 1. 2. 3. hoặc dấu gạch đầu dòng -

VÍ DỤ TRÌNH BÀY ĐÚNG:

Câu 3 sai. Đáp án đúng: $x \\geq 3$

Giải thích: Căn thức $\\sqrt{{x-3}}$ xác định khi biểu thức trong căn không âm, tức là:
$$x - 3 \\geq 0$$
$$x \\geq 3$$

Câu 4 sai. Đáp án đúng: $\\frac{{3}}{{2}}$

Phương trình $2x^2 - 3x - 5 = 0$ có:
- $\\Delta = b^2 - 4ac = 9 + 40 = 49$
- Tổng 2 nghiệm: $x_1 + x_2 = -\\frac{{b}}{{a}} = \\frac{{3}}{{2}}$

Trả lời bằng tiếng Việt, thân thiện."""

    try:
        response = model.generate_content([prompt])
        # KHÔNG dùng clean_ai_output vì cần giữ nguyên LaTeX
        ai_feedback = response.text
    except Exception as e:
        ai_feedback = f"❌ Lỗi: {str(e)}"

    return render_template('result.html',
                           score=correct_count,
                           feedback=feedback,
                           ai_feedback=ai_feedback,
                           total_questions=total_questions,
                           results=results)


@app.route('/project/<project_id>', methods=['GET', 'POST'])
def project(project_id):
    projects = load_projects()
    project_info = next((p for p in projects if p["id"] == project_id), None)
    if not project_info:
        return "Không tìm thấy đề bài."

    all_images = load_project_images()
    images = all_images.get(project_id, [])
    ai_feedback = None

    if request.method == 'POST':
        image = request.files.get('image')
        group_name = request.form.get('group_name')
        note = request.form.get('note', '').strip()

        if not image or image.filename == '' or not group_name:
            return render_template('project.html',
                                   project=project_info,
                                   images=images,
                                   feedback="❌ Thiếu ảnh hoặc tên nhóm.")

        image_id = str(uuid.uuid4())
        filename = f"{image_id}_{image.filename}"
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(image_path)

        try:
            img = Image.open(image_path)
            prompt = (
                f"Đây là ảnh bài làm của học sinh. "
                f"Hãy phân tích nội dung, chỉ ra lỗi sai nếu có, và đề xuất cải thiện, chấm bài làm trên thang 10."
            )
            response = model.generate_content([img, prompt])
            ai_feedback = response.text
        except Exception as e:
            ai_feedback = f"❌ Lỗi khi xử lý ảnh: {str(e)}"

        new_image = {
            "id": image_id,
            "filename": filename,
            "group_name": group_name,
            "note": note,
            "ai_feedback": ai_feedback,
            "comments": []
        }
        images.append(new_image)
        all_images[project_id] = images
        save_project_images(all_images)

    return render_template('project.html',
                           project=project_info,
                           images=images,
                           feedback=ai_feedback)


@app.route('/comment/<project_id>/<image_id>', methods=['POST'])
def comment(project_id, image_id):
    student_name = request.form.get('student_name', '').strip()
    comment_text = request.form.get('comment_text', '').strip()
    score = request.form.get('score', '').strip()

    if not student_name or not comment_text or not score:
        flash("Vui lòng nhập đầy đủ tên, bình luận và điểm số.")
        return redirect(url_for('project', project_id=project_id))

    try:
        score = float(score)
        if score < 0 or score > 10:
            flash("Điểm phải nằm trong khoảng 0 - 10.")
            return redirect(url_for('project', project_id=project_id))
    except ValueError:
        flash("Điểm phải là số hợp lệ.")
        return redirect(url_for('project', project_id=project_id))

    all_images = load_project_images()
    images = all_images.get(project_id)

    if images is None:
        flash("Đề bài không tồn tại.")
        return redirect(url_for('home'))

    target_image = next((img for img in images if img.get("id") == image_id),
                        None)

    if target_image is None:
        flash("Không tìm thấy ảnh để bình luận.")
        return redirect(url_for('project', project_id=project_id))

    for c in target_image.get("comments", []):
        if (c["student_name"] == student_name
                and c["comment_text"] == comment_text
                and c.get("score") == score):
            flash("Bình luận đã tồn tại.")
            return redirect(url_for('project', project_id=project_id))

    target_image.setdefault("comments", []).append({
        "student_name": student_name,
        "comment_text": comment_text,
        "score": score
    })

    scores = [
        c["score"] for c in target_image.get("comments", []) if "score" in c
    ]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0
    target_image["average_score"] = avg_score

    all_images[project_id] = images
    save_project_images(all_images)

    flash(f"Bình luận đã được thêm. Điểm trung bình hiện tại: {avg_score}")
    return redirect(url_for('project', project_id=project_id))


@app.route('/upload_image', methods=['GET', 'POST'])
def upload_image():
    ai_feedback = None
    score_feedback = None
    all_images = load_project_images()
    images = all_images.get("general", [])

    if request.method == 'POST':
        uploaded_file = request.files.get('image')
        group_name = request.form.get('group_name')

        if not uploaded_file or uploaded_file.filename == '' or not group_name:
            return render_template('upload_image.html',
                                   feedback="❌ Thiếu file hoặc tên nhóm.",
                                   images=images)

        if not allowed_file(uploaded_file.filename):
            return render_template(
                'upload_image.html',
                feedback="❌ File không hợp lệ. Chỉ chấp nhận ảnh hoặc PDF.",
                images=images)

        file_ext = uploaded_file.filename.rsplit('.', 1)[1].lower()
        file_id = str(uuid.uuid4())
        filename = f"{file_id}_{uploaded_file.filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        uploaded_file.save(file_path)

        try:
            if file_ext == 'pdf':
                text = extract_text_from_pdf(file_path)
                if not text.strip():
                    ai_feedback = "❌ Không tìm thấy nội dung trong file PDF."
                    score_feedback = ""
                else:
                    ai_feedback = generate_feedback(text)
                    score_feedback = generate_score_feedback(text)

            elif file_ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']:
                img = Image.open(file_path)

                # ===== PROMPT CẢI THIỆN CHO PHẢN HỒI AI =====
                ai_response = model.generate_content([
                    img,
                    """Bạn là giáo viên đang chấm bài học sinh. Hãy phân tích bài làm trong ảnh và đưa ra nhận xét chi tiết.

NHIỆM VỤ:
1. Mô tả ngắn gọn nội dung bài làm
2. Chỉ ra các điểm làm đúng (nếu có)
3. Chỉ ra các lỗi sai cụ thể (nếu có)
4. Đề xuất cách cải thiện

QUY TẮC TRÌNH BÀY QUAN TRỌNG:
• TUYỆT ĐỐI KHÔNG dùng: **, ***, ##, ###, ````
• Công thức toán viết văn bản thường, ví dụ: (3x + 6)/(4x - 8) hoặc x^2 + 2x + 1
• Mỗi ý PHẢI xuống dòng rõ ràng
• Dùng dấu đầu dòng đơn giản: - hoặc số thứ tự 1. 2. 3.
• Không viết quá dài, mỗi đoạn tối đa 3-4 dòng

VÍ DỤ TRÌNH BÀY ĐÚNG:

Nội dung bài làm:
Học sinh đã giải phương trình (x + 2)(x - 3) = 0

Điểm tốt:
- Nhận diện đúng dạng phương trình tích
- Áp dụng đúng quy tắc tích bằng 0

Lỗi sai:
- Bước 2: Viết x + 2 = 0 hoặc x - 3 = 0 (thiếu chữ "hoặc")
- Kết luận thiếu tập nghiệm S = {-2; 3}

Đề xuất cải thiện:
Cần ghi rõ "hoặc" khi tách nhân tử. Luôn viết tập nghiệm ở cuối.

Trả lời bằng tiếng Việt, ngắn gọn, dễ hiểu."""
                ])
                ai_feedback = clean_ai_output(ai_response.text)

                # ===== PROMPT CẢI THIỆN CHO CHẤM ĐIỂM =====
                score_response = model.generate_content([
                    img,
                    """Hãy chấm điểm bài làm của học sinh theo 4 tiêu chí sau:

TIÊU CHÍ CHẤM ĐIỂM:
1. Nội dung (0-10): Độ đầy đủ, đúng đắn của bài làm
2. Trình bày (0-10): Sạch sẽ, rõ ràng, dễ đọc
3. Phương pháp (0-10): Cách giải, logic tư duy
4. Kết quả (0-10): Đáp án cuối cùng có chính xác không

QUY TẮC TRÌNH BÀY:
• KHÔNG dùng **, ***, ##, ###, ````
• Mỗi tiêu chí ghi trên 1 dòng riêng
• Format: Tên tiêu chí: X/10 - Lý do ngắn gọn
• Cuối cùng ghi điểm trung bình và nhận xét chung

VÍ DỤ TRÌNH BÀY ĐÚNG:

Nội dung: 8/10 - Làm đầy đủ các bước, có một chỗ thiếu
Trình bày: 7/10 - Khá rõ ràng nhưng chữ hơi nhỏ
Phương pháp: 9/10 - Áp dụng đúng công thức và logic tốt
Kết quả: 6/10 - Đáp án sai do nhầm dấu ở bước cuối

Điểm trung bình: 7.5/10

Nhận xét chung:
Bài làm khá tốt, phương pháp đúng. Cần cẩn thận hơn ở bước tính toán cuối cùng để tránh sai số.

Trả lời bằng tiếng Việt."""
                ])
                score_feedback = clean_ai_output(score_response.text)

            else:
                ai_feedback = "❌ Định dạng file không hỗ trợ."
                score_feedback = ""

        except Exception as e:
            ai_feedback = f"❌ Lỗi khi xử lý file: {str(e)}"
            score_feedback = ""

        ai_score = extract_average_from_feedback(score_feedback)

        new_image = {
            "id": file_id,
            "filename": filename,
            "group_name": group_name,
            "file_type": file_ext,
            "ai_feedback": ai_feedback,
            "score_feedback": score_feedback,
            "comments": [],
            "scores": [],
            "average_score": None
        }

        if ai_score is not None:
            new_image["scores"].append(ai_score)
            new_image["average_score"] = ai_score

        images.append(new_image)

        all_images["general"] = images
        save_project_images(all_images)

    for img in images:
        if "scores" in img and img["scores"]:
            avg = sum(img["scores"]) / len(img["scores"])
            img["average_score"] = round(avg, 2)
        else:
            img["average_score"] = None

    return render_template('upload_image.html',
                           feedback=ai_feedback,
                           score=score_feedback,
                           images=images)


# ===== HÀM HỖ TRỢ LÀM SẠCH OUTPUT CỦA AI =====
def clean_ai_output(text):
    """
    Làm sạch output của AI để hiển thị đẹp hơn
    """
    import re

    # Loại bỏ các dấu markdown không mong muốn
    text = re.sub(r'\*\*\*', '', text)  # Loại bỏ ***
    text = re.sub(r'\*\*', '', text)  # Loại bỏ **
    text = re.sub(r'#{1,6}\s', '', text)  # Loại bỏ ##, ###

    # Loại bỏ code blocks
    text = re.sub(r'```[a-z]*\n', '', text)
    text = re.sub(r'```', '', text)

    # Chuẩn hóa xuống dòng (loại bỏ xuống dòng thừa)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Loại bỏ khoảng trắng thừa đầu/cuối dòng
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)

    return text.strip()

####
@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint đơn giản để ping service giữ server thức"""
    return jsonify({
        "status": "ok",
        "message": "Server is running",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    }), 200

# Hoặc đơn giản hơn:
@app.route('/ping')
def ping():
    return "pong", 200
###
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
