#!/usr/bin/env python3
"""
Rebuild the Ollama model used by email_agent.py.

Copies knowledge_base.txt into the KNOWLEDGE BASE section of the Modelfile's
SYSTEM prompt, then runs `ollama create`. Run this after editing either
knowledge_base.txt or the Modelfile.

Uses only the Python standard library.
"""

import re
import subprocess
import sys
from pathlib import Path

MODEL_NAME = "email-agent"

HERE = Path(__file__).parent
MODELFILE = HERE / "Modelfile"
KNOWLEDGE_BASE_FILE = HERE / "knowledge_base.txt"

# The knowledge base lives between these two marker lines in the Modelfile.
KB_SECTION = re.compile(r"(KNOWLEDGE BASE:\n<<<\n).*?(\n>>>)", re.DOTALL)


def main():
    kb_text = KNOWLEDGE_BASE_FILE.read_text(encoding="utf-8").strip()
    if '"""' in kb_text:
        sys.exit('ERROR: knowledge_base.txt contains """, which would end the SYSTEM block early.')

    modelfile = MODELFILE.read_text(encoding="utf-8")
    updated, count = KB_SECTION.subn(lambda m: m.group(1) + kb_text + m.group(2), modelfile)
    if count != 1:
        sys.exit("ERROR: could not find the KNOWLEDGE BASE <<< ... >>> section in the Modelfile.")
    MODELFILE.write_text(updated, encoding="utf-8")

    subprocess.run(["ollama", "create", MODEL_NAME, "-f", str(MODELFILE)], check=True)
    print(f"Built {MODEL_NAME} from {MODELFILE.name} + {KNOWLEDGE_BASE_FILE.name}.")


if __name__ == "__main__":
    main()
