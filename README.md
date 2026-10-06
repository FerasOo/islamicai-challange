# دليلك — Daleel

Daleel is an Arabic evidence workspace for use during a conversation. It retrieves original quotations with references and linked source context; it does not generate religious answers or rulings.

This project was built for the [AI Challenge for Islamic Content (تحدي الذكاء الاصطناعي في خدمة المحتوى الإسلامي)](https://islamicaich.org/). It helps researchers and specialists find and review source evidence during discussions.

## Features

- Search Islamic sources including the Quran, Tafsir Mujahid, Sahih al-Bukhari, Sahih Muslim, hadith explanations, fatwas, fiqh, and terminology.
- See quoted passages, source references, related context, and relevance results. Filter by source group and save useful evidence.
- Type a question or use the microphone for live Arabic transcription. The app can detect when a conversation needs new evidence and start a search automatically.
- Enroll a voice and optionally search only when that speaker talks. Sessions, searches, and saved evidence are kept locally.

## Models and services

- **Jina v5-small retrieval** creates query embeddings locally; **LanceDB** searches the precomputed source embeddings, with source text stored in SQLite.
- **Jev 1.13** checks whether retrieved passages are relevant and classifies their relationship to the question through OpenRouter.
- **Gemini 3.5 Live transcription** converts microphone audio to Arabic text.
- **ECAPA-TDNN** and **Silero VAD** handle local speaker matching and speech detection.

Cloud features require an internet connection and API keys. Voice identification is experimental.

## Setup

Use Python 3.12 and Node.js 22 with npm. From the project root, run this setup once. `npm ci` downloads the frontend packages listed in the lockfile:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r app/requirements.txt
npm ci --prefix app/frontend
```

Create a `.env` file in the project root:

```text
OPENROUTER_API_KEY=your_key
GEMINI_API_KEY=your_key
```

### Retrieval files

The embedding model and precomputed retrieval data are excluded from Git. Building the full embeddings and vector store takes about **2 hours**, so download the processed files instead:

**Download the prepared retrieval files:** [Google Drive](https://drive.google.com/file/d/1gWzOqCqn1MFVx741xizazCkNXw7cab-u/view?usp=sharing)

The ZIP should contain a top-level `retrieval/` folder. Unzip it from the project root (replace the filename with your download):

```sh
unzip daleel-retrieval.zip -d .
```

After extraction, check that these paths exist:

```text
retrieval/models/jina-v5-small-retrieval/
retrieval/processed/corpus.sqlite
retrieval/lancedb/arabic_evidence.lance/
```

Keep the `retrieval/` directory structure shown above; the backend loads files from those exact paths.

Voice matching uses the ECAPA checkpoint files at `app/models/speaker-ecapa/*.ckpt`.

## Run

Open **two terminals** in the project root. In terminal 1, activate the Python environment and start the backend:

```sh
source .venv/bin/activate
python -m uvicorn app.backend.main:app --host 127.0.0.1 --port 8765
```

In terminal 2, start the frontend:

```sh
npm --prefix app/frontend run dev
```

Open **http://127.0.0.1:5173**.
