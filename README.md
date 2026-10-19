# RigRAG

RigRAG recommends complete, compatible PC builds from a plain-English request like "70k gaming PC, 1440p, AMD CPU, needs Wi-Fi". Prices are in Philippine pesos.

Most "AI PC builder" demos let a language model pick the parts, which means they sometimes pick parts that don't fit together. RigRAG goes the other way. Every part choice, compatibility check, and price is handled by ordinary, tested Python code backed by a real database. The language model only does two jobs: it turns the request into structured fields, and it explains the build the engine already chose.

## How it works

1. **Parse the request.** The LLM turns the message into a `BuildRequest` (budget, use case, resolution, brand preferences, and so on). If the model is unavailable or returns something invalid, a rule-based parser takes over.
2. **Find candidates.** SQL filters handle hard constraints like price and brand. Semantic search over Qdrant handles fuzzy ones like "quiet" or "good for 1440p". Every result is re-read from Postgres, so a stale vector entry can never leak an old price.
3. **Build and score.** The budget is split into soft per-category bands. A beam search assembles full builds, dropping incompatible combinations as it goes, and each finished build is scored on fit to the use case, value, preferences, upgrade headroom, and power.
4. **Check compatibility.** Rules for CPU socket, RAM type, case clearance, and PSU headroom (25% margin) run once while searching and again on the final build.
5. **Explain.** The LLM gets a compact summary of the chosen build and writes the explanation. It never sees the raw catalog, so it has nothing to make specs up from.

Follow-up questions work in chat. "Why not the Ryzen 7?" goes to a tool-calling loop backed by the same tested code. "What if I add 10k to the budget?" re-runs the engine under the new budget and explains the difference.

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
python -m app.ingestion.embed_and_index  # embed products and index them in Qdrant

uvicorn app.main:app --reload
```

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

**Reference data you maintain by hand:**
- `data/reference/gpu_chips.json`: TDP and tier for each GPU chip, since Icecat lists cards but not chip power. A card on a chip missing from this file is skipped until the chip is added.
- `data/reference/spec_overrides.json`: corrections where Icecat disagrees with the manufacturer's spec page, each with its source URL.

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

`test_compatibility.py`, `test_rag_documents.py`, and the other LLM unit tests run on their own. The rest need the seeded Postgres from the setup steps. The chat tests use a fake LLM, so no model needs to be running.

To measure retrieval quality (Hit@k and MRR on a hand-built query set) against the Qdrant index:

```bash
python -m app.rag.evaluation.run_eval
```

## Project layout

```
backend/app/
├── api/routers/          REST endpoints
├── models/               SQLAlchemy models (Product plus per-category spec tables)
├── services/             Part lookups and chat session state
├── recommendation/
│   ├── compatibility/    Rules, checker, power estimation
│   ├── optimization/     Budget bands, candidate generation, beam search
│   ├── scoring/          Build scoring and performance tiers
│   └── engine.py         Single entry point for recommendations
├── rag/                  Embeddings, Qdrant store, document building, hybrid retrieval, evaluation
├── llm/                  Providers, prompts, intent parsing, explanations, tool calling
└── ingestion/
    ├── sources/          Open Icecat API client
    ├── parsers/          Icecat index file parser
    ├── normalization/    Icecat field mapping, GPU chip lookup, manual overrides
    ├── validation/       Required-field and range checks before import
    ├── prices/           Shopee and Lazada clients, listing matching, price refresh
    └── import_icecat.py  Catalog import command
backend/alembic/          Database migrations
backend/tests/            pytest suite
data/seed/                Curated starter catalog
data/reference/           GPU chip data and manual spec corrections
docker/                   Docker Compose for Postgres, Qdrant, and the backend
```

## Known limitations

- **The seed catalog is small.** 36 parts is enough to exercise every compatibility rule, but with only a few CPUs and GPUs per socket or brand, the "cheaper" and "higher performance" alternatives sometimes land on the same family. Use the Icecat import to grow it.
- **Open Icecat skips some major brands.** Only brands that sponsor Open Icecat are included (ASUS, MSI, Gigabyte, Kingston, Samsung, be quiet!, and Seagate, among others). AMD, Intel, Corsair, Cooler Master, Noctua, Crucial, and Lian Li need paid Full Icecat, so CPUs still come from the seed catalog.
- **Icecat dimensions aren't always right.** Icecat lists the ASUS DUAL-RTX4060-O8G as 200 mm long; ASUS says 227.2 mm. That's corrected in `spec_overrides.json`, but other errors like it may exist, and an under-reported length can let a card pass a case clearance check it should fail.
- **The Lazada client is unverified.** Lazada only shows its affiliate API docs to approved apps, so the search endpoint and response field names in `prices/lazada.py` are best guesses until checked against the real docs. Request signing follows Lazada's standard scheme.
- **Seed prices are estimates.** Prices refreshed from Shopee or Lazada are marked `observed`; seed prices are curated estimates, not live quotes.
- **Icecat terms apply.** Icecat data must be credited as "Specs Icecat" wherever it's shown (product `source` fields carry that credit), and their terms forbid using it to train AI models. Retrieval isn't training, but check the terms before relying on it.
- **Performance tiers and scoring weights are hand-tuned.** They are not derived from benchmarks, so they are only used as a relative signal and never presented as FPS numbers.
- **Evaluation covers retrieval only.** Answer faithfulness isn't measured automatically. An LLM judge would add its own noise.
- **The LLM can still misquote a number in its prose.** One explanation listed a ₱1,800 part as ₱11,800 even though the correct price was in its input. Structured fields like `total_price_php` are always the numbers to trust.
- **Chat sessions live in memory.** They're lost on restart and not shared between workers. A production setup would keep them in Postgres or Redis.
- **Local 8B models are slow on small GPUs.** On a 4GB laptop GPU, `qwen3:8b` splits across GPU and CPU and runs slowly. `qwen3:4b` or Groq is the practical choice there, and the hardware probe recommends accordingly.
- **The Groq retry is minimal.** It retries a rate limit once, but there's no automatic fallback to another provider.
- **There's no frontend yet.** The Docker Compose `backend` service also hasn't been tested in a container. Development so far has run the API from a local venv against the Postgres and Qdrant containers.
