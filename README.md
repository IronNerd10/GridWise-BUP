# GridWise Energy Optimizer

An HTTP API built with FastAPI that optimizes energy management using Linear Programming and dynamically adjusts operational constraints based on operator notes using an LLM.

## Architecture

1. **Optimization/Solver**: The core optimization engine uses `scipy.optimize.linprog` with the `highs` method. It builds a 120-variable matrix (24 hours × 5 variables) to solve the linear programming problem (minimizing cost while maintaining balance and battery constraints).
2. **LLM Interpreter**: Operator notes are parsed using an LLM to extract structured temporal and load constraints.
3. **Guardrails**: All LLM outputs are passed through strict validation (via Pydantic) and logic guardrails to ensure they adhere to valid hours (0-23), positive capacities, and recognized directive types. If the LLM fails or times out, a robust Regex-based heuristic fallback ensures the pipeline never crashes.
4. **Caching**: A thread-safe LRU cache memoizes LLM responses for identical notes, dropping latency to ~0.01s on cache hits.

## Credited Tools
- **FastAPI & Uvicorn**: High-performance HTTP web framework.
- **Pydantic (v2)**: Data validation and request shaping.
- **SciPy (`scipy.optimize.linprog`)**: Advanced LP solving using the HiGHS solver.
- **HTTPX**: Async HTTP client for communicating with the LLM.
- **Groq/OpenAI-compatible APIs**: Used for ultra-fast, structured LLM inference.

## Environment Variables

The application requires the following environment variables (defined in a `.env` file for local development or passed via Docker/Secrets). **DO NOT** commit actual keys.

- `LLM_PROVIDER`: The LLM provider (e.g., `Groq`, `OpenAI`)
- `LLM_MODEL`: The ID of the model to use (e.g., `llama-3.3-70b-versatile` or `gpt-oss-120b`)
- `GROQ_API_KEY`: Your actual Groq API key (or `LLM_API_KEY` for other providers)
- `LLM_BASE_URL`: Base URL for the OpenAI-compatible endpoint (defaults to `https://api.groq.com/openai/v1`)
- `LLM_TIMEOUT_SECONDS`: Request timeout in seconds (default `8`)

See `.env.example` for the template.

## Running Locally

1. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**:
   ```bash
   cp .env.example .env
   # Add your GROQ_API_KEY to .env
   ```

4. **Run the API server**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

## Running the Public-Sample Tests

A robust test suite verifies the optimizer and the LLM interpreter against the provided public sample cases.

```bash
# Run individual tests
python -m tests.test_interpreter
python -m tests.test_paraphrase
python -m tests.test_e2e
python -m tests.test_latency
```

## API Endpoints

### Health Check

Verify the API is running:
```bash
curl http://localhost:8000/health
```

### Optimize Energy

POST to `/optimize-energy` with the scenario configuration. Here is an exact run command you can test:

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

## System Limitations

1. **No State Persistence**: The API is completely stateless and handles each request in isolation. It does not track battery state across different calls or days.
2. **LLM Latency**: The natural language operator note parsing adds network dependency and processing time (p95 latency ~2-3 seconds depending on the provider).
3. **Linear Programming Complexity**: The optimizer uses a fixed 120-variable matrix (24 hours x 5 variables). Extremely large continuous horizons (e.g. multi-month optimization) would require scaling the solver strategy.
4. **Hardcoded Fallbacks**: If the LLM path fails entirely (timeout or rate limit), the system falls back to regex-based heuristics which only cover common vocabulary. Edge cases falling out of those bounds will result in a "no-op" directive.
