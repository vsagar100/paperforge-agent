from __future__ import annotations

import math
import os
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import httpx

from paperforge.config import Model
from paperforge.schemas import Reply


class ProviderFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float = 0,
        uncertain: bool = False,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.uncertain = uncertain
        self.status_code = status_code


class HTTPProvider:
    """One HTTP attempt. Routing, budgets and retries belong to the durable gateway."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(transport=transport, follow_redirects=False)

    def close(self):
        self.client.close()

    @staticmethod
    def has_credentials(model: Model) -> bool:
        local = urlparse(model.base_url or "").hostname in {"localhost", "127.0.0.1", "::1"}
        return (model.provider == "ollama" and local) or bool(
            os.getenv(model.api_key_env or "", "").strip()
        )

    @staticmethod
    def error_message(model: Model, status: int) -> str:
        message = f"{model.provider} returned HTTP {status} for {model.requested_id}"
        if status == 404 and model.provider == "gemini":
            message += (
                ". Model access or API-version/resource availability may differ for this account. "
                "Gemini 2.5 access is restricted for new accounts; try a current supported ID. "
                "Use 'paperforge models PROJECT ROUTE', update 'paperforge model --model', "
                "then 'paperforge probe PROJECT ROUTE' and resume."
            )
        if status == 429:
            message += ". Provider rate limit or quota exceeded; saved billing labels do not change provider entitlement."
            if model.provider == "gemini":
                message += (
                    " Check this Google project's model RPM, input TPM and daily quota in "
                    "AI Studio. Keys in the same project share limits. Wait for the relevant "
                    "reset or select another permitted provider, then resume."
                )
        return message

    @staticmethod
    def retry_details(response: httpx.Response, model: Model) -> tuple[float, bool, str]:
        """Read only structured delay/quota fields; never echo the server error body."""
        delay = 0.0
        header = response.headers.get("Retry-After", "")
        try:
            number = float(header)
            if math.isfinite(number):
                delay = max(0, number)
        except ValueError:
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=UTC)
                delay = max(0, (date - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
        permanent, hint = False, ""
        if response.status_code == 429 and model.provider == "gemini":
            try:
                body = response.json()
                details = body.get("error", {}).get("details", [])
                if not isinstance(details, list):
                    details = []
                daily, zero = False, False
                for item in details:
                    if not isinstance(item, dict):
                        continue
                    if item.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
                        duration = item.get("retryDelay")
                        if isinstance(duration, str) and re.fullmatch(
                            r"\d+(?:\.\d{1,9})?s", duration
                        ):
                            value = float(duration[:-1])
                            if math.isfinite(value):
                                delay = max(delay, value)
                    if item.get("@type") == "type.googleapis.com/google.rpc.QuotaFailure":
                        violations = item.get("violations", [])
                        if not isinstance(violations, list):
                            continue
                        for violation in violations:
                            if not isinstance(violation, dict):
                                continue
                            quota = str(violation.get("quotaId", "")).lower()
                            metric = str(violation.get("quotaMetric", "")).lower()
                            daily |= "perday" in quota or "per_day" in metric
                            zero |= str(violation.get("quotaValue", "")) == "0"
                permanent = daily or zero
                if zero:
                    hint = " Provider reports zero quota allocation; check model access and billing in AI Studio."
                elif daily:
                    hint = " Provider reports a daily quota limit; brief retries cannot resolve it."
            except (ValueError, AttributeError, TypeError):
                pass
        if delay:
            hint += f" Provider suggests retrying no sooner than {delay:g} seconds."
        return delay, permanent, hint

    def list_models(self, model: Model) -> list[dict]:
        """Read metadata, never generate or silently change the chosen model."""
        if not self.has_credentials(model):
            raise ProviderFailure(f"Set {model.api_key_env} for provider {model.provider}")
        key = os.getenv(model.api_key_env or "", "").strip()
        headers, params = {}, {}
        if model.provider == "gemini":
            endpoint = "/v1beta/models"
            headers["x-goog-api-key"] = key
            params["pageSize"] = 1000
        elif model.provider == "ollama":
            endpoint = "/api/tags"
            if key:
                headers["Authorization"] = f"Bearer {key}"
        elif model.provider == "anthropic":
            endpoint = "/v1/models"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            params["limit"] = 100
        else:
            endpoint = "/models"
            headers["Authorization"] = f"Bearer {key}"
        records, seen_pages = {}, set()
        for _ in range(10):
            try:
                response = self.client.get(
                    (model.base_url or "") + endpoint,
                    headers=headers,
                    params=params,
                    timeout=model.timeout_seconds,
                )
            except httpx.RequestError as exc:
                raise ProviderFailure(
                    f"{model.provider} model listing connection failed ({type(exc).__name__})"
                ) from exc
            if not response.is_success:
                raise ProviderFailure(self.error_message(model, response.status_code))
            try:
                body = response.json()
                rows = body["models"] if model.provider in {"gemini", "ollama"} else body["data"]
                if not isinstance(rows, list):
                    raise ValueError("Invalid model list")
                for item in rows:
                    name = (
                        item.get("name")
                        if model.provider in {"gemini", "ollama"}
                        else item.get("id")
                    )
                    if not isinstance(name, str) or not name:
                        raise ValueError("Model lacks an identifier")
                    identifier = (
                        name.removeprefix("models/") if model.provider == "gemini" else name
                    )
                    records[identifier] = {"id": identifier}
                    if model.provider == "gemini":
                        records[identifier].update(
                            supported_generation_methods=item.get("supportedGenerationMethods", []),
                            input_token_limit=item.get("inputTokenLimit"),
                            output_token_limit=item.get("outputTokenLimit"),
                        )
                token = (
                    body.get("nextPageToken")
                    if model.provider == "gemini"
                    else body.get("last_id")
                    if model.provider == "anthropic" and body.get("has_more")
                    else None
                )
                if model.provider == "anthropic" and body.get("has_more") and not token:
                    raise ValueError("Missing pagination cursor")
                if not token:
                    return list(records.values())
                if not isinstance(token, str) or token in seen_pages:
                    raise ValueError("Invalid/repeated pagination")
                seen_pages.add(token)
                params["pageToken" if model.provider == "gemini" else "after_id"] = token
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ProviderFailure(f"{model.provider} returned unusable model metadata") from exc
        raise ProviderFailure(
            f"{model.provider} model list exceeded ten pages; narrow the request outside PaperForge"
        )

    def generate(self, model: Model, system: str, prompt: str) -> Reply:
        key = os.getenv(model.api_key_env or "", "").strip()
        if not self.has_credentials(model):
            raise ProviderFailure(f"Set {model.api_key_env} for provider {model.provider}")
        headers = {"Content-Type": "application/json"}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        model_id = model.requested_id
        if model.provider == "ollama":
            endpoint = "/api/chat"
            if key:
                headers["Authorization"] = f"Bearer {key}"
            payload = {
                "model": model_id,
                "messages": messages,
                "stream": False,
                "think": "low" if model_id.startswith("gpt-oss") else False,
                "options": {
                    "num_predict": model.max_output_tokens,
                    "temperature": model.temperature,
                },
            }
        elif model.provider == "gemini":
            endpoint = f"/v1beta/models/{quote(model_id, safe='')}:generateContent"
            headers["x-goog-api-key"] = key
            generation = {
                "temperature": model.temperature,
                "maxOutputTokens": model.max_output_tokens,
            }
            if model_id.startswith("gemini-2.5-flash"):
                generation["thinkingConfig"] = {"thinkingBudget": 0}
            payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": generation,
            }
        elif model.provider == "anthropic":
            endpoint = "/v1/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            payload = {
                "model": model_id,
                "system": system,
                "messages": messages[1:],
                "max_tokens": model.max_output_tokens,
                "temperature": model.temperature,
            }
        else:
            endpoint = "/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            token_param = (
                "max_completion_tokens"
                if model.provider in {"groq", "openai"}
                else model.token_parameter
            )
            payload = {
                "model": model_id,
                "messages": messages,
                token_param: model.max_output_tokens,
                "temperature": model.temperature,
                "stream": False,
            }
            if model.provider == "deepseek":
                payload["thinking"] = {"type": "disabled"}
        # Some reasoning models disallow temperature; explicitly configure null in that route.
        if model.temperature is None:
            payload.pop("temperature", None)
            if "generationConfig" in payload:
                payload["generationConfig"].pop("temperature", None)
            if "options" in payload:
                payload["options"].pop("temperature", None)
        try:
            response = self.client.post(
                (model.base_url or "") + endpoint,
                headers=headers,
                json=payload,
                timeout=model.timeout_seconds,
            )
        except httpx.RequestError as exc:
            # Do not include request URL/body or exception text: they can contain credentials.
            raise ProviderFailure(
                f"{model.provider} connection failed ({type(exc).__name__})",
                retryable=True,
                uncertain=True,
            ) from exc
        if not response.is_success:
            status = response.status_code
            retryable = status in {408, 429, 500, 502, 503, 504}
            delay, permanent, hint = self.retry_details(response, model)
            raise ProviderFailure(
                self.error_message(model, status) + hint,
                retryable=retryable and not permanent,
                retry_after=delay,
                uncertain=status >= 500 or status == 408,
                status_code=status,
            )
        try:
            body = response.json()
            if model.provider == "ollama":
                text = body["message"]["content"]
                input_tokens, output_tokens = body.get("prompt_eval_count"), body.get("eval_count")
                returned = body.get("model", model_id)
                truncated = body.get("done_reason") == "length"
            elif model.provider == "gemini":
                candidate = body["candidates"][0]
                text = "".join(
                    part.get("text", "")
                    for part in candidate["content"]["parts"]
                    if not part.get("thought")
                )
                usage = body.get("usageMetadata", {})
                input_tokens = usage.get("promptTokenCount")
                output_tokens = usage.get("candidatesTokenCount")
                if output_tokens is not None:
                    output_tokens += usage.get("thoughtsTokenCount", 0)
                returned = body.get("modelVersion", model_id)
                truncated = candidate.get("finishReason") == "MAX_TOKENS"
            elif model.provider == "anthropic":
                text = "".join(
                    part.get("text", "") for part in body["content"] if part.get("type") == "text"
                )
                usage = body.get("usage", {})
                input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
                returned = body.get("model", model_id)
                truncated = body.get("stop_reason") == "max_tokens"
            else:
                choice = body["choices"][0]
                text = choice["message"]["content"]
                usage = body.get("usage", {})
                input_tokens, output_tokens = (
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                )
                returned = body.get("model", model_id)
                truncated = choice.get("finish_reason") == "length"
            if not isinstance(text, str) or not text.strip() or truncated:
                raise ValueError("empty or truncated response")
            return Reply(
                text=text,
                provider=model.provider,
                requested_model=model_id,
                returned_model=returned,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderFailure(
                f"{model.provider} returned an unusable response; increase output limit if truncated",
                uncertain=True,
            ) from exc
