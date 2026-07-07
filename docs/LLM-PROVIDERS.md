# LLM Provider Configuration

Errander-AI works with any OpenAI-compatible API. Set `ERRANDER_LLM_BASE_URL` and
`ERRANDER_LLM_MODEL` (or `llm.model` in `settings.yaml`) to point at your provider.

After configuring, verify with:

```bash
uv run python -m errander --check-llm
```

> **Tool/function calling** is required only for the opt-in agentic investigation loop
> (`--ask --agentic`, default OFF). The scheduled maintenance batch, the planning note,
> reports, and the default `--ask` use plain structured completions and work on any
> OpenAI-compatible endpoint. If an endpoint/model does not support tool calling, the
> agentic loop detects it and falls back to the deterministic fixed-context path — it
> never errors. For self-hosted vLLM, enable it at serve time with
> `--enable-auto-tool-choice --tool-call-parser hermes` (already in the reference command).

---

## Option A: Self-hosted vLLM (Qwen3-8B-AWQ)

**When to pick:** Privacy-first, no data leaves your VPN, lowest per-token cost at scale.
Requires a GPU VM (Tesla T4 or better, 16 GB VRAM).

```env
ERRANDER_LLM_BASE_URL=http://10.0.1.5:8000/v1
ERRANDER_LLM_MODEL=Qwen/Qwen3-8B-AWQ
# ERRANDER_LLM_API_KEY is not required for unauthenticated vLLM
```

```yaml
# settings.yaml
llm:
  model: "Qwen/Qwen3-8B-AWQ"
  temperature: 0.1
  timeout_seconds: 60   # T4 is slower than cloud APIs
  max_retries: 2
```

vLLM serve command (see `deploy/vllm/docker-compose.yml`):

```bash
vllm serve Qwen/Qwen3-8B-AWQ \
  --reasoning-parser deepseek_r1 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --max-model-len 8192 --gpu-memory-utilization 0.85
```

---

## Option B: Ollama (local, CPU/GPU)

**When to pick:** Privacy-first, no GPU required (runs on CPU), easiest local setup.
Slower than vLLM but zero infrastructure overhead.

```env
ERRANDER_LLM_BASE_URL=http://localhost:11434/v1
ERRANDER_LLM_MODEL=llama3.2
ERRANDER_LLM_API_KEY=ollama
```

```yaml
llm:
  model: "llama3.2"
  temperature: 0.1
  timeout_seconds: 120  # CPU inference is slow
  max_retries: 1
```

Start Ollama:

```bash
ollama serve
ollama pull llama3.2
```

---

## Option C: OpenAI

**When to pick:** Best ecosystem support, reliable latency, easiest to get started.
Data leaves your network — check your compliance requirements.

```env
ERRANDER_LLM_BASE_URL=https://api.openai.com/v1
ERRANDER_LLM_MODEL=gpt-4o-mini
ERRANDER_LLM_API_KEY=sk-...
```

```yaml
llm:
  model: "gpt-4o-mini"
  temperature: 0.1
  timeout_seconds: 30
  max_retries: 2
```

`gpt-4o-mini` is the recommended default — low cost, fast, sufficient for structured JSON.
Use `gpt-4o` for higher-quality failure analysis on complex incidents.

---

## Option D: Anthropic (Claude)

**When to pick:** Highest quality reasoning, strong structured output. Uses Anthropic's
OpenAI-compatible endpoint. Data leaves your network.

```env
ERRANDER_LLM_BASE_URL=https://api.anthropic.com/v1
ERRANDER_LLM_MODEL=claude-haiku-4-5-20251001
ERRANDER_LLM_API_KEY=sk-ant-...
```

```yaml
llm:
  model: "claude-haiku-4-5-20251001"
  temperature: 0.1
  timeout_seconds: 30
  max_retries: 2
```

Use `claude-sonnet-4-6` for higher quality at higher cost.

**Prefix caching:** Errander-AI automatically attaches `cache_control` breakpoints to the stable system-prompt content block when `ERRANDER_LLM_BASE_URL` contains `anthropic.com`. This enables Anthropic's prompt prefix caching — no extra configuration needed.

---

## Option E: Groq

**When to pick:** Fastest inference of any cloud provider (LPU hardware), very low cost.
Good choice when latency matters and you're comfortable with cloud APIs.

```env
ERRANDER_LLM_BASE_URL=https://api.groq.com/openai/v1
ERRANDER_LLM_MODEL=llama-3.3-70b-versatile
ERRANDER_LLM_API_KEY=gsk_...
```

```yaml
llm:
  model: "llama-3.3-70b-versatile"
  temperature: 0.1
  timeout_seconds: 15   # Groq is very fast
  max_retries: 2
```

---

## Option F: Azure AI Foundry

