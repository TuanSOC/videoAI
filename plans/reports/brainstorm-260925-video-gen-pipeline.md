# Brainstorm: AI Video Generation Pipeline (faceless channels)

Date: 2026-09-25 · Status: approved

## Problem / goal
Tool nội bộ: nhập topic → video MP4 realistic để đăng YouTube/TikTok/Reels kiếm tiền trên kênh của chính user. Chi phí ~$0.

## Requirements (confirmed)
- Mục tiêu: tool cho kênh riêng (không SaaS)
- Format: short 9:16 (<60-90s) + long 16:9 (8-15 phút)
- Ngôn ngữ: vi + en
- Niche: kể chuyện/sự thật thú vị (MVP); tài chính/tech/AI news (phase 2)
- Budget: càng rẻ càng tốt → $0 API
- Hardware: NVIDIA GPU ≥12GB VRAM
- Stack: Python
- Automation: topic → MP4 + metadata; user duyệt & upload tay

## Key facts / constraints
- YouTube (7/2025) "inauthentic content" policy → mass-produced AI bị demonetize. Cần human review + giá trị riêng.
- YT/TikTok/Meta bắt buộc label AI realistic content.
- Veo 3: free chỉ qua UI (Gemini app Veo 3.1 Lite, Flow 50 credits/ngày). API KHÔNG free (tính theo giây). GCP $300 credit mới có thể thử.
- Open-source local: Wan 2.2 (Apache 2.0, realism tốt nhất), LTX-2.3 (có audio). 5s clip ≈ 3-8 phút trên 12GB → không khả thi 100% AI cho long-form.

## Approaches evaluated
| | A. Stock only | **B. Hybrid (chosen)** | C. 100% AI local |
|---|---|---|---|
| Cost | $0 | $0 | $0 |
| Long 10' render | ~5' | ~20-40' | 10-20h |
| Realism | cao nhất | cao | TB, lộ lỗi AI |
| Inauthentic risk | thấp | thấp | cao |

## Final solution — Hybrid Python pipeline
```
topic → 1.Script → 2.Voice → 3.Timing → 4.Visuals → 5.Assemble → final.mp4 + metadata.json
```
| Stage | Tool | Notes |
|---|---|---|
| Script + scenes | Gemini API free (Flash); fallback Ollama/Qwen | JSON scenes {narration, visual keywords}; prompt riêng short (hook 3s) vs long (chương) |
| Voice | edge-tts (fallback Kokoro/Piper) | vi: HoaiMy/NamMinh; en neural |
| Timing | faster-whisper (GPU) | word timestamps → karaoke subs, scene cuts |
| Visuals | Pexels+Pixabay API → Flux-schnell → Wan 2.2 via ComfyUI API | fallback chain per scene, cache assets |
| Assemble | FFmpeg | 9:16/16:9, Ken Burns, ASS subs, music ducking |
| Metadata | LLM | title/desc/hashtags + AI disclosure + sources |

CLI: `python -m vidgen make "<topic>" --format short|long --lang vi|en [--review]`
Output: `output/<slug>/{script.json, voice.mp3, final.mp4, metadata.json}`
`--review` mặc định bật: dừng sau script cho user sửa.

## Acceptance criteria
- Topic → MP4 chạy được cho vi/en × short/long
- Sub lệch ≤0.2s so với giọng
- Không watermark, chỉ asset license free-commercial
- Short < 10', long < 45' render trên GPU 12GB
- $0 API cost

## Out of scope (MVP)
Auto-upload, trend discovery, web UI, voice clone, avatar, SaaS, finance/news data grounding.

## Risks
1. Finance/AI news: LLM hallucinate số liệu → phase 2 cần RSS/source grounding.
2. Nhạc: chỉ Pixabay Music / YT Audio Library.
3. edge-tts unofficial + giọng robot → nâng ElevenLabs (~$5/th) khi có doanh thu.
4. Posting cadence 1-2 video/ngày có review, tránh spam flag.
5. Gemini free tier rate limits có thể đổi → giữ Ollama fallback.

## Success metrics
- Pipeline tạo ≥1 video/ngày ổn định không lỗi
- Kênh đạt YPP threshold (1000 subs + 4000h hoặc 10M Shorts views/90 ngày)
- Retention short >50%, long >35%

## Next steps
1. /ck:plan với report này
2. Phase 2: finance/AI news module (RSS grounding)
3. Phase 3: trend discovery, scheduling upload
