# Email Agent

Answers course questions sent to a Gmail inbox, using only the facts in `knowledge_base.txt` and a local Ollama model. No paid APIs, and no Python packages to install.

For each new email from the allowed sender, the agent replies with one of:

| Situation | Reply |
| --- | --- |
| The knowledge base fully answers the question | A short answer (1-3 sentences) |
| It only partly answers it, or the email asks for a decision or exception | "A team member will follow up." |
| The topic isn't covered | "I am unable to answer your question on that topic." |

## Requirements

- Python 3
- [Ollama](https://ollama.com), installed and running
- A Gmail account with 2-Step Verification turned on (needed to create an app password)

## Setup

1. Pull the base model:

   ```
   ollama pull qwen2.5:3b
   ```

2. Create your `.env` file:

   ```
   cp .env.example .env
   ```

   Then fill it in:

   - `GMAIL_ADDRESS`: the inbox the agent watches and replies from
   - `GMAIL_APP_PASSWORD`: a Gmail app password (Google Account > Security > App passwords), not your normal password
   - `ALLOWED_SENDER`: the only address the agent will answer

   `.env` is listed in `.gitignore`. Never commit it.

3. Build the agent's model:

   ```
   python3 build_model.py
   ```

   This copies `knowledge_base.txt` into the `Modelfile`'s system prompt and creates an Ollama model named `email-agent`.

## Running

```
python3 email_agent.py
```

The agent checks Gmail every 30 seconds and answers only emails that arrive after it starts. Press Ctrl+C to stop.

It sends real replies by default. To save replies as Gmail drafts instead, so you can review them first:

```
SEND_MODE=draft python3 email_agent.py
```

Answered emails get the Gmail label `AutoReplied` so they are never answered twice. Emails that got the "team member will follow up" reply also get `NeedsFollowUp`.

## Changing the knowledge base or prompt

- Facts the agent can use: edit `knowledge_base.txt`.
- Instructions for the model: edit the `SYSTEM` block in `Modelfile`. Leave the `KNOWLEDGE BASE:` / `<<<` / `>>>` lines in place; `build_model.py` overwrites whatever is between them.

After either change, rebuild:

```
python3 build_model.py
```

If the model is out of date with `knowledge_base.txt`, the agent refuses to start and tells you to rebuild.

## Files

| File | Purpose |
| --- | --- |
| `email_agent.py` | The agent: reads Gmail, asks the model, replies |
| `Modelfile` | Base model, settings, and system prompt for `email-agent` |
| `build_model.py` | Rebuilds `email-agent` from the Modelfile and knowledge base |
| `knowledge_base.txt` | The only facts the agent may use |
| `.env.example` | Template for your `.env` |
