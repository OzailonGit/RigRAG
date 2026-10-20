# RigRAG

RigRAG recommends complete, compatible PC builds from a plain-English request like "70k gaming PC, 1440p, AMD CPU, needs Wi-Fi". Prices are in Philippine pesos.

Most "AI PC builder" demos let a language model pick the parts, which means they sometimes pick parts that don't fit together. RigRAG goes the other way. Every part choice, compatibility check, and price is handled by ordinary, tested Python code backed by a real database. The language model only does two jobs: it turns the request into structured fields, and it explains the build the engine already chose.

## How it works

1. **Parse the request.** The LLM turns the message into a `BuildRequest` (budget, use case, resolution, brand preferences, and so on). If the model is unavailable or returns something invalid, a rule-based parser takes over.
2. **Find candidates.** SQL filters pick each part's candidates from structured specs: price, brand, socket, memory type, form factor, Wi-Fi, PSU wattage, and benchmark speed. Semantic search over Qdrant isn't part of build selection. It backs `/api/search` and the chat's part lookups ("a short GPU for a small case"), with metadata filters for category, price, and manufacturer, and every hit is re-read from Postgres so a stale vector entry can never leak an old price. Preferences the catalog has no data for, like `quiet` (there are no noise figures), are parsed but don't change the build.
3. **Build and score.** The budget is split into soft per-category bands. A beam search assembles full builds, dropping incompatible combinations as it goes, and each finished build is scored on fit to the use case, measured speed, performance per peso, preferences, upgrade headroom, and power. CPU and GPU speed come from published benchmarks (Tom's Hardware's hierarchies): GPUs by rasterization at the requested resolution, CPUs by gaming or multithreaded score depending on the use case, and their tiers follow from those scores. A build that uses less than 85% of the budget is retried with higher CPU and GPU tiers, and the reply says why when budget is still left over. Alternatives are separate runs: a build at least 5% (and ₱2,000) cheaper, the next tier up when it costs up to 20% over budget and is at least 5% faster, and a noticeably more upgradeable one. Asking for "the fastest CPU" (or GPU) sets `favor`: the engine tries that part from fastest down and keeps the first one a complete in-budget build fits around, as long as the other part still meets the request's need, and the reply compares it with the balanced build.
4. **Check compatibility.** Rules for CPU socket, RAM type, case clearance, and PSU headroom (25% margin) run once while searching and again on the final build.
5. **Explain.** The LLM gets a compact summary of the chosen build and writes the explanation. It never sees the raw catalog, so it has nothing to make specs up from.

Follow-up questions work in chat. "Why not the Ryzen 7?" goes to a tool-calling loop backed by the same tested code. "What if I add 10k to the budget?" re-runs the engine under the new budget and explains the difference.

Each chat keeps its last six messages and the parts follow-up answers named, so "switch to it" resolves to a real product ID. That part is pinned (`pinned_parts` in the `BuildRequest`): the engine builds the rest around it, counts its price against the budget, and the reply compares the old and new builds using the engine's totals. If a request doesn't say what the PC is for, the chat asks once before building, then sizes the build for general use if there's still no answer.

## Tech stack

- **API:** FastAPI
- **Database:** PostgreSQL 16, SQLAlchemy, Alembic
- **Vector search:** Qdrant with `BAAI/bge-small-en-v1.5` embeddings (local, via sentence-transformers)
- **LLM:** Ollama (local, default) or any OpenAI-compatible API (Groq is pre-configured)

## Getting started

You need Python 3.11+, Docker, and either Ollama or a free Groq API key.

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml up -d postgres qdrant

cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m alembic upgrade head
python -m app.ingestion.seed             # load data/seed/*.json into Postgres
python -m app.ingestion.datasets.build   # optional: download and merge the public datasets
python -m app.ingestion.import_datasets  # optional: add them to the catalog (~25,000 parts)
python -m app.ingestion.import_benchmarks  # CPU and GPU benchmark scores (import_datasets also runs this)
python -m app.ingestion.embed_and_index  # embed products and index them in Qdrant

uvicorn app.main:app --reload
```

Then, in a second terminal, start the chat UI (needs Node 20+):

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to the backend on :8000
```

