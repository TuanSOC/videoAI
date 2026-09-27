# Brainstorm: Groq cho kịch bản + enhance prompt (2026-09-27)

## Problem
Script viết bằng qwen3:8b (Ollama) → nội dung mỏng (2–4 dữ kiện), câu đều đều. User có Groq API key (free tier) và muốn: kịch bản chuẩn + phong phú hơn, và "enhance prompt" cho chủ đề.

## Facts (verified live)
- Groq = OpenAI-compatible `https://api.groq.com/openai/v1/chat/completions`; `response_format: json_schema strict` works.
- Key models: `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` (ctx 131k); both returned valid VI JSON.
- Free limits (headers): 1000 req/day, **8000 tokens/min** per model → script call (~5–6k tok) can hit 429 → must wait/retry.
- `.env` gitignored; key goes to `GROQ_API_KEY` only. Key was pasted in chat → suggest rotating.

## Decisions (user)
- Groq for **creative** tasks: brief angles, script, expand, rewrite, hook fix, metadata, split ideas. **Checker** tasks (fact-check, selector text judge) stay Ollama.
- Chain: `groq:openai/gpt-oss-120b` → `groq:qwen/qwen3.8-27b` → `ollama`.
- Enhance = (a) ✨ topic enrich button, (b) strong-model prompt set.
- Facts: model may use general knowledge for context; figures/dates/names must be in sources; fact-check flags the rest.

## Design
1. `GroqProvider` (llm.py): httpx POST, json_schema strict, 429 → sleep `retry-after` (cap ~60 s) once/twice then raise → next provider. `LLMChain.last_provider` recorded.
2. Config roles: `llm.creative`, `llm.checker` lists of `provider[:model]`; `default_chain(s, role)`; Groq skipped when no key (still $0 path). Keep backward compat with `llm.providers`? → migrate config.yaml, `extra="forbid"` so decide in plan.
3. Prompt tiers: `prompts/strong/<name>.md` used when the provider serving the call is Groq; fallback to Ollama re-renders the base prompt. Chain needs per-tier prompt (callable or dict).
4. Strong prompts: short/long/brief_angles/expand/rewrite — storytelling (character, moment, conflict), 4–6 concrete facts, varied rhythm, keep hook ≤12 words / open loop / payoff / English visual_query rules; figures only from sources.
5. `POST /api/topic/enhance {topic, lang, format}` → `{topic, reason}`; UI ✨ next to topic input, suggestion + [Dùng]/[Bỏ qua]; prompt forbids invented figures.
6. UI: "Viết bởi <model>" + warning when fallback to Ollama happened.

## Out of scope
Web search, more sources, changing the fact-check model.

## Risks
- 8k TPM → waits ~1 min occasionally; fallback chain covers hard failures.
- Free-tier data policy of Groq (public topics OK).
- Strong prompt + model knowledge → more unsourced claims → rely on fact-check flags.

## Success criteria
- Tests: provider (valid JSON, 429 retry-after, fallback), role routing, prompt tier choice, enhance endpoint.
- Live: WannaCry short via Groq vs qwen3:8b — more concrete facts, ≤ same fact-check flags, pipeline end-to-end OK.
