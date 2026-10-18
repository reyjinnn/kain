# SISA Atelier

Leftover fabric, no plan, an afternoon to spare. **SISA Atelier** walks you from a story about what you want to make to a pouch you can actually sew: an AI design partner drafts the requirements, a geometry engine proves the pattern fits the cloth you own, a rotatable 3D preview shows the result, and a validated A4 pattern plus illustrated assembly guide comes out the other side. AI suggests, code decides — every pass or fail comes from geometry, not from a model's opinion.

The frontend interface is in English, with four stages: **Story, Fabric, Design, and Guide**. Backend-generated explanations, AI replies, and PDF contents may still be in Indonesian; user-entered text is preserved.

## Features

- **AI-assisted design:** describe what you want to make, review the interpreted requirements, and request changes through chat.
- **Fabric inventory:** use sample materials or enter your own fabric dimensions, shapes, colors, and defect locations.
- **Validated patterns and layouts:** code checks geometry, fabric containment, defects, clearance, piece counts, rotations, and revision consistency. AI does not decide whether a pattern passes validation.
- **Design previews:** inspect a rotatable Three.js model, with an SVG fallback when WebGL is unavailable.
- **AI illustrations and image-based previews:** generate an illustration and reconstruct an approximate textured pouch model locally using OpenCV. Reconstruction does not require a separate 3D service or GPU.
- **PDF exports:** download illustrated assembly guides, technical reports, and full-scale A4 vector patterns with a 50 mm calibration square.
- **Local project storage:** autosave work and save named projects in SQLite, separated by browser identity.

## Technology

| Area | Technologies |
| --- | --- |
| Backend | Python, FastAPI, Uvicorn, Pydantic |
| Geometry and layout | Shapely and project-specific validation code |
| PDF generation and previews | ReportLab and PyMuPDF |
| Image processing | Pillow, NumPy, OpenCV |
| Frontend | JavaScript modules, CSS, Three.js |
| Storage | SQLite |
| AI integration | 9Router's OpenAI-compatible chat and image APIs |

## Running locally

### Prerequisites

- Python 3.10 or newer, with `pip` and `venv`.
- A modern browser. WebGL is recommended for interactive 3D previews.
- A running 9Router instance with a connected provider, an API key, and access to suitable text and image models for AI features.
- Node.js and npm only if you want to install frontend development tools or run browser checks. They are not required to serve the bundled frontend.

Run the following commands from the project root unless stated otherwise.

### 1. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS / Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install backend dependencies

```sh
python -m pip install -r backend/requirements.txt
```

### 3. Configure the AI connection

Start 9Router and connect your provider in its dashboard. Copy the example configuration to the location read by the current application configuration loader.

Windows PowerShell:

```powershell
Copy-Item .env.example .env.local
```

macOS / Linux:

```sh
cp .env.example .env.local
```

Edit `.env.local` and replace the placeholders with your router settings:

```dotenv
NINEROUTER_URL=http://127.0.0.1:20128/v1
NINEROUTER_KEY=<text-api-key>
NINEROUTER_IMAGE_KEY=
SISA_TEXT_MODEL=your-text-model
SISA_IMAGE_MODEL=your-image-model
SISA_AI_RESPONSE_FORMAT=prompt
SISA_AI_DIAGNOSTICS=0
SISA_DATA_DIR=
```

| Variable | Purpose |
| --- | --- |
| `NINEROUTER_URL` | OpenAI-compatible API base URL. The local router address above is the default. Remote endpoints must use HTTPS. |
| `NINEROUTER_KEY` | API key for text requests. |
| `NINEROUTER_IMAGE_KEY` | Optional separate image API key. When empty, the text key is used. |
| `SISA_TEXT_MODEL` | Text model or combo ID available to your key. Takes precedence over the legacy `SISA_AI_MODEL` setting. |
| `SISA_IMAGE_MODEL` | Image model or combo ID available to your image key. |
| `SISA_AI_RESPONSE_FORMAT` | `prompt`, `json_object`, or `json_schema`. Start with `prompt`; use structured modes only after checking provider support. |
| `SISA_AI_DIAGNOSTICS` | Leave at `0` for normal use. Setting it to `1` enables local diagnostic logging that can include user stories and redacted response excerpts. |
| `SISA_DATA_DIR` | Optional custom directory for local application data. Leave empty to use the default. |

