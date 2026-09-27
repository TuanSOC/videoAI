---
phase: 1
title: Groq provider and role routing
status: completed
priority: P1
effort: 3h
dependencies: []
---

# Phase 1: Groq provider and role routing

## Overview
`GroqProvider` in `script/llm.py`, provider specs `name[:model]`, per-role chains, 429 handling, and a record
of which provider actually answered.

## Requirements
- `GroqProvider(api_key, model)`: POST `https://api.groq.com/openai/v1/chat/completions` with
  `response_format={"type":"json_schema","json_schema":{"name":schema.__name__,"strict":True,"schema":...}}`,
  temperature 0.7; gpt-oss models get `reasoning_effort: "low"` (reasoning tokens count against 8k TPM).
  - Strict mode needs `additionalProperties: false` + all props required: build via a helper `strict_schema()`
    that walks `model_json_schema()` (incl. `$defs`); fields with defaults stay required (model fills them).
    If Groq rejects the schema (400) → fall back to `{"type":"json_object"}` + schema text in the prompt.
  - 429: read `retry-after` (s); if ≤ `RATE_WAIT_MAX` (65 s) sleep then retry, max 2 waits; longer (TPD
    exhausted) → raise → next provider. Injectable `sleep` for tests.
  - 401/403 → `LLMError("Groq key invalid …")` (no retry).
- `Secrets.groq_api_key` (`GROQ_API_KEY`), `.env.example` line.
- `LLMConfig`: replace `providers` with `creative: list[str]` and `checker: list[str]` of specs
  `"groq:openai/gpt-oss-120b"`, `"ollama"`, `"ollama:qwen3:8b"`, `"gemini"`. Keep `ollama_model`,
  `ollama_think`, `gemini_model` as defaults for bare specs. Validate spec prefix (`groq|ollama|gemini`).
  `providers` removed (extra="forbid" → old config errors loudly; config.yaml migrated in the same commit).
- `default_chain(s, role="creative")`; skip groq/gemini specs without key; empty → LLMError (existing).
- `LLMChain.last_provider: str` = `"groq:openai/gpt-oss-120b"` etc., set on success; `fallback: bool` = answer
  came from a provider other than the first.
- Callers: `checker` for `factcheck.py` path (`pipeline.check_facts`) and `selector.py:607`; everything else
  keeps `default_chain(s)` (creative).

## Related Code Files
- Modify: `src/vidgen/script/llm.py`, `src/vidgen/config.py`, `config.yaml`, `.env.example`,
  `src/vidgen/pipeline.py` (check_facts), `src/vidgen/visuals/selector.py` (judge chain), `.env` (add key)
- Create: `tests/test_llm_groq.py`

## Implementation Steps (TDD)
1. Tests first (httpx `MockTransport` / monkeypatched `httpx.post`):
   - valid JSON content → model validated; request carries json_schema strict + bearer key.
   - 429 `retry-after: 3` then 200 → one sleep(3), success.
   - 429 `retry-after: 3600` → no sleep, chain moves to next provider; `last_provider` = next, `fallback` True.
   - 401 → next provider immediately.
   - `strict_schema` of `ShortDraft`/`BriefDrafts`: every object has additionalProperties false + all required.
   - `default_chain(role)`: no key → only ollama; with key → groq specs first; checker = ollama only.
   - config rejects `providers:` and unknown spec prefix.
2. Implement; migrate `config.yaml`:
   ```yaml
   llm:
     creative: ["groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b", ollama]
     checker: [ollama]
   ```
3. Run full suite; fix callers/tests that referenced `llm.providers`.

## Success Criteria
- [ ] New tests + existing 300 green.
- [ ] Without `GROQ_API_KEY` pipeline behaviour identical (chain = [ollama]).

## Risk Assessment
- Strict schema rejections for some Pydantic shapes (Literal/enum, optional) → json_object fallback path tested.
- 8k TPM: one short script call ≈ 5–6k tokens → waits happen; cap keeps worst case ~2 min before fallback.
