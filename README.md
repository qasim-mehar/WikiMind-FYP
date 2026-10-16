
# WikiMind AI

WikiMind AI is a personal knowledge and memory system. Users upload documents, the application extracts searchable text, an OpenRouter-hosted language model extracts entities, facts, and relationships, and MongoDB stores the resulting knowledge. Users can then ask questions across all uploaded documents.

The application also supports GPT-style chat sessions. A chat can contain multiple questions and answers, and each chat is titled using its first question.

## What The Project Does

1. Accepts PDF, DOCX, TXT, and Markdown files.
2. Extracts text and splits it into searchable chunks.
3. Sends document text to an OpenRouter model for structured knowledge extraction.
4. Stores documents, chunks, entities, facts, and relationships in MongoDB.
5. Searches document chunks, entities, and relationships when a user asks a question.
6. Sends retrieved context to the AI model with instructions not to invent facts.
7. Stores answered questions in persistent memory.
8. Groups questions by `chatId` so one chat can contain multiple turns.

If AI extraction is temporarily unavailable, the document text is still saved. The upload response reports that the text was saved without AI-extracted knowledge.

## Technology Stack

- Next.js 16 App Router
- React 19
- TypeScript
- MongoDB / MongoDB Atlas
- OpenRouter Chat Completions API
- `pdf-parse` for PDF extraction
- `mammoth` for DOCX extraction
- ESLint

Development uses Webpack because the PDF parser worker is not reliable with the default Turbopack setup:

```text
npm run dev  ->  next dev --webpack
```

## Requirements

- Node.js 20 or newer is recommended.
- A reachable MongoDB Atlas cluster or MongoDB server.
- An OpenRouter API key.
- Network access to MongoDB and `openrouter.ai`.

## Setup

Install dependencies:

```bash
npm install
```

Create `.env` in the project root:

```env
MONGODB_URI=mongodb+srv://USERNAME:PASSWORD@CLUSTER.mongodb.net/?appName=WikiMind
MONGODB_DB=wikimind

OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free

# Optional: Google Drive import
GOOGLE_DRIVE_CLIENT_ID=your_google_oauth_client_id
GOOGLE_DRIVE_CLIENT_SECRET=your_google_oauth_client_secret
GOOGLE_DRIVE_TOKEN_SECRET=replace_with_a_random_secret_at_least_32_characters_long
# Set this to your deployed origin when the app is hosted; local default is http://localhost:3000/api/drive/callback
GOOGLE_DRIVE_REDIRECT_URI=http://localhost:3000/api/drive/callback

```

The AI helper also accepts `GEMINI_API_KEY` as a backward-compatible fallback, but an OpenRouter key should be stored as `OPENROUTER_API_KEY`. Never commit `.env` or expose API keys. Rotate a key immediately if it is exposed.

### Google Drive import

Drive import uses Google's Drive API with read-only OAuth access. Standard Drive API usage is quota-limited and does not require a billing account; do not start a Cloud free-trial billing setup for this integration. In Google Cloud Console, create a project, enable Google Drive API, configure the OAuth consent screen for External/Testing, add your Google account as a test user, and create a Web application OAuth client. Add the callback URL above as an authorized redirect URI. Find the client ID and client secret in **Google Cloud Console > APIs & Services > Credentials > OAuth 2.0 Client IDs**. They are OAuth client credentials, not a Google API key. Configure these and a cryptographically random token secret of at least 32 characters as the `GOOGLE_DRIVE_*` secrets in `.env`. For deployment, set `GOOGLE_DRIVE_REDIRECT_URI` to the exact public callback URL and register the same URL with Google. Users connect their own Google account from the app. Drive access and refresh tokens are encrypted in an HTTP-only cookie and are not stored in MongoDB.

The import panel supports PDF, DOCX, TXT, Markdown, and Google Docs, Sheets, and Slides exports. Individual downloads are limited to 50 MB.

The **Google Research** option opens Google Scholar in a new tab. Copy the URL of a public article or PDF and paste it into WikiMind to add its readable text and source URL to Documents. This does not use Google Search API credentials. Only public HTTPS pages returning HTML or plain text (up to 2 MB) and PDF files (up to 20 MB) are supported; local/private addresses and redirects to them are blocked.

Start the development server:

```bash
npm run dev
```

Open `http://localhost:3000`.

On Windows, use `npm.cmd` if PowerShell cannot resolve `npm`:

```powershell
npm.cmd install
npm.cmd run dev
```

If Next.js has stale generated files after dependency or bundler changes, stop the server and remove `.next` before restarting.

## Commands

```bash
npm run dev      # Start development server with Webpack
npm run build    # Create a production build
npm run start    # Start the production server
npm run lint     # Run ESLint
```

## Application Workflow

### Uploading documents

Supported extensions:

- `.pdf`
- `.docx`
- `.txt`
- `.md`
- `.markdown`
- `.jpg` / `.jpeg`
- `.png`
- `.webp`
- `.gif`

The server extracts text, creates chunks of approximately 1,800 characters, and asks the configured OpenRouter model for JSON containing entities, facts, and relationships. For images, the free multimodal Gemma 4 model runs through OpenRouter JSON mode to create a searchable description and extract supported entities, facts, and relationships. It uses the same `OPENROUTER_API_KEY`; no separate Google Gemini key is required. Images are limited to 20 MB. The image description and extracted knowledge are saved in MongoDB. Relationships and facts are also copied into central collections so they can connect information from different documents.

### Asking questions

When a user asks a question, the server:

