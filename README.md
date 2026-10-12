# Email Agent

Answers course questions sent to a Gmail inbox, using only the facts in `knowledge_base.txt` and a Cohere model called through the Cohere API. No Python packages to install.

For each new email from an allowed sender, the agent replies with one of:

| Situation | Reply |
| --- | --- |
| The knowledge base fully answers the question | A short answer (1-3 sentences) |
| It only partly answers it, or the email asks for a decision or exception | "A team member will follow up." |
| The topic isn't covered | "I am unable to answer your question on that topic." |

## Requirements

- Python 3
- A [Cohere API key](https://dashboard.cohere.com/api-keys) (a free trial key works, with rate limits)
- A Gmail account with 2-Step Verification turned on (needed to create an app password)

## Setup

1. Create your `.env` file:

   ```
   cp .env.example .env
   ```

2. Fill it in:

   - `GMAIL_ADDRESS`: the inbox the agent watches and replies from
   - `GMAIL_APP_PASSWORD`: a Gmail app password (Google Account > Security > App passwords), not your normal password
   - `ALLOWED_SENDERS`: the only addresses the agent will answer, separated by commas (e.g. `a@example.com,b@example.com`)
   - `COHERE_API_KEY`: your Cohere API key

   `.env` is listed in `.gitignore`. Never commit it.

Each email from an allowed sender, along with the whole knowledge base, is sent to Cohere to be classified and answered.

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
- Instructions for the model: edit `SYSTEM_PROMPT_TEMPLATE` in `email_agent.py`.
- Which Cohere model is used: set `COHERE_MODEL` in `.env` (default `command-a-plus-05-2026`).

Restart the agent to pick up any of these changes.

## Files

| File | Purpose |
| --- | --- |
| `email_agent.py` | The agent: reads Gmail, asks the Cohere model, replies |
| `knowledge_base.txt` | The only facts the agent may use |
| `.env.example` | Template for your `.env` |
