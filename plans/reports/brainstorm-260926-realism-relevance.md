# Brainstorm 260926 — video chân thật hơn, hình liên quan hơn, chuyển cảnh nhẹ, phụ đề chuẩn

## Vấn đề (đo trên video phishing-…-260926)
- Hình lệch: fallback_queries rút gọn mất nghĩa ("email with", "person clicking", "fake bank"); relevance chỉ so chữ slug → green screen (c1), bàn tay (c6), tiền "fake" (c7), máy tính bỏ túi (c8).
- Giọng rời rạc: cut_point = giữa quãng nghỉ edge-tts + pad 0.15s → lặng 1.0–1.4s mỗi ranh giới cảnh.
- Chuyển cảnh: xfade 0.3s mọi cảnh → khung giữa đục khi 2 cảnh khác màu; zoompan trên video vốn đã động trông giả.
- Phụ đề: 1–3 chữ, \kf fill + pop + phóng to từ khoá → rối, ngắt vụng.
- Không nhạc (assets/music trống).

## Quyết định của user
| Mảng | Chọn |
|---|---|
| Chấm hình | Ollama qwen2.5vl:7b, chấp nhận +2–4 phút/video |
| Chuyển cảnh | cắt thẳng trong cảnh + mờ 0.15–0.2s giữa cảnh; bỏ zoom trên video |
| Phụ đề | cụm 3–6 chữ, 1 dòng ≤~26 ký tự, trắng viền đen, chữ đang đọc → vàng (\k), không pop/zoom |
| Chân thật | nhịp giọng, nhạc theo mood, hình ít stock, tinh chỉnh edge-tts |
| Nhạc | thư mục assets/music/<mood>/ do user tải; LLM gán mood |
| Giọng | edge-tts: rate cấu hình (+8%), chọn giọng nam/nữ theo video; không TTS mới |

## Thiết kế
1. **Visuals**
   - script: mỗi cảnh `visual_queries` 3 truy vấn cụ thể, quay được (giữ `visual_query` = cái đầu, tương thích ngược).
   - stock.Candidate thêm `thumb` (Pexels video `image`, photo `src.medium`; Pixabay thumbnail).
   - Lọc luật: green screen / chroma key / mockup / template.
   - Top 5 theo điểm text → tải thumb → `vision.py`: 1 request/cảnh, nhiều ảnh, JSON điểm 0–10 (đúng nội dung + thật + không chữ/logo). Chọn max, bỏ <5; không ai ≥5 → giữ max (không placeholder).
   - VRAM: unload qwen3 (keep_alive 0) trước; chấm hết cảnh trong 1 lượt nạp model; tải video sau.
   - Ảnh tĩnh bị trừ điểm so với video. Swap clip dùng cùng judge.
2. **Chuyển cảnh**: không zoompan cho video (giữ crop focus + grade); Ken Burns nhẹ chỉ cho ảnh; xfade 0.18s ở ranh giới cảnh; cắt thẳng trong cảnh.
3. **Chân thật**: cắt lặng (≤0.08s trước, ≤0.2s sau câu); `voice.rate` config; film grain nhẹ (`noise=alls=3:allf=t`); nhạc theo mood: offset ngẫu nhiên, fade in/out, ducking sẵn có; thiếu mood → bài bất kỳ; không bài → không nhạc.
4. **Phụ đề**: chunk theo max_chars 26 + max_words 6 + min 3 (trừ cuối câu); giữ FUNCTION_WORDS/số+đơn vị/không mồ côi; `\k` thay `\kf`; bỏ POP, EMPHASIS; giữ safe-zone margins.

## Rủi ro
- Pexels thiếu clip cho khái niệm trừu tượng → vision chỉ loại sai, không tạo đúng; ra minh hoạ gần đúng.
- Swap model Ollama trên 8GB: chậm lần nạp (~10s); qwen3 cần nạp lại cho metadata.
- Vision JSON không hợp lệ → fallback về xếp hạng text (không fail job).
- Trim lặng quá tay cắt mất âm cuối → dùng mốc word end + 0.2s, test trên 2 video.
- edge-tts không cho SSML → không chỉnh ngữ điệu từng câu.

## Tiêu chí hoàn thành
- Render lại phishing: cảnh 1/6/7/8 hình đúng nội dung (điểm ≥6).
- Không khoảng lặng giữa cảnh >0.4s.
- Mọi cụm phụ đề 3–6 chữ (trừ câu ngắn hơn), ≤26 ký tự, không kết thúc hư từ.
- Thời gian tăng ≤4 phút/video.
- Test pass; test không gọi Ollama/Pexels/Wikipedia thật.

## Ngoài phạm vi
TTS mới, AI video, tự tải nhạc, tối ưu riêng video dài.

## File chạm
visuals/stock.py, visuals/selector.py, visuals/vision.py (mới), script/writer.py + prompts, models.py (Scene.visual_queries, mood), voice/builder.py, voice/tts.py, assemble/clips.py, assemble/subtitles.py, assemble/render.py, assemble/music.py, config.yaml, tests.
