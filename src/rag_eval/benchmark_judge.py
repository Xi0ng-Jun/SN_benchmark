"""Schema-aware judge using an isolated configured product model client."""
import asyncio
import json

from deepeval.models import DeepEvalBaseLLM


def _schema_hint(schema):
    if schema is None:
        return '{}'
    document = schema.model_json_schema()

    def example(node):
        reference = node.get('$ref')
        if reference:
            target = document
            for part in reference.removeprefix('#/').split('/'):
                target = target[part]
            return example(target)
        if 'enum' in node:
            return node['enum'][0]
        if 'anyOf' in node:
            choice = next(
                (item for item in node['anyOf'] if item.get('type') != 'null'),
                node['anyOf'][0],
            )
            return example(choice)
        kind = node.get('type')
        if kind == 'object' or 'properties' in node:
            return {
                name: example(field)
                for name, field in node.get('properties', {}).items()
            }
        if kind == 'array':
            return [example(node.get('items', {}))]
        if kind in ('integer', 'number'):
            return 0
        if kind == 'boolean':
            return False
        return ''

    return json.dumps(example(document), ensure_ascii=False)


class BenchmarkJudge(DeepEvalBaseLLM):
    def __init__(self, repo):
        self.repo = repo
        self.usage = []
        super().__init__(model='public-benchmark-judge')

    def load_model(self):
        return self.repo.chat('ask_answer')

    def generate(self, prompt, schema=None):
        raw = self.model.chat_json(
            [{'role': 'user', 'content': prompt}], _schema_hint(schema)
        )
        if schema is not None:
            if isinstance(raw, str):
                return schema.model_validate_json(raw)
            return schema.model_validate(raw)
        return raw if isinstance(raw, str) else json.dumps(raw)

    async def a_generate(self, prompt, schema=None):
        return await asyncio.to_thread(self.generate, prompt, schema)

    def get_model_name(self):
        return 'public-benchmark-' + str(getattr(self.model, 'model', 'configured-chat'))
