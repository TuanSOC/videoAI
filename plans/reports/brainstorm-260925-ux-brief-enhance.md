# Brainstorm: UX review toàn luồng + Brief/enhance chủ đề bằng AI

Date: 2026-09-25 · Status: approved · Prev: [brainstorm-260925-video-gen-pipeline.md](./brainstorm-260925-video-gen-pipeline.md)

## Problem (evidence từ dữ liệu thật)
Video `thien-nhien-dia-ly-vi-sao-nuoc-bien-man-giai-thich-260925`: ô chủ đề nhận nguyên đoạn text gồm tiêu đề mục + 2 chủ đề + ghi chú sản xuất →
- kịch bản trộn 2 chủ đề ("Tại sao biển mặn? Bí ẩn cực quang phương Bắc")
- research lấy top-1 Wikipedia mù quáng → nguồn sai: *Why the Sea is Salt* (truyện cổ tích Na Uy), *Cá sấu nước mặn*
- dữ kiện sai theo nguồn sai; user chỉ thấy nguồn SAU khi script viết xong

## Flow review
| Bước | Vấn đề | Mức |
|---|---|---|
| Nhập chủ đề | text thô, nhiều topic, lẫn ghi chú, quá chung | cao (đã lỗi thật) |
| Research | top-1, không check liên quan; không xem/đổi nguồn trước khi viết | cao |
| Duyệt script | không so độ dài với target | TB |
| Render | restart server → mất job, UI lặng lẽ về "Chờ duyệt" | TB |
| Sau render | 1 cảnh xấu → force visuals đổi hết clip | TB |
| Metadata/thư viện | ổn | — |

## Approaches (enhance)
| | Brief 3 góc (chosen) | Nút ✨ cải thiện | Auto ngầm |
|---|---|---|---|
| Sửa multi-topic | có (tách video) | không | một phần |
| Xem/đổi nguồn trước khi viết | có | không | không |
| Kiểm soát user | cao | TB | thấp |
| Chi phí | +15-25s/topic | +5s | +5s |

## Decisions
- Brief step: 3 góc/khác kiểu (giải thích / lật tẩy lầm tưởng / kể chuyện), mỗi góc: title, hook, 3-4 key points, wiki queries/keywords, sources (checkbox loại)
- Multi-topic → tách thành nhiều video (cap 5)
- auto_render → auto chọn góc 1
- Thêm cả 4 UX fix

## Final design (thứ tự build)
1. **Job persistence**: job state → state.json mỗi lần đổi; startup: running/queued → `interrupted`; UI banner + nút Tiếp tục. Files: web/jobs.py, web/app.py, app.js
2. **Research chọn đúng bài**: search top-5 + snippet → LLM chọn bài phù hợp hoặc "none". Files: script/research.py
3. **Brief + multi-topic**: `POST /api/ideas` → LLM split topics → mỗi topic 1 video dir, status `brief`, job tạo `brief.json` (3 angles + sources). `POST /api/videos/{slug}/brief` {angle, edits, excluded sources} → script job; writer nhận title/hook/key points. Files: script/brief.py (new), pipeline.py, writer.py, prompts, web/app.py, app.js
4. **Length bar**: API trả target_seconds; UI ước tính từ WPS, sau voice dùng duration thật; vàng khi ngoài range. Files: web/app.py, app.js
5. **Đổi clip từng cảnh**: selector lưu 3 alternates/cảnh trong assets.json; `POST /api/videos/{slug}/scenes/{id}/swap` {query?} → alt tiếp theo hoặc search mới → invalidate render(+metadata) only. Files: visuals/selector.py, pipeline.py, web/app.py, app.js

## Acceptance
- Dán lại đoạn 2 chủ đề → 2 video; nguồn Seawater/Nước biển + Aurora; không fairy tale/cá sấu
- "bạch tuộc" → 3 góc khác biệt rõ
- Kill server giữa render → "Bị gián đoạn – Tiếp tục" → resume không làm lại bước xong
- Đổi clip cảnh 3 (short) → clip mới không trùng, dựng lại <30s, cảnh khác giữ nguyên
- Thanh độ dài vàng khi <45s hoặc >75s

## Out of scope
Preview hình trước render; queue nhiều render song song; ComfyUI.

## Risks
- qwen3:8b ra 3 góc na ná → prompt ép 3 kiểu khác nhau
- Tách topic sai → user xóa video thừa
- Thêm 1 LLM call chọn bài (~3s)/lookup

## Estimate
~2-3 ngày công.
