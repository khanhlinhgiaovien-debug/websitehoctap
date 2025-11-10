from flask import Flask, render_template, request, redirect, url_for
import json, os, re
from PIL import Image
import google.generativeai as genai
import uuid
from datetime import datetime
from flask import session
import random
from flask import jsonify
import fitz  # PyMuPDF
from flask import flash

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Cấu hình thư mục upload
UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.environ["GOOGLE_API_KEY"] = "AIzaSyDyucgFgQgemWGcXmAXY4i-euwHikvGKyI"
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
model = genai.GenerativeModel("models/gemini-2.0-flash")

# Định nghĩa các extension được phép
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
@app.route('/vanbai', methods=['GET', 'POST'])
def vanbai():
    if request.method == 'GET':
        return render_template('vanbai_form.html')

    essay = request.form.get("essay", "").strip()
    if not essay:
        return "Vui lòng nhập bài văn."

    if len(essay) > 1900:
        return "Bài văn vượt quá giới hạn 600 chữ. Vui lòng rút gọn."

    prompt = (
        f"Học sinh gửi bài văn sau:\n\n{essay}\n\n"
        "Bạn là giáo viên môn Ngữ văn. Hãy:\n"
        "1. Phân tích điểm mạnh và điểm yếu của bài viết.\n"
        "2. Nhận xét về cách hành văn, lập luận, cảm xúc, và ngôn ngữ.\n"
        "3. Đưa ra lời khuyên để cải thiện bài viết.\n"
        "4. Đánh giá xem bài viết có dấu hiệu được tạo bởi AI hay không (dựa vào phong cách, độ tự nhiên, tính cá nhân).\n"
        "Trình bày rõ ràng, dễ hiểu, giọng văn thân thiện."
    )

    try:
        response = model.generate_content([prompt])
        ai_feedback = response.text
    except Exception as e:
        ai_feedback = f"❌ Lỗi khi gọi Gemini: {str(e)}"

    return render_template(
        'vanbai_result.html',
        essay=essay,
        ai_feedback=ai_feedback
    )

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

        existing = next((s for s in scores if s["nickname"] == nickname and s.get("bai") == bai), None)

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
            "title": "Bài làm không phân loại",
            "description": "Dành cho các bài làm không gắn với đề cụ thể."
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
        if user_answer and user_answer.strip().lower() == correct.strip().lower():
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

    score = correct_count
    summary = f"Học sinh làm đúng {correct_count} / {total_questions} câu."
    detailed_errors = "\n".join(feedback)

    prompt = (
        f"{summary}\n\n"
        "Dưới đây là danh sách các lỗi học sinh đã mắc phải trong bài làm:\n"
        + detailed_errors + "\n\n"
        "Bạn là giáo viên môn Toán. Hãy viết một phản hồi dành cho học sinh, gồm các phần sau:\n"
        "1. Nhận xét tổng thể về kết quả bài làm (giọng văn tích cực, khích lệ).\n"
        "2. Phân tích từng lỗi sai đã nêu: giải thích lý do sai, kiến thức liên quan, và cách sửa.\n"
        "3. Đề xuất ít nhất 3 dạng bài tập cụ thể để học sinh luyện tập đúng phần bị sai.\n"
        "Trình bày rõ ràng, dễ hiểu, thân thiện như một giáo viên đang trò chuyện với học sinh."
        "4. Hãy chấm điểm trên thang 10"
    )

    try:
        response = model.generate_content([prompt])
        ai_feedback = response.text
    except Exception as e:
        ai_feedback = f"❌ Lỗi khi gọi AI: {str(e)}"

    return render_template(
        'result.html',
        score=score,
        feedback=feedback,
        ai_feedback=ai_feedback,
        total_questions=total_questions,
        results=results
    )

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
            return render_template(
                'project.html',
                project=project_info,
                images=images,
                feedback="❌ Thiếu ảnh hoặc tên nhóm."
            )

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

    return render_template(
        'project.html',
        project=project_info,
        images=images,
        feedback=ai_feedback
    )

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

    target_image = next((img for img in images if img.get("id") == image_id), None)

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

    scores = [c["score"] for c in target_image.get("comments", []) if "score" in c]
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
            return render_template('upload_image.html', feedback="❌ Thiếu file hoặc tên nhóm.", images=images)

        if not allowed_file(uploaded_file.filename):
            return render_template('upload_image.html', feedback="❌ File không hợp lệ. Chỉ chấp nhận ảnh hoặc PDF.", images=images)

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

                # ===== SỬA PROMPT ĐỂ HIỂN THỊ ĐẸP HƠN =====
                ai_response = model.generate_content([
                    img,
                    """Đây là ảnh bài làm của học sinh. Hãy phân tích nội dung, chỉ ra lỗi sai nếu có, và đề xuất cải thiện.

QUAN TRỌNG - Quy tắc trình bày:
- KHÔNG dùng dấu **, ***, ##, ### 
- Công thức toán viết dạng văn bản thường: ví dụ (3x + 6)/(4x - 8) thay vì LaTeX
- Xuống dòng rõ ràng giữa các phần
- Dùng số thứ tự 1., 2., 3. để liệt kê
- Thụt lề đầu dòng khi cần

Trả lời bằng tiếng Việt, trình bày dễ đọc, rõ ràng."""
                ])
                ai_feedback = ai_response.text

                score_response = model.generate_content([
                    img,
                    """Dựa trên bài làm của học sinh, hãy chấm điểm theo các tiêu chí sau:

1. Nội dung đầy đủ (0-10 điểm)
2. Trình bày rõ ràng (0-10 điểm)
3. Kỹ thuật chính xác (0-10 điểm)
4. Thái độ học tập (0-10 điểm)

Sau đó tổng kết điểm trung bình trên thang 10 và đưa ra nhận xét ngắn gọn.

QUAN TRỌNG - Quy tắc trình bày:
- KHÔNG dùng dấu **, ***, ##, ###
- Công thức toán viết văn bản thường, không LaTeX
- Xuống dòng rõ ràng
- Format kiểu:
  Nội dung đầy đủ: 8/10
  Trình bày rõ ràng: 7/10
  (mỗi tiêu chí 1 dòng)

Trả lời bằng tiếng Việt."""
                ])
                score_feedback = score_response.text

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

if __name__ == "__main__":
    app.run(debug=True)