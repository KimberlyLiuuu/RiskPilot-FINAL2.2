import json
import os

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


def _client():
    """Create a client without automatic retries."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    return OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        max_retries=0,
    )


def _parse_json(content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek returned empty content")

    cleaned = (
        content.replace("```json", "")
        .replace("```JSON", "")
        .replace("```", "")
        .strip()
    )

    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise ValueError("DeepSeek response must be a JSON object")
    return result


class RiskAgent:
    def _request_json(self, prompt, timeout=90):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("AI prompt must be a non-empty string")

        response = _client().chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are RiskPilot's construction risk intelligence "
                        "assistant. Follow the user's requested JSON schema "
                        "exactly and return only one valid JSON object."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            timeout=timeout,
        )

        return _parse_json(response.choices[0].message.content)

    def analyze(self, prompt, timeout=90):
        # The caller already owns the complete prompt and output schema.
        # Do not wrap it in the obsolete overall_score schema.
        return self._request_json(prompt, timeout)

    def explain_current_risk(self, prompt, timeout=60):
        return self._request_json(prompt, timeout)

    def explain_vision_text(self, prompt, timeout=90):
        return self._request_json(prompt, timeout)

    def generate_report(self, prompt, timeout=90):
        return self._request_json(prompt, timeout)
