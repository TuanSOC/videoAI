import os

# tests never reach hosted LLMs: environment beats .env for pydantic-settings, so the real key stays unused
os.environ["GROQ_API_KEY"] = ""
