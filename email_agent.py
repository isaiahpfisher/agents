#!/usr/bin/env python3
"""
Email agent: answers course questions using ONLY knowledge_base.txt,
with a local Ollama model. No paid APIs, no third-party services.

The system prompt (instructions + knowledge base) lives in the Modelfile and
is baked into the Ollama model. After editing knowledge_base.txt or the
Modelfile, run: python3 build_model.py

Rules:
  Full match            -> short answer, only facts from the knowledge base
  Partial match/unsure  -> "A team member will follow up."
  No match              -> "I am unable to answer your question on that topic."

Only emails from ALLOWED_SENDER are answered. Handled emails get the Gmail
label "AutoReplied" so they are never answered twice; partial matches also
get "NeedsFollowUp".

Uses only the Python standard library.
"""

import email
import email.policy
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from html import unescape
from pathlib import Path

# ----------------------------- Settings ---------------------------------------

def load_env_file(path):
    """Read KEY=VALUE lines from a .env file into os.environ.
    Variables already set in the real environment win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


# Secrets and addresses live in .env (not committed); see .env.example.
load_env_file(Path(__file__).with_name(".env"))

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")

ALLOWED_SENDER = os.environ.get("ALLOWED_SENDER", "").strip().lower()

KNOWLEDGE_BASE_FILE = Path(__file__).with_name("knowledge_base.txt")

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "email-agent")   # built by build_model.py

POLL_SECONDS = 30            # how often to check Gmail
SEND_MODE = os.environ.get("SEND_MODE", "send").lower()   # "draft" or "send"

DONE_LABEL = "AutoReplied"
FOLLOWUP_LABEL = "NeedsFollowUp"

PARTIAL_REPLY = "A team member will follow up."
NO_MATCH_REPLY = "I am unable to answer your question on that topic."

MAX_BODY_CHARS = 4000        # ignore anything past this in long emails

# ------------------------------------------------------------------------------

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "match": {"type": "string", "enum": ["full", "partial", "none"]},
        "answer": {"type": "string"},
    },
    "required": ["match", "answer"],
}


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# ----------------------------- Email parsing ----------------------------------

def html_to_text(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", "", html))


def get_body(msg):
    """Return the plain-text body of an email (falls back to HTML)."""
    plain, html = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = content
        elif ctype == "text/html" and html is None:
            html = content
    text = plain if plain is not None else html_to_text(html or "")
    return strip_quoted(text)


def strip_quoted(text):
    """Remove quoted earlier messages so only the new question is analyzed."""
    text = text.replace("\r\n", "\n")
    # Cut at "On <date>, <name> wrote:" (may wrap over two lines)
    text = re.split(r"\n\s*On [^\n]{0,200}(?:\n[^\n]{0,200})?wrote:\s*\n", text)[0]
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()[:MAX_BODY_CHARS]


# ----------------------------- Model ------------------------------------------

def ask_model(subject, body):
    # No system message and no options here: the system prompt, temperature,
    # and context size all come from the Modelfile.
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": RESPONSE_SCHEMA,
        "messages": [
            {"role": "user", "content": f"EMAIL SUBJECT: {subject}\n\nEMAIL BODY:\n{body}"},
        ],
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=150) as resp:
        data = json.loads(resp.read())
    return json.loads(data["message"]["content"])


def numbers_are_grounded(answer, kb_text):
    """Every number in the answer must appear in the knowledge base.
    Catches made-up times, dates, percentages, and room numbers."""
    pattern = r"\d+(?::\d+)?"          # whole numbers and clock times like 12:30
    kb_numbers = set(re.findall(pattern, kb_text))
    return all(n in kb_numbers for n in re.findall(pattern, answer))


def decide_reply(kb_text, subject, body):
    """Return (category, reply_text). Raises if Ollama is unreachable,
    so the email is retried on the next poll instead of mis-answered."""
    try:
        result = ask_model(subject, body)
    except urllib.error.URLError:
        raise
    except Exception as e:  # bad JSON or odd output -> play it safe
        log(f"  model output problem ({e}); treating as partial")
        return "partial", PARTIAL_REPLY

    match = str(result.get("match", "")).lower()
    answer = str(result.get("answer", "")).strip()

    if match == "full" and answer:
        if not numbers_are_grounded(answer, kb_text):
            log("  answer contained a number not in the knowledge base; treating as partial")
            return "partial", PARTIAL_REPLY
        return "full", answer
    if match == "none":
        return "none", NO_MATCH_REPLY
    return "partial", PARTIAL_REPLY


# ----------------------------- Gmail ------------------------------------------

def build_reply(original, reply_text):
    reply = EmailMessage()
    subject = original.get("Subject", "") or ""
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    reply["From"] = GMAIL_ADDRESS
    reply["To"] = ALLOWED_SENDER
    reply["Subject"] = subject
    reply["Date"] = formatdate(localtime=True)
    reply["Message-ID"] = make_msgid(domain="gmail.com")
    orig_id = original.get("Message-ID")
    if orig_id:
        reply["In-Reply-To"] = orig_id
        refs = original.get("References", "")
        reply["References"] = (refs + " " + orig_id).strip()
    reply.set_content(reply_text)
    return reply


def send_reply(reply):
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as smtp:
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.send_message(reply)


def save_draft(imap, reply):
    imap.append('"[Gmail]/Drafts"', "(\\Draft)",
                imaplib.Time2Internaldate(time.time()), reply.as_bytes())


def add_label(imap, uid, label):
    imap.uid("STORE", uid, "+X-GM-LABELS", f"({label})")


def poll_once(kb_text, start_ts, handled_ids):
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX")

        query = f'"from:{ALLOWED_SENDER} -label:{DONE_LABEL} after:{start_ts}"'
        typ, data = imap.uid("SEARCH", "X-GM-RAW", query)
        if typ != "OK" or not data or not data[0]:
            return

        for uid in data[0].split():
            typ, fetched = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not fetched or fetched[0] is None:
                continue
            msg = email.message_from_bytes(fetched[0][1], policy=email.policy.default)

            # Double-check the actual sender address (not just a name match)
            sender = parseaddr(str(msg.get("From", "")))[1].lower()
            if sender != ALLOWED_SENDER:
                continue

            msg_id = msg.get("Message-ID", f"uid-{uid.decode()}")
            if msg_id in handled_ids:
                continue

            subject = str(msg.get("Subject", ""))
            body = get_body(msg)
            log(f"New email: {subject!r}")

            category, reply_text = decide_reply(kb_text, subject, body)
            log(f"  -> {category}: {reply_text}")

            reply = build_reply(msg, reply_text)
            if SEND_MODE == "send":
                send_reply(reply)
                log("  sent")
            else:
                save_draft(imap, reply)
                log("  saved as draft (SEND_MODE=draft)")

            handled_ids.add(msg_id)
            add_label(imap, uid, DONE_LABEL)
            if category == "partial":
                add_label(imap, uid, FOLLOWUP_LABEL)


# ----------------------------- Startup ----------------------------------------

def preflight():
    problems = []
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD or not ALLOWED_SENDER:
        problems.append("Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD, and ALLOWED_SENDER in .env "
                        "(copy .env.example to .env and fill it in).")
    if not KNOWLEDGE_BASE_FILE.exists():
        problems.append(f"Missing {KNOWLEDGE_BASE_FILE.name} next to this script.")
    if SEND_MODE not in ("draft", "send"):
        problems.append('SEND_MODE must be "draft" or "send".')
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            models = [m["name"] for m in json.loads(resp.read()).get("models", [])]
        if not any(m == OLLAMA_MODEL or m.startswith(OLLAMA_MODEL + ":") for m in models):
            problems.append(f"Model {OLLAMA_MODEL} not found. Run: python3 build_model.py")
        elif KNOWLEDGE_BASE_FILE.exists():
            # The knowledge base is baked into the model; make sure it is current.
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/show",
                data=json.dumps({"model": OLLAMA_MODEL}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                baked_prompt = json.loads(resp.read()).get("system", "")
            if KNOWLEDGE_BASE_FILE.read_text(encoding="utf-8").strip() not in baked_prompt:
                problems.append(f"Model {OLLAMA_MODEL} is out of date with "
                                f"{KNOWLEDGE_BASE_FILE.name}. Run: python3 build_model.py")
    except Exception:
        problems.append("Ollama is not running. Open the Ollama app (or run: ollama serve).")
    if problems:
        for p in problems:
            print("ERROR:", p)
        sys.exit(1)

    # Make sure the labels exist (harmless if they already do)
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        for label in (DONE_LABEL, FOLLOWUP_LABEL):
            imap.create(label)


def main():
    preflight()
    kb_text = KNOWLEDGE_BASE_FILE.read_text(encoding="utf-8")
    start_ts = int(time.time())   # only answer emails that arrive after startup
    handled_ids = set()

    log(f"Email agent running. Model={OLLAMA_MODEL}, mode={SEND_MODE}, "
        f"watching for emails from {ALLOWED_SENDER} every {POLL_SECONDS}s. Ctrl+C to stop.")

    while True:
        try:
            poll_once(kb_text, start_ts, handled_ids)
        except urllib.error.URLError:
            log("Ollama unreachable; will retry next poll.")
        except imaplib.IMAP4.error as e:
            log(f"Gmail login/IMAP error: {e}")
        except Exception as e:
            log(f"Error: {e!r}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")
