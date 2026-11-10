import json
import re
import requests
from urllib.parse import quote
from .schemas import SCHEMAS


class ModelError(Exception):
    def __init__(self, message, code="model_error"):
        super().__init__(message)
        self.code = code


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ModelError("모델 응답의 JSON 형식을 확인하지 못했습니다. 다시 시도해 주세요.") from exc
    if not isinstance(value, dict):
        raise ModelError("모델이 객체 형식으로 응답하지 않았습니다.")
    return value


class GeminiClient:
    def __init__(self, settings):
        self.settings = settings

    def generate(self, instruction, context, schema="response"):
        if not self.settings.live:
            raise ModelError("Gemini API 키가 설정되지 않았습니다.")
        native = self.settings.api_style == "generate_content"
        if native:
            url = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(self.settings.model, safe="-._") + ":generateContent"
            body = {
                "systemInstruction": {"parts": [{"text": instruction}]},
                "contents": [{"role": "user", "parts": [{"text": json.dumps(context, ensure_ascii=False)}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMAS[schema],
                    "maxOutputTokens": 7000, "thinkingConfig": {"thinkingLevel": self.settings.thinking_level}},
            }
        else:
            url = self.settings.base_url + "/interactions"
            body = {
                "model": self.settings.model,
                "system_instruction": instruction,
                "input": json.dumps(context, ensure_ascii=False),
                "response_format": {"type": "text", "mime_type": "application/json", "schema": SCHEMAS[schema]},
                "generation_config": {"max_output_tokens": 7000, "thinking_level": self.settings.thinking_level, "thinking_summaries": "none"},
                "store": False,
            }
        try:
            r = requests.post(url,
                              headers={"x-goog-api-key": self.settings.api_key,
                                       "Content-Type": "application/json"},
                              json=body, timeout=(10, self.settings.timeout))
        except requests.ReadTimeout as exc:
            raise ModelError(f"Gemini가 {self.settings.timeout}초 안에 생성 응답을 반환하지 않았습니다. 입력은 보관되어 있습니다.", "response_timeout") from exc
        except requests.ConnectTimeout as exc:
            raise ModelError("Gemini 서버 연결을 시작하지 못했습니다. 네트워크 연결을 확인해 주세요.", "connection_timeout") from exc
        except requests.RequestException as exc:
            raise ModelError("Gemini 서버에 연결할 수 없습니다. 네트워크 연결을 확인해 주세요.", "connection_error") from exc
        if r.status_code in {401, 403}:
            raise ModelError("Gemini API 키 인증 또는 접근 권한을 확인해 주세요. .env와 AI Studio의 키 설정을 확인해 주세요.")
        if r.status_code == 429:
            raise ModelError("Gemini 무료 할당량 또는 호출 제한에 도달했습니다. AI Studio의 사용 한도를 확인하거나 잠시 후 다시 시도해 주세요.")
        if r.status_code in {500, 502, 503, 504}:
            raise ModelError(f"Gemini 서버가 일시적으로 응답하지 못했습니다(HTTP {r.status_code}). 입력은 보관되어 있으니 잠시 후 같은 입력으로 다시 시도해 주세요.", "upstream_unavailable")
        if not r.ok:
            raise ModelError(f"Gemini 요청을 처리하지 못했습니다(HTTP {r.status_code}). API 키, 모델명과 연결 설정을 확인해 주세요.")
        try:
            data = r.json()
            if native:
                candidates = data.get("candidates") or []
                candidate = candidates[0] if candidates else {}
                finish = candidate.get("finishReason")
                if finish and finish != "STOP":
                    raise ModelError("Gemini 응답이 끝까지 생성되지 않았습니다. 다시 시도해 주세요.", "incomplete_response")
                content = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", [])
                                  if not part.get("thought"))
            else:
                if data.get("status") in {"failed", "cancelled", "incomplete", "requires_action"}:
                    raise ModelError("Gemini 응답 생성이 완료되지 않았습니다. 다시 시도해 주세요.", "incomplete_response")
                # Read only final model text. Thought steps/signatures are not used or logged.
                content = "".join(part.get("text", "")
                                  for step in data.get("steps", []) if step.get("type") == "model_output"
                                  for part in step.get("content", []) if part.get("type") == "text")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ModelError("Gemini 응답에 사용할 수 있는 본문이 없습니다.") from exc
        if not content:
            raise ModelError("Gemini 응답에 사용할 수 있는 본문이 없습니다.")
        return parse_json(content or "")