To run everything in containers instead (API on :8000, UI on :8080; set `BACKEND_PORT` or `FRONTEND_PORT` to move them):

```bash
docker compose -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml exec backend python -m alembic upgrade head
docker compose -f docker/docker-compose.yml exec backend python -m app.ingestion.seed
```

The backend image is built from the repo root, since it needs `data/reference` and `data/seed`; Compose also mounts `backend/` and `data/` so code and data changes apply without a rebuild. Inside the container, Ollama is reached at `host.docker.internal` (override with `OLLAMA_BASE_URL_DOCKER`).

The UI keeps conversations in the browser's local storage and shows each recommended build as a parts table, with tabs for the alternatives and the compatibility warnings underneath.

### Growing the catalog

The seed catalog is small. Real products come from [Open Icecat](https://icecat.biz) (specs) and Shopee and Lazada (prices).

**Specs (Open Icecat).** Single products work with the built-in demo account:

```bash
python -m app.ingestion.import_icecat --product ASUS:90YV0JC0-M0NA00 --gtin 4711387223734
```

Importing whole categories reads Icecat's index file, which needs a free account ([register here](https://icecat.biz/registration)). Set `ICECAT_USERNAME` and `ICECAT_PASSWORD`, then:

```bash
python -m app.ingestion.import_icecat --index --categories gpu,motherboard --country PH --limit 500
python -m app.ingestion.import_icecat --index --categories gpu --dry-run   # see what would be skipped and why
```

Every datasheet is mapped, checked against plausible ranges, and skipped with a reason if a field the compatibility rules need is missing. Imported parts have no price yet, so they show up in listings but are never recommended.

**Prices (Shopee and Lazada).** Needs affiliate API credentials for at least one of them (see `.env.example`):

```bash
python -m app.ingestion.prices.refresh --only-missing --dry-run
python -m app.ingestion.prices.refresh --category gpu --limit 50
```

Each product is searched by name, and listings are kept only if they match the brand, model numbers, and capacity, and aren't used, refurbished, or bundled. Every accepted listing is stored in `price_observations`. The product's price becomes the median, using official stores (Shopee Mall, LazMall) when there are any.

**Combined public datasets.** One command downloads ten public PC-part datasets (official AMD/Intel specs, Wikipedia and TechPowerUp GPU tables, two PCPartPicker dumps, and others) and merges them into one file per part type:

```bash
python -m app.ingestion.datasets.build
```

Output goes to `data/combined/` (CSV and JSONL for each part type, plus `manifest.json` with every source's license). Rows that appear in several datasets are merged, and each field takes the value most sources agree on. Every row lists its `sources`, any `conflicts` between them, and `derived_fields` that were inferred rather than stated. The folder is gitignored because the sources have mixed licenses.

To load the combined files into the catalog:

```bash
python -m app.ingestion.import_datasets --dry-run          # counts, plus why rows were skipped
python -m app.ingestion.import_datasets
python -m app.ingestion.import_datasets --priced-only --eur-php 66 --usd-php 57
```

That adds about 18,800 parts on current platforms (AM4, AM5, LGA1200, LGA1700, LGA1851), around 6,800 of them with a price. A few rules decide what gets in:

- **What's skipped.** Server and laptop parts, DDR3 and older, SFX PSUs, M.2 SATA drives, and GPUs on chips missing from `gpu_chips.json`.
- **Prices.** They're estimates converted from the PCPartPicker listings in the datasets. The French euro price (May 2026) is used when there is one, otherwise the US dollar price (July 2025), and both are adjusted to Philippine VAT. A listing more than 1.5x the part's launch MSRP is dropped, since that's usually leftover stock.
- **Re-runs.** Specs and estimated prices update in place. A price observed from Shopee or Lazada is never overwritten.
- **Links.** Each part links to its PCPartPicker product page, and seed or Icecat parts borrow the link of a dataset row with the same name. A price refresh replaces the link with the Shopee or Lazada listing priced closest to the quoted median. Links point at the main pcpartpicker.com site, rewritten from the French listings the datasets came from. Seed parts named generically (an "NVIDIA RTX 4060" rather than a specific card, or a RAM kit sold in several latencies) have no link. Generic seed GPUs are left out of builds once the catalog has priced cards on the same chip and memory size.
- **Board slots.** No dataset lists SATA ports, and few list M.2 slots, so boards get a floor of 1 M.2 slot and 2 SATA ports. Those fields are recorded in `assumed_fields`, and the search documents say "at least".
- **Cases and coolers.** The datasets don't list case clearances or cooler sockets. The 100 most popular models get them from `case_specs.json` and `cooler_specs.json` (below). Every other tower case and cooler is imported **unverified**. The rules can't check those parts, so they return a warning telling the user what to confirm instead of passing or failing them, and both the beam search and final scoring penalize each unverified check. Desktop, slim, and small ITX cases without verified specs are skipped, since they may need an SFX PSU.

**Reference data you maintain by hand:**
- `data/reference/gpu_chips.json`: TDP and tier for each GPU chip, since Icecat lists cards but not chip power. A card on a chip missing from this file is skipped until the chip is added.
- `data/reference/spec_overrides.json`: corrections where Icecat disagrees with the manufacturer's spec page, each with its source URL.
- `data/reference/benchmarks.json`: relative benchmark scores for 48 GPUs (rasterization at 1080p, 1440p, and 4K, as a percentage of the RTX 5090) and 95 CPUs (1080p gaming and multithreaded, as a percentage of the fastest CPU tested), from Tom's Hardware's GPU and CPU hierarchies. Older CPUs' gaming scores come from the previous hierarchy, rescaled by the CPUs both tables test, and are marked `rescaled`. `import_benchmarks` matches GPUs by chip and VRAM and CPUs by model (Intel F and KF chips share the plain chip's score). An unbenchmarked part gets the lowest score of its tier and is marked `benchmark_estimated`.
- `data/reference/chipsets.json`: the PCIe generation of the graphics slot and CPU-attached M.2 slot for each AMD chipset, from AMD's AM5 and AM4 spec tables (B650 and B650E from a Corsair comparison, since AMD's table no longer lists them). Board descriptions state these so explanations don't guess. Intel chipsets are left out: on LGA1700 and LGA1851 the slot's generation depends on the board, and descriptions say so.
- `data/reference/case_specs.json` and `cooler_specs.json`: GPU and cooler clearances, radiator mounts, supported sockets, and cooler heights for 48 cases and 52 coolers, matched to dataset rows by name and aliases. Each entry has a source URL and a `source_kind` of `manufacturer` or `retailer`; the second marks values taken from retailer or review listings that agreed with each other when the manufacturer page couldn't be read. Where a figure depends on configuration, the conservative one is used.

