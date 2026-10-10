# Models and costs

Configuration resolves into paperforge.yaml at initialization. Unknown fields, roles and
stages fail validation. Model defaults do not guarantee future availability.

| Provider | Protocol | Default ID | Credential |
|---|---|---|---|
| Ollama | Native chat | gpt-oss:20b | OLLAMA_API_KEY, optional locally |
| Gemini | generateContent | gemini-3.5-flash-lite | GEMINI_API_KEY |
| Groq | Compatible chat | openai/gpt-oss-120b | GROQ_API_KEY |
| DeepSeek | Compatible chat | deepseek-flash | DEEPSEEK_API_KEY |
| Mistral | Compatible chat | mistral-small-latest | MISTRAL_API_KEY |
| OpenAI | Compatible chat | Explicit ID | OPENAI_API_KEY |
| Anthropic | Native messages | Explicit ID | ANTHROPIC_API_KEY |
| OpenRouter | Compatible chat | Explicit ID | OPENROUTER_API_KEY |
| Compatible | Compatible chat | Explicit ID and HTTPS base_url | LLM_API_KEY |

version is a **complete native snapshot ID**, replacing model in requests. Snapshot suffixes
are never guessed. Requested and returned model IDs are recorded separately. Provider aliases
can change; pin a supported snapshot where offered.

Route precedence: stage_routes > role_routes > default_routes. Roles: extractor, planner,
writer, reviewer, reviser. Only stages requesting generation actually use routes.
free_only excludes paid routes; free_first prioritizes configured free routes; paid_only
excludes free routes. Hosted unknown billing/missing credentials are ineligible.
Fallback is restricted to listed names. Reviewer and writer can use different providers.
Browser chat subscriptions do not supply API entitlement.

## Paid setup

Replace placeholders with a supported model and current INR-per-million **ceiling rates**:

~~~bash
paperforge model projects/paper paid-review openai \
  --model YOUR_MODEL_ID --billing paid \
  --input-rate YOUR_INPUT_INR_PER_MILLION \
  --output-rate YOUR_OUTPUT_INR_PER_MILLION
paperforge route projects/paper local paid-review --stage review
paperforge policy projects/paper --mode free_first --budget-inr 500
~~~

For models rejecting temperature, set temperature: null in YAML. Compatible routes accept
token_parameter; OpenAI/Groq use max_completion_tokens. Ollama gpt-oss uses low thinking;
Gemini 2.5 Flash and DeepSeek disable optional thinking in default requests. Not all future
model families support identical generation options.

## Accounting

Each paid attempt reserves a conservative allowance transactionally before dispatch. The cap
includes settled costs and all unresolved reservations. Actual reported tokens settle successful
calls; cached successes incur no new dispatch. Every retry has its own reservation. Transient
errors retry within bounds; auth failures do not retry.

HTTP 429 guidance distinguishes only limits explicitly reported by structured provider fields;
an unspecified 429 remains ambiguous. HTTP Retry-After (seconds or date) and Gemini RetryInfo
are honored without shortening the delay. Reported Gemini daily/zero quotas do not rapidly retry.
Long cooldowns survive resume; waits above max_inline_wait_seconds (default/maximum 60) return
control instead of blocking indefinitely. Set model --min-interval-seconds (0–60, default 0)
to pace dispatches across roles and restarts in one PaperForge project. Other applications/projects
sharing provider quota require their own coordination. Spacing does not increase daily/token quota.

Timeouts, disconnections and malformed successes may have been billed: their reservations
remain counted. External inference cannot be guaranteed exactly once. There is no automatic
release of uncertain costs without reconciliation.
Switching to free_only with a zero new budget still allows zero-cost calls after prior paid spend;
the historical ledger remains intact and further positive-cost reservations remain blocked.

Budget accounting uses configured price ceilings, not the provider's final bill. Taxes, exchange
fees, price changes and server behavior can differ. Use provider-side spend limits too.
Hosted free entitlement is an author assertion; a key cannot certify pricing/quota. Local
inference still uses hardware/electricity. doctor checks credentials without a billable call.

## Saved environment and connection checks

Shared keys go in repository .env; project-specific keys go in PROJECT/.env.
Nonblank existing process values win, followed by project values, then repository defaults.
UTF-8 with or without BOM is accepted. Blank example keys do not erase shared values.
Model selections, snapshots, billing ceilings and routes live in PROJECT/paperforge.yaml.
The Python Workflow API also loads saved keys. No secret values are printed by doctor.

paperforge models PROJECT ROUTE requests provider metadata only. It handles Gemini and
Anthropic pagination, local Ollama tags and compatible model endpoints. It does not change
routes or guarantee free entitlement or generation access. Provider metadata services may
themselves have rate limits/access policies.

paperforge probe PROJECT ROUTE makes one new generation request on exactly that named route,
with retries/fallback disabled and normal billing/cap accounting. It leaves stages untouched.
Each invocation checks again; it cannot return an old success after a key rotation.

Google's Gemini 2.5 access is restricted for new users. New Gemini routes default to
gemini-3.5-flash-lite; existing YAML is not silently changed. If a route returns 404, choose
an ID supported by the current account, probe it and resume. A listed model can still return
404 during generation. See the [PowerShell guide](POWERSHELL_GUIDE.md).

Official API references:
[Ollama](https://docs.ollama.com/api), [Gemini](https://ai.google.dev/gemini-api/docs),
[Anthropic](https://docs.anthropic.com/en/api/messages),
[Groq](https://console.groq.com/docs/openai), [DeepSeek](https://api-docs.deepseek.com),
[Mistral](https://docs.mistral.ai/api), [OpenRouter](https://openrouter.ai/docs).
Recheck supported IDs, prices and entitlement when selecting hosted routes.