1. Normalizes the question and checks whether it was already answered in the active chat.
2. Searches document chunks using word matching.
3. Finds matching entities.
4. Finds relationships whose source or target matches those entities.
5. Builds context from documents, entities, and relationships.
6. Sends the context to OpenRouter.
7. Returns the answer, source documents, and relationship count.

The AI prompt instructs the model to use only supplied knowledge and to say when the documents do not contain enough information.

### Chat sessions

Each active chat has a `chatId`. The browser keeps all turns in the active conversation until **New chat** is clicked. The first question becomes the chat title. The memory API groups stored turns by `chatId` and restores complete chats from the sidebar.

## API Routes

### `POST /api/documents`

Uploads one document as multipart form data. The request field is `file`. The response includes the document ID, chunk count, and extracted knowledge counts. If OpenRouter times out, the document text is still saved.

`DELETE /api/documents?id=...` deletes one document and removes its associated facts, relationships, and entity references.

### `GET /api/documents/list`

Returns uploaded documents without the large `text` and `chunks` fields.

### Google Drive routes

- `GET /api/drive/connect` starts the Google read-only OAuth flow.
- `GET /api/drive/callback` completes OAuth and sets the encrypted Drive token cookie.
- `GET /api/drive/files` lists the connected user's Drive files.
- `GET /api/drive/download?id=...` downloads or exports a supported file for the existing document upload pipeline.
- `DELETE /api/drive/disconnect` clears the local Drive connection cookie.

### Webpage import route

- `POST /api/research` accepts a `{ "url": "https://..." }` body, extracts readable page text as Markdown or streams through a validated PDF. The client saves the result and source URL through `POST /api/documents`.

### `POST /api/ask`

Accepts:

```json
{
	"prompt": "Who is working on WikiMind AI?",
	"chatId": "browser-generated-chat-id"
}
```

Returns an answer, source documents with matching scores, relationship count, and whether the answer came from memory.

### `GET /api/memory`

Returns saved chats grouped by `chatId`. Legacy records without a `chatId` are exposed as individual legacy chats.

### `DELETE /api/memory?chatId=...`

Deletes every saved turn belonging to a chat.

## MongoDB Data Model

Default database:

```text
wikimind
```

Collections:

### `documents`

Stores uploaded files and document-level extraction:

```json
{
	"name": "example.pdf",
	"type": "application/pdf",
	"size": 12345,
	"text": "Extracted document text",
	"chunks": ["Searchable text chunk"],
	"entities": [],
	"facts": [],
	"relationships": [],
	"createdAt": "Date"
}
```

### `entities`

Central entity records shared across documents. Each record contains `name`, `type`, `documentIds`, and `updatedAt`.

### `relationships`

Central graph edges used during question answering:

```json
{
	"source": "WikiMind AI",
	"relation": "USES",
	"target": "MongoDB",
	"evidence": "MongoDB is used as the main database.",
	"documentId": "ObjectId",
	"documentName": "example.pdf",
	"createdAt": "Date"
}
```

### `facts`

Normalized subject-predicate-object records containing `subject`, `predicate`, `object`, document metadata, and `createdAt`.

### `memory`

Stores answered questions and chat turns:

```json
{
	"chatId": "browser-generated-chat-id",
	"title": "The first question in the chat",
	"question": "A question",
	"normalizedPrompt": "normalized question",
	"answer": "The generated answer",
	"sources": [],
	"relationshipsUsed": 2,
	"timesAsked": 1,
	"createdAt": "Date",
	"lastAskedAt": "Date"
}
```

The `relationships` array inside a document is document-level data. The question-answering route primarily reads the central `relationships` collection, so a document can have an empty local array while relationships still appear in answers.

## Project Structure

```text
app/
	page.tsx                    Main client UI and chat state
	globals.css                 Global stylesheet
	layout.tsx                  Root layout
	api/
		ask/route.ts              Search knowledge and answer questions
		documents/route.ts        Upload and process documents
		documents/list/route.ts   List uploaded documents
		memory/route.ts           List and delete chat memory
	lib/
		gemini.ts                 OpenRouter client, retries, and fallback model
		mongodb.ts                MongoDB connection and Atlas DNS handling
public/                       Static assets
questions.txt                 Manual test questions and expected behavior
```

## AI Provider Behavior

The project calls OpenRouter's Chat Completions endpoint. Temporary `429`, `503`, and timeout errors are retried. The configured model is tried first, followed by `openrouter/free` when appropriate.

Free models can be rate-limited or slow. Question answering may return a provider error after retries. Document text extraction is independent of AI extraction: if AI extraction times out, the document is still stored with its text and chunks.

## PDF Notes

The project currently uses `pdf-parse` 1.1.1 because its CommonJS parser works with the current server bundle. PDFs with malformed cross-reference tables can produce `bad XRef entry`; re-save or export those files as new PDFs before uploading them.

## Testing Checklist

Use `questions.txt` to verify:

- Cross-document answers identify Ali and MongoDB correctly.
- Multi-hop relationships connect Ali, WikiMind AI, MongoDB, and the knowledge graph.
- AI extraction identifies entities, facts, concepts, and relationships.
- Unsupported questions receive a no-information answer instead of a hallucination.
- Multiple questions remain visible in one chat.
- **New chat** starts a separate conversation.
- Chat titles use the first question.

Run validation with:

```bash
npm run lint
npm run build
```

## Security And Operational Notes

- Keep API keys and MongoDB credentials in environment variables.
- Restrict MongoDB Atlas network access for the deployment environment.
- Rotate credentials if they are exposed.
- Never put provider keys in client components or browser code.
- The application currently has no authentication or per-user data isolation. Treat it as a private prototype until authentication and authorization are added.

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