### Choosing an LLM

**Groq (easiest).** No local model download needed. Get a key at https://console.groq.com/keys and set:

```
LLM_PROVIDER=groq
GROQ_API_KEY=<your key>
GROQ_MODEL=openai/gpt-oss-120b
```

Groq rotates its free model lineup now and then. If you get `model_not_found`, list the current models with `GET https://api.groq.com/openai/v1/models`.

**Ollama (fully local).** To see which model your machine can handle:

```bash
cd backend
python -c "from app.llm.hardware_probe import probe_hardware, recommend_model; p = probe_hardware(); print(p); print(recommend_model(p))"
```

The probe checks system RAM and, on NVIDIA cards, VRAM, then suggests `qwen3:4b`, `qwen3:8b`, or `qwen3:30b-a3b`. Pull that model with `ollama pull <model>` and set `LLM_PROVIDER=ollama` and `OLLAMA_MODEL` in `.env`.

**Anything else.** Set `LLM_PROVIDER=custom` with `CUSTOM_LLM_BASE_URL`, `CUSTOM_LLM_API_KEY`, and `CUSTOM_LLM_MODEL` to use LM Studio, OpenRouter, or any other OpenAI-compatible endpoint.

## API

| Method | Endpoint | What it does |
| --- | --- | --- |
| GET | `/api/health` | Health check |
| GET | `/api/parts?category=gpu&max_price_php=25000` | List parts with filters |
| GET | `/api/parts/{id}` | One part with full specs |
| POST | `/api/search` | Semantic search, e.g. `{"query": "good 1440p GPU with lots of VRAM", "category": "gpu"}` |
| POST | `/api/compatibility` | Check a set of part IDs against the compatibility rules |
| POST | `/api/compare` | Compare two or more parts by ID |
| POST | `/api/build` | Recommend a build, e.g. `{"budget_php": 70000, "use_case": ["gaming"], "resolution": "1440p"}`. Add `?explain=true` for a written explanation. |
| POST | `/api/chat` | Conversational builder, e.g. `{"session_id": "abc123", "message": "Build me a 70k gaming PC"}` |

