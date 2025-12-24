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
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Cấu hình thư mục upload
UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

api_key = os.environ.get("GOOGLE_API_KEY")  # ← SỬA DÒNG NÀY
if not api_key:  
    raise ValueError(" Thiếu GOOGLE_API_KEY trong file .env")
genai.configure(api_key=api_key)  # ← SỬA DÒNG NÀY
model = genai.GenerativeModel("models/gemini-2.5-flash")
analysis_model = model




CLASS_ACTIVITY_FILE = os.path.join('data', 'class_activities.json')
CLASS_ACTIVITY_IMAGES = os.path.join('static', 'class_activity_uploads')

# Tạo thư mục nếu chưa có
os.makedirs(os.path.dirname(CLASS_ACTIVITY_FILE), exist_ok=True)
os.makedirs(CLASS_ACTIVITY_IMAGES, exist_ok=True)
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
        return redirect(url_for('class_activity_detail', activity_id=activity_id))
    
    return render_template('new_class_activity.html')

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
            return redirect(url_for('class_activity_detail', activity_id=activity_id))
        
        if not uploaded_files or all(f.filename == '' for f in uploaded_files):
            flash('Vui lòng chọn ít nhất 1 ảnh!', 'error')
            return redirect(url_for('class_activity_detail', activity_id=activity_id))
        
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
                    'id': file_id,
                    'filename': filename,
                    'uploaded_at': datetime.now().strftime("%d/%m/%Y %H:%M")
                })
        
        # Cập nhật activity
        for i, a in enumerate(activities):
            if a['id'] == activity_id:
                activities[i] = activity
                break
        
        save_class_activities(activities)
        
        flash(f'Đã upload ảnh cho {group_name}!', 'success')
        return redirect(url_for('class_activity_detail', activity_id=activity_id))
    
    return render_template('class_activity_detail.html', activity=activity)
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
        flash('Chưa có ảnh nào được upload. Vui lòng upload ảnh trước khi phân tích!', 'error')
        return redirect(url_for('class_activity_detail', activity_id=activity_id))
    
    try:
        # ========================================
        # BƯỚC 1: PHÂN TÍCH TEXT TỪ ẢNH CÁC TỔ
        # ========================================
        analysis_prompt = [f"""Bạn là giáo viên chủ nhiệm đang đánh giá sinh hoạt lớp tuần này.

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

ĐỊNH DẠNG PHẢN HỒI (JSON) - BẮT BUỘC:
{{
  "tong_quan": "Tổng quan về tuần học...",
  "thoi_khoa_bieu": [
    {{"thu": "Thứ 2", "tiet_1": "Toán", "tiet_2": "Văn", "tiet_3": "Anh", "tiet_4": "Hóa", "tiet_5": "Thể dục", "do_dong_phuc": "Áo trắng"}},
    {{"thu": "Thứ 3", "tiet_1": "Lý", "tiet_2": "Sinh", "tiet_3": "Sử", "tiet_4": "Địa", "tiet_5": "GDCD", "do_dong_phuc": "Quần tây"}},
    {{"thu": "Thứ 4", "tiet_1": "Toán", "tiet_2": "Văn", "tiet_3": "Anh", "tiet_4": "Vật lý", "tiet_5": "TD", "do_dong_phuc": "Áo trắng"}}
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
    "Giữ gìn vệ sinh, không xả rác bừa bãi"
  ]
}}

CHỈ TRẢ VỀ JSON, KHÔNG THÊM TEXT KHÁC.

Dưới đây là báo cáo các tổ:
"""]
        
        # Thêm ảnh của từng tổ
        for group_name, images in activity['groups'].items():
            if images:
                group_display = {
                    'to_1': 'TỔ 1', 'to_2': 'TỔ 2', 
                    'to_3': 'TỔ 3', 'to_4': 'TỔ 4',
                    'giao_vien': 'GIÁO VIÊN'
                }
                analysis_prompt.append(f"\n--- BÁO CÁO {group_display[group_name]} ---")
                
                for img_data in images:
                    img_path = os.path.join(CLASS_ACTIVITY_IMAGES, img_data['filename'])
                    if os.path.exists(img_path):
                        img = Image.open(img_path)
                        analysis_prompt.append(img)
        
        # Gọi Gemini phân tích
        analysis_response = model.generate_content(analysis_prompt)
        ai_analysis = clean_ai_output(analysis_response.text)
        
        # Parse JSON
        try:
            # Loại bỏ markdown code blocks
            ai_analysis_clean = ai_analysis.replace('```json', '').replace('```', '').strip()
            analysis_data = json.loads(ai_analysis_clean)
        except Exception as parse_error:
            print(f"JSON Parse Error: {parse_error}")
            print(f"AI Response: {ai_analysis}")
            # Tạo data mẫu nếu parse thất bại
            analysis_data = {
                "tong_quan": "Không thể phân tích được dữ liệu từ ảnh.",
                "thoi_khoa_bieu": [
                    {"thu": "Thứ 2", "tiet_1": "Toán", "tiet_2": "Văn", "tiet_3": "Anh", "tiet_4": "Hóa", "tiet_5": "TD"},
                    {"thu": "Thứ 3", "tiet_1": "Lý", "tiet_2": "Sinh", "tiet_3": "Sử", "tiet_4": "Địa", "tiet_5": "GDCD"}
                ],
                "nhan_xet_tuan_qua": [
                    {"tieu_chi": "Học tập", "danh_gia": "Tốt", "xep_loai": "Khá", "icon": "✅"}
                ],
                "phuong_huong_tuan_moi": [
                    "Ôn tập chủ động",
                    "Tham gia phát biểu"
                ]
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
        html_prompt = f"""Tạo file HTML HOÀN CHỈNH cho infographic kế hoạch tuần học lớp 8A4 - THCS Cẩm Phả.

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
    <h1 style="text-align:center; color:white; font-size:48px; text-shadow: 3px 3px 6px rgba(0,0,0,0.3); margin-bottom:10px;">KẾ HOẠCH TUẦN HỌC LỚP 8A4</h1>
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
        html_content = html_content.replace('```html', '').replace('```', '').strip()
        
        # Lưu file HTML
        infographic_dir = "static/class_activity_infographics"
        os.makedirs(infographic_dir, exist_ok=True)
        
        infographic_filename = f"{activity_id}_infographic.html"
        infographic_path = os.path.join(infographic_dir, infographic_filename)
        
        with open(infographic_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        activity['infographic_html'] = f"/static/class_activity_infographics/{infographic_filename}"
        
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
        return redirect(url_for('class_activity_detail', activity_id=activity_id))
    
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
                img_path = os.path.join(CLASS_ACTIVITY_IMAGES, img_data['filename'])
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
- Giúp học sinh hiểu bài, giải đáp thắc mắc về mọi môn học
- Phân tích bài làm, hình ảnh bài tập học sinh gửi lên
- Giải thích từng bước một cách dễ hiểu, phù hợp với trình độ học sinh

CÁCH TRẢ LỜI:
1. Luôn trả lời bằng tiếng Việt
2. Giải thích chi tiết, dễ hiểu, có ví dụ cụ thể
3. Với bài toán: trình bày từng bước, công thức, cách tính
4. Với văn: phân tích ý nghĩa, thông điệp, kỹ thuật viết
5. Với bài làm/ảnh: chỉ ra điểm tốt, lỗi sai, cách cải thiện
6. Khuyến khích học sinh tư duy, không chỉ đưa đáp án

QUY TẮC TRÌNH BÀY:
- KHÔNG dùng **, ***, ##, ###, ````
- Công thức toán viết văn bản thường: (x + 2)/(x - 3) hoặc x^2 + 3x + 2
- Xuống dòng rõ ràng giữa các ý
- Dùng số thứ tự 1. 2. 3. hoặc dấu gạch đầu dòng -
- Giữ văn phong thân thiện, động viên

Hãy ưu tiên sử dụng thông tin từ KIẾN THỨC CƠ SỞ khi trả lời các câu hỏi liên quan.
"""

        try:
            # Xử lý nếu có file đính kèm
            if uploaded_file and uploaded_file.filename != '':
                file_ext = uploaded_file.filename.rsplit('.', 1)[1].lower()
                
                # Lưu file tạm
                temp_filename = f"temp_{uuid.uuid4()}_{secure_filename(uploaded_file.filename)}"
                temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
                uploaded_file.save(temp_path)
                
                # Xử lý theo loại file
                if file_ext == 'pdf':
                    # Đọc text từ PDF
                    pdf_text = extract_text_from_pdf(temp_path)
                    full_prompt = f"{system_prompt}\n\nHọc sinh gửi file PDF với nội dung:\n{pdf_text}\n\nCâu hỏi: {user_message if user_message else 'Hãy phân tích nội dung file này'}"
                    response = model.generate_content([full_prompt])
                    response_text = response.text
                    
                elif file_ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']:
                    # Đọc ảnh
                    img = Image.open(temp_path)
                    full_prompt = f"{system_prompt}\n\nHọc sinh gửi ảnh và hỏi: {user_message if user_message else 'Hãy phân tích nội dung ảnh này'}"
                    response = model.generate_content([img, full_prompt])
                    response_text = response.text
                    
                else:
                    response_text = " Định dạng file không được hỗ trợ. Chỉ chấp nhận ảnh (.png, .jpg, .jpeg) hoặc PDF."
                
                # Xóa file tạm
                try:
                    os.remove(temp_path)
                except:
                    pass
                    
            else:
                # Chỉ có text message
                if user_message:
                    full_prompt = f"{system_prompt}\n\nHọc sinh hỏi: {user_message}"
                    response = model.generate_content([full_prompt])
                    response_text = response.text
                else:
                    response_text = "Vui lòng nhập câu hỏi hoặc gửi file."
            
            # Làm sạch output
            response_text = clean_ai_output(response_text)
            
            # Lưu vào lịch sử chat
            session['chat_history'].append({
                'user': user_message if user_message else '[Đã gửi file]',
                'bot': response_text,
                'timestamp': datetime.now().strftime("%H:%M")
            })
            session.modified = True
            
        except Exception as e:
            response_text = f" Lỗi: {str(e)}"
    
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
        expert = next((e for e in experts if e['username'] == username and e['password'] == password), None)
        
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
        
        if not student_name or not question:
            flash('Vui lòng nhập đầy đủ thông tin!', 'error')
            return redirect(url_for('health_support'))
        
        question_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        new_question = {
            'id': question_id,
            'student_name': student_name,
            'question': question,
            'consult_type': consult_type,
            'timestamp': timestamp,
            'ai_response': None,
            'expert_responses': [],
            'status': 'pending'  # pending, answered
        }
        
        # Nếu chọn AI tư vấn
        if consult_type == 'ai':
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
        return redirect(url_for('health_support'))
    
    # Kiểm tra xem user có phải chuyên gia không
    is_expert = session.get('expert_logged_in', False)
    
    return render_template('health_support.html', 
                         questions=questions, 
                         is_expert=is_expert,
                         expert_name=session.get('expert_name'))

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
        
        with open('health_questions.json', 'w', encoding='utf-8') as f:
            json.dump(questions, f, ensure_ascii=False, indent=2)
        
        flash('Đã gửi câu trả lời!', 'success')
    else:
        flash('Không tìm thấy câu hỏi!', 'error')
    
    return redirect(url_for('health_support'))
#####

def generate_feedback(text):
    """Tạo feedback từ text bằng AI"""
    try:
        prompt = f"Đây là nội dung bài làm của học sinh:\n\n{text}\n\nHãy phân tích, chỉ ra lỗi sai và đề xuất cải thiện. Trả lời bằng tiếng Việt."
        response = model.generate_content([prompt])
        return response.text
    except Exception as e:
        return f" Lỗi khi tạo feedback: {str(e)}"

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
    text = re.sub(r'\*\*', '', text)    # Loại bỏ **
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
if __name__ == "__main__":
    app.run(debug=True)