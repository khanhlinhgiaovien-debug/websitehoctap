# Supabase PostgreSQL Migration

Ứng dụng hiện hỗ trợ 2 chế độ lưu dữ liệu cho hệ thống kiểm tra:

- Không có `DATABASE_URL`: dùng các file JSON trong thư mục `data/`.
- Có `DATABASE_URL`: dùng PostgreSQL/Supabase qua bảng `exam_system_store`.

## Cấu hình Render

Trong Render service, thêm Environment Variable:

```text
DATABASE_URL=postgresql://...
```

Nên dùng connection string dạng pooled/shared pooler của Supabase. Nếu URL chưa có `sslmode`, app sẽ tự dùng:

```text
DATABASE_SSLMODE=require
```

## Import dữ liệu JSON hiện tại

App có cơ chế tự bootstrap: nếu database còn trống, lần đọc đầu tiên sẽ import collection từ JSON lên DB.

Nếu muốn import thủ công:

```bash
python scripts/import_exam_json_to_db.py
```

Nếu muốn ghi đè dữ liệu collection đã tồn tại:

```bash
python scripts/import_exam_json_to_db.py --force
```

## Export backup từ DB về JSON

```bash
python scripts/export_exam_db_to_json.py --output-dir data_backup
```

## Kiểm tra

Vào trang admin:

```text
/admin/dashboard
```

Mục `Lưu trữ dữ liệu` sẽ hiển thị:

- `JSON local`: chưa cấu hình `DATABASE_URL`.
- `PostgreSQL/Supabase`: đã chạy bằng database.