## Tests and evaluation

```bash
cd backend
python -m pytest tests/ -v
```

`test_compatibility.py`, `test_rag_documents.py`, and the other LLM unit tests run on their own. The rest need the seeded Postgres from the setup steps. The chat tests use a fake LLM, so no model needs to be running. A few tests need the full dataset catalog and skip themselves on the seed catalog.

CI (`.github/workflows/ci.yml`) runs on every push: the backend tests against a fresh Postgres with the seed catalog and benchmark scores, the frontend lint and build, and a build of both Docker images.

**Retrieval evaluation.** 25 queries across every part category, each with a relevance rule written by hand over the part's specs ("AM5 socket and built-in Wi-Fi") and checked against the results it accepts and rejects. It reports precision@5, Hit@5, MRR, and search latency against the Qdrant index:

```bash
python -m app.rag.evaluation.run_eval            # add --verbose to see every result judged
```

On the full catalog (2026-10-20): precision@5 50%, Hit@5 17/25, MRR 0.56, about 110ms per query. Structured needs that the text embeddings can't tell apart are where it fails: "AM5" returns Intel boards, "integrated graphics" returns CPUs without it, and "power efficient" returns 300W cards. Parts missing the spec a rule needs (no read speed, no cooler height) count as misses.

**Build evaluation.** 18 requests from ₱25k to ₱600k across gaming, editing, rendering, AI, and brand, Wi-Fi, small form factor, favored-part, and owned-part constraints. Every build shown must be compatible and on budget, and the engine must answer within 3 seconds; each case also sets the lowest CPU and GPU tier its budget can buy, checked by pricing those parts in the catalog. It exits non-zero on any failure:

```bash
python -m app.recommendation.evaluation.build_eval
```

On the full catalog: 16/18 pass, median 0.9s per request. The two failures are real: the ₱25k and ₱30k builds use a dual-core Athlon 3000G, because CPUs like the Ryzen 5 5600 have no benchmark yet and rate entry tier, and the engine always adds a graphics card where an APU alone would do. Times are for the engine; chat replies add the language model's time.

## Project layout

```
backend/app/
├── api/routers/          REST endpoints
├── models/               SQLAlchemy models (Product plus per-category spec tables)
├── services/             Part lookups and chat session state
├── recommendation/
│   ├── compatibility/    Rules, checker, power estimation
│   ├── optimization/     Budget bands, candidate generation, beam search
│   ├── scoring/          Build scoring, performance tiers, benchmark speed
│   ├── evaluation/       Build evaluation set and runner
│   └── engine.py         Single entry point for recommendations
├── rag/                  Embeddings, Qdrant store, document building, hybrid retrieval, retrieval evaluation
├── llm/                  Providers, prompts, intent parsing, explanations, tool calling
└── ingestion/
    ├── sources/          Open Icecat API client
    ├── parsers/          Icecat index file parser
    ├── normalization/    Icecat and dataset field mapping, GPU chip lookup, manual overrides
    ├── validation/       Required-field and range checks before import
    ├── prices/           Shopee and Lazada clients, listing matching, price refresh
    ├── datasets/         Public dataset downloads and merging (build.py)
    ├── import_icecat.py  Catalog import from Open Icecat
    ├── import_benchmarks.py
    └── import_datasets.py
                          Catalog import from the merged public datasets
backend/alembic/          Database migrations
backend/tests/            pytest suite
data/seed/                Curated starter catalog
data/reference/           GPU chip data, benchmark scores, and manual spec corrections
docker/                   Docker Compose for Postgres, Qdrant, and the backend
frontend/                 React + Vite chat UI (components in src/components/, tokens in src/index.css)
```

