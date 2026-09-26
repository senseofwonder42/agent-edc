"""Check native tool calling against the vLLM server before anything else (§ 8.3).

Usage: uv run python scripts/00_check_tool_calling.py
"""

from agent_edc.config import VLMConfig
from agent_edc.llm import build_chat_model, check_tool_calling

if __name__ == "__main__":
    config = VLMConfig()
    check_tool_calling(build_chat_model(config))
    print(f"OK — appel d'outils natif fonctionnel sur {config.model}")
