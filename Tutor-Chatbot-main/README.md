# Tutor Chatbot

A Socratic-style AI tutor for CS and programming concepts. Instead of giving you the answer directly, it guides you toward understanding through analogies, hints, and leading questions. Supports PDF upload for personalized tutoring based on your own study material.

**Home Page**

<img width="859" height="921" alt="image" src="https://github.com/user-attachments/assets/4655d238-941c-4ee0-a5bc-e440f38d86e5" />

**A discussion about recursion then switching to code execution**

<img width="674" height="890" alt="image" src="https://github.com/user-attachments/assets/d3f69418-6e68-4fc5-82d7-8677f466d8d9" />


## How it works

The backend uses a LangGraph state machine that runs on every message:

1. **extract_topic** — extracts the concept the student wants to learn, updates if the topic changes
2. **assess_understanding** — evaluates the conversation, updates hint level and misconception tracking
3. **choose_strategy** — determines the response approach based on the current hint level

The hint system escalates with each wrong or confused answer:

| Level | Strategy |
|-------|----------|
| 0 | Real-world analogy + broad question |
| 1 | Narrower hint pointing at the gap |
| 2 | Leading question that almost gives it away |
| 3 | Full answer revealed with explanation |

When the student submits code, it is automatically executed via the Judge0 API and the actual output is passed to the tutor for more accurate feedback.

When a PDF is uploaded, the document is chunked, embedded, and stored in an in-memory ChromaDB vector store. Relevant chunks are retrieved on every message and added to the tutor's context.

## Tech Stack

**Backend:** Python, FastAPI, LangChain, LangGraph, ChromaDB, HuggingFace Embeddings

**LLM Providers:** Groq (openai/gpt-oss-120b), Gemini (gemini-2.5-flash-lite)

**Code Execution:** Judge0 API

**Frontend:** Next.js, Tailwind CSS

## Prerequisites

- Python 3.11+
- Node.js
- Groq API key — free at [console.groq.com](https://console.groq.com)
- Google API key — free at [aistudio.google.com](https://aistudio.google.com) (optional, for Gemini)

## Running locally

### Backend

```bash
cd backend
python3 -m venv venv

# Mac/Linux
source venv/bin/activate

# Windows PowerShell
.\venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
```

Create a `.env` file in the backend folder:
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
GROQ_API_KEY=your_key_here
Optional - for Gemini support
GOOGLE_API_KEY=your_key_here

Start the server:

```bash
python -m uvicorn app:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Copy `frontend/env.example` to `frontend/.env.local` and adjust the backend URL
if it is not running on `http://localhost:8000`.

Open `http://localhost:3000`.

## Project Structure
TutorChatAgent/

├── backend/

│   ├── app.py              # FastAPI app, endpoints, streaming

│   ├── requirements.txt

│   └── agent/

│       ├── graph.py        # LangGraph nodes and assessment graph

│       ├── state.py        # TutorState, ChatRequest models

│       ├── prompts.py      # All LLM prompt templates

│       ├── tools.py        # Judge0 code execution, language detection

│       └── rag/

│           ├── indexer.py  # PDF ingestion and ChromaDB indexing

│           └── retriever.py # Semantic search over uploaded documents

└── frontend/

└── app/

├── page.tsx        # Landing page

├── chat/

│   └── page.tsx    # Chat route

└── components/

└── TutorChat.tsx

## Features

- Socratic tutoring with 4-level hint escalation
- Automatic code execution via Judge0 (Python, Java, C++, JavaScript and more)
- PDF upload with RAG — tutor answers based on your actual study material
- Choice of LLM provider (Groq or Gemini) from the landing page
- Streaming responses
- Rate limiting and input validation