## Known limitations

- **Most cases and coolers are unverified.** Only the 100 models in the reference files (plus the seed parts) have checked clearances and sockets. Builds prefer them, but an unverified part can still win when it's much cheaper, and then the build carries a warning to check it by hand. 44 of the 100 reference entries come from retailer listings rather than the manufacturer's own page.
- **Benchmarks cover most GPUs but few budget CPUs.** 865 of 905 priced GPUs have measured scores, but only about 40 of 149 priced CPUs have both a gaming and a multithreaded score: budget chips like the Ryzen 5 5600 and 7500F and the Core i3-12100F aren't in Tom's tables, so they get their tier's lowest score. The scores are relative to one review suite and one test system, so a CPU's gaming score reflects 1080p with an RTX 5090, where CPU differences are largest. See TODO.md.
- **The top tier is still a ceiling.** A ₱600,000 gaming build stops at about ₱216,000 (a Ryzen 7 9850X3D and an RTX 5090): nothing faster exists, and the reply says so.
- **Open Icecat skips some major brands.** Only brands that sponsor Open Icecat are included (ASUS, MSI, Gigabyte, Kingston, Samsung, be quiet!, and Seagate, among others). AMD, Intel, Corsair, Cooler Master, Noctua, Crucial, and Lian Li need paid Full Icecat, so CPUs still come from the seed catalog.
- **Icecat dimensions aren't always right.** Icecat lists the ASUS DUAL-RTX4060-O8G as 200 mm long; ASUS says 227.2 mm. That's corrected in `spec_overrides.json`, but other errors like it may exist, and an under-reported length can let a card pass a case clearance check it should fail.
- **The Lazada client is unverified.** Lazada only shows its affiliate API docs to approved apps, so the search endpoint and response field names in `prices/lazada.py` are best guesses until checked against the real docs. Request signing follows Lazada's standard scheme.
- **Most prices are estimates.** Seed prices are curated estimates. Dataset prices are converted from European and US listings, so they don't reflect local markups or availability. Only prices refreshed from Shopee or Lazada (marked `observed`) are local quotes.
- **Icecat terms apply.** Icecat data must be credited as "Specs Icecat" wherever it's shown (product `source` fields carry that credit), and their terms forbid using it to train AI models. Retrieval isn't training, but check the terms before relying on it.
- **Scoring weights and tier thresholds are hand-tuned.** The scores are measured, but how much speed, value, and fit each count for, and where one tier ends, are judgment calls. Benchmark percentages are relative, never presented as FPS.
- **Explanations aren't evaluated.** The evaluations cover retrieval and the engine's builds; whether a reply describes its build faithfully isn't measured automatically. An LLM judge would add its own noise.
- **Every build gets a graphics card.** A general-use build that an APU alone would serve still pays for a discrete GPU.
- **The LLM can still misquote a number in its prose.** One explanation listed a ₱1,800 part as ₱11,800 even though the correct price was in its input. Structured fields like `total_price_php` are always the numbers to trust.
- **Chat sessions are never cleaned up.** Each one is a row in `chat_sessions`, kept indefinitely.
- **Chat memory is short.** Only the last six messages are kept, with long replies cut to their first 800 characters, so "the GPU you mentioned earlier" can stop resolving after a few turns. Parts are only remembered when a follow-up answer names one it looked up, so a part the model names from its own knowledge can't be pinned.
- **Local 8B models are slow on small GPUs.** On a 4GB laptop GPU, `qwen3:8b` splits across GPU and CPU and runs slowly. `qwen3:4b` or Groq is the practical choice there, and the hardware probe recommends accordingly.
- **The Groq retry is minimal.** It retries a rate limit once, but there's no automatic fallback to another provider.
- **Replies arrive all at once.** The chat API doesn't stream, so a slow local model shows a waiting indicator for up to a minute before the reply appears.
- **CI tests the seed catalog only.** The dataset import needs files that aren't in the repo, so the evaluations, which need the full catalog, run locally rather than in CI.
