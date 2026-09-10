from deepeval.models import DeepEvalBaseLLM


class ProjectJudge(DeepEvalBaseLLM):
    """Use Silicon Notebook's configured chat workload as the DeepEval judge."""
    def __init__(self, repo, workload='ask_answer'):
        self.repo = repo
        self.workload = workload
        super().__init__(model='silicon-notebook-' + workload)

    def load_model(self):
        return self.repo.chat(self.workload)

    def generate(self, prompt: str, schema=None):
        return self.model.chat_json([{'role': 'user', 'content': prompt}], schema or {})

    async def a_generate(self, prompt: str, schema=None):
        return self.generate(prompt, schema)

    def get_model_name(self):
        return 'silicon-notebook-' + self.workload