The example file uses `json_object`; the configuration above uses `prompt` for providers whose structured-output support has not been checked. Model names must match your router configuration; placeholder values will not work.

Settings are loaded at server startup. Restart the server after editing them. Existing process environment variables take precedence over file values.

**Keep API keys private.** They are read by the backend and are not sent to the browser. Do not commit or distribute `.env.local` files. The application and maintenance scripts read `.env.local` from the project root.

### 4. Start the server

```sh
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001>. FastAPI serves both the API and the frontend; no separate frontend development server is needed. Stop the server with `Ctrl+C`.

## Using the application

1. **Story:** describe the pouch you need, approve sending information to the AI provider, and review the interpreted requirements before applying them.
2. **Fabric:** choose sample fabric or enter your own materials. Check dimensions, colors, and defects, then confirm the measurements. A split front panel requires separate approval.
3. **Design:** review the validated layout and 3D preview. Request changes or generate an AI illustration if the image model is configured.
4. **Guide:** preview and download the PDF guide or technical export. Print patterns at **100% / actual size**, not “fit to page,” and measure the 50 mm calibration square before cutting.

AI proposals can be reviewed and undone. If a request fails, existing inputs are retained rather than replaced with a fabricated proposal.

## Local data and privacy

- Each browser receives a random `sisa_sid` cookie. This is a local browser identity, not a user account. Tabs in the same browser share work; another browser or device does not automatically have access.
- Clearing cookies can remove access to previously saved work. There is no cloud synchronization or full project history.
- The default SQLite database is `%LOCALAPPDATA%/SISA/data/sisa.sqlite3` on Windows, or `~/.local/share/SISA/data/sisa.sqlite3` when `LOCALAPPDATA` is unavailable. Set `SISA_DATA_DIR` to choose another location.
- Inactive project data is retained for 30 days. Up to 20 named projects can be saved per browser identity.
- AI consent controls sending information to the configured provider. Optional activity logging is separate and disabled by default; it records a random research ID, event names, and server timestamps rather than chat contents or fabric photos.

## Development checks

Install the development dependencies:

```sh
python -m pip install -r backend/requirements-dev.txt
cd frontend
npm ci
npx playwright install chromium
```

From the project root, discover backend tests in their current directory:

```sh
python -m unittest discover -s backend/tests -p "test_*.py"
```

Frontend check commands are listed in `frontend/package.json`, including `npm run lint`, `npm run typecheck`, and `npm run test:browser`. The combined entry point is `python scripts/checks/test_all.py`.

Automated checks use mocked AI responses or disable live credentials. Live provider requests are separate, require valid configuration, and may incur usage charges.

## Project structure

```text
backend/
  app.py                FastAPI application and API routes
  config.py             Local environment and settings loader
  design/               Geometry engine, palette, templates, and assembly catalog
  services/             AI integration, chat intent handler, and 3D image model
  pdf/                  Validated PDF assembly guides and technical exports
  sessions/             Browser sessions and local persistence
  tests/                Backend unit and integration tests
frontend/
  index.html            Application page
  src/
    app.js              Application entry point
    config/             Palette and English interface copy
    core/               API client, shared state, and DOM helpers
    features/           Chat, materials, PDF, projects, and onboarding
    preview/            SVG diagrams, 3D preview, and image model
  styles/               Stylesheets
  assets/               Fonts and other application assets
  vendor/               Bundled runtime libraries
  test/                 Frontend tests
scripts/
  checks/               Frontend checks and combined test runner
  diagnostics/          AI request reproduction, live smoke checks, and color calibration
  generators/           PDF examples and parity fixture generation
  release/              Scanned release archive and shell entry point
  security/             Secret scanning and local router key rotation
references/             Product documentation and validation protocols
reports/                Implementation reports and requirement audits
.env.example            Configuration template without credentials
```

## Prototype limitations

- Patterns, layouts, piece lists, and PDFs are tied to a checked revision, but software validation does not certify sewing construction or real-world fit.
- The 3D model is a visual preview, not a fabric simulation. Image-based reconstruction estimates depth and hidden surfaces; physical dimensions are not recovered from an illustration.
- AI illustrations and color checks are approximate, not calibrated measurements of real fabric.
- Physical print measurements, paper assembly, construction review, fabric sewing trials, and participant usability studies remain unverified. Assemble a paper trial before using fabric.
- This project is intended for local prototyping, not deployment as a public multi-user service.