**When to pick:** You already have an Azure subscription with Foundry credit. Lets you A/B
different models (gpt-4o-mini, Llama-3.3-70B, Phi-4, Mistral-Large, DeepSeek, etc.) behind
the same agent code to compare cost vs. output quality. Data stays in your Azure tenant.

Foundry exposes three endpoint shapes. The agent uses the stock OpenAI SDK — pick the URL
form that matches your resource type.

### F1a: New Foundry project endpoint *(recommended — created via ai.azure.com)*

Projects created in the Azure AI Foundry portal use this format:

```env
ERRANDER_LLM_BASE_URL=https://<hub>.services.ai.azure.com/api/projects/<project>/v1/
ERRANDER_LLM_MODEL=<your-deployment-name>
ERRANDER_LLM_API_KEY=<key from project Settings → API keys>
```

Find these at: [ai.azure.com](https://ai.azure.com) → your project → **Settings → API keys**.
The endpoint is shown on the project overview page — copy it verbatim and add a trailing `/`.

### F1b: Classic Azure OpenAI resource *(Azure portal → Azure OpenAI service)*

Older resources created directly in the Azure portal use this format:

```env
ERRANDER_LLM_BASE_URL=https://<your-resource>.cognitiveservices.azure.com/openai/v1/
ERRANDER_LLM_MODEL=<your-deployment-name>
ERRANDER_LLM_API_KEY=<key from "Keys and Endpoint" blade>
```

Find these at: Azure portal → your **Azure OpenAI** resource → **Keys and Endpoint**.

```yaml
# settings.yaml (same for F1a and F1b)
llm:
  model: "<your-deployment-name>"   # the deployment name you chose, NOT the model ID
  temperature: 0.1
  timeout_seconds: 30
  max_retries: 2
```

Notes:
- `ERRANDER_LLM_MODEL` must be the **deployment name** you set in Foundry, not the
  underlying model (`gpt-4o-mini`).
- The trailing slash on the base URL matters for both formats.
- Recommended starter deployment: `gpt-4o-mini` — cheap, fast, sufficient for structured JSON.
  Move to `gpt-4o` or `gpt-4.1` if reasoning quality on incident analysis is too low.

### F2: Foundry serverless / Models-as-a-Service (Llama, Phi, Mistral, DeepSeek, Cohere)

These expose a true OpenAI-compatible endpoint per deployment:

```env
ERRANDER_LLM_BASE_URL=https://<deployment-name>.<region>.models.ai.azure.com/v1
ERRANDER_LLM_MODEL=<model-id-shown-in-foundry>
ERRANDER_LLM_API_KEY=<key from the deployment page>
```

```yaml
llm:
  model: "<model-id-shown-in-foundry>"
  temperature: 0.1
  timeout_seconds: 45
  max_retries: 2
```

Notes:
- Each serverless deployment has its **own** base URL and key — switching models means
  swapping the env vars, not just `ERRANDER_LLM_MODEL`.
- Good models to A/B for this agent: `Llama-3.3-70B-Instruct`, `Mistral-Large-2411`,
  `Phi-4`, `DeepSeek-V3`. Compare them on the same dry-run plan and judge the quality of
  the generated maintenance report and incident-analysis JSON.
- Some MaaS models charge per-token; some are pay-per-hour endpoint hosting. Check the
  pricing page in Foundry before leaving an endpoint running overnight.

### Comparing models across Foundry

Practical workflow for quality testing:

1. Deploy 2–3 models in Foundry.
2. Keep separate `.env.foundry-gpt4omini`, `.env.foundry-llama70b`, `.env.foundry-phi4` files.
3. Symlink/copy the one you want to test to `.env`, run the same dry-run command, save
   the audit database aside per model (e.g. separate PostgreSQL databases).
4. Diff the LLM-generated report sections to judge output quality.

---

## Verifying your configuration

```bash
# Check connectivity, list available models, measure latency
uv run python -m errander --check-llm

# Expected output:
# Checking LLM endpoint: http://10.0.1.5:8000/v1 (model: Qwen/Qwen3-8B-AWQ)
# --------------------------------------------------
#   Status   : OK
#   Models   : Qwen/Qwen3-8B-AWQ
#   Latency  : 2341.5 ms (test completion)
#   Response : 'OK'
# --------------------------------------------------
# LLM endpoint is healthy.
```

---

## Choosing a temperature

All providers use `temperature: 0.1` by default, which gives low-variance structured JSON.

| Value | Behavior | Use case |
|-------|----------|----------|
| 0.0 | Deterministic | Debugging, reproducibility |
| 0.1 | Low variance | **Default — structured JSON output** |
| 0.5 | Moderate variance | Creative reports |
| 1.0+ | High variance | Not recommended for this agent |
