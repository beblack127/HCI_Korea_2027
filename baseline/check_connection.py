"""Check the configured Gemini key with a tiny synthetic request; never print keys."""
from core.config import Settings
from core.llm import GeminiClient, ModelError


def main():
    settings = Settings()
    if not settings.api_key:
        print("GEMINI_NOT_CONFIGURED: .env의 GEMINI_API_KEY를 입력하세요.")
        return 2
    try:
        GeminiClient(settings).generate(
            'Return exactly one JSON object with query and reason. This is a connection test.',
            {'task': 'Set query to academic exploration and reason to connection test.'}, schema='query')
    except ModelError as exc:
        print('GEMINI_CONNECTION_FAILED: ' + str(exc))
        return 1
    print('GEMINI_CONNECTION_OK: ' + settings.model)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
