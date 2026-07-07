"""RAG evaluation interfaces — reserved for future implementation.

Defines abstract evaluator and metric models. When RAG evaluation is
needed, implement a concrete subclass (e.g. LLMJudgeEvaluator) that
performs retrieval/generation quality assessment.
"""

from abc import ABC, abstractmethod

from personal_assistant.knowledge.models import GenerationMetrics, RetrievalMetrics


class RAGEvaluator(ABC):
    """Abstract evaluator for RAG retrieval and generation quality.

    Reserved for future implementation. Expected evaluation dimensions:
      - Retrieval precision@k, recall@k, MRR
      - Generation faithfulness, answer relevancy
      - End-to-end benchmark with golden datasets
    """

    @abstractmethod
    async def evaluate_retrieval(
        self,
        query: str,
        retrieved_doc_ids: list[str],
        expected_doc_ids: list[str] | None = None,
    ) -> RetrievalMetrics:
        """Evaluate retrieval quality.

        Args:
            query: The user query.
            retrieved_doc_ids: doc_ids of retrieved chunks, in order.
            expected_doc_ids: ground-truth relevant doc_ids (optional).

        Returns:
            RetrievalMetrics with precision@k, recall@k, MRR, hit_rate.
        """
        ...

    @abstractmethod
    async def evaluate_generation(
        self,
        query: str,
        response: str,
        retrieved_docs: list[dict],
        ground_truth: str | None = None,
    ) -> GenerationMetrics:
        """Evaluate generation quality.

        Args:
            query: The user query.
            response: The LLM's answer.
            retrieved_docs: The chunks used for generation.
            ground_truth: Optional reference answer.

        Returns:
            GenerationMetrics with faithfulness and answer_relevancy.
        """
        ...

    @abstractmethod
    async def run_benchmark(
        self,
        test_cases: list[dict],
    ) -> dict:
        """Run a full RAG benchmark.

        Args:
            test_cases: List of {query, expected_doc_ids, ground_truth?}.

        Returns:
            Aggregated metrics dict.
        """
        ...


class NoopEvaluator(RAGEvaluator):
    """Default no-op evaluator that returns empty/zero metrics.

    Used when RAG evaluation is not configured.
    """

    async def evaluate_retrieval(
        self,
        query: str,
        retrieved_doc_ids: list[str],
        expected_doc_ids: list[str] | None = None,
    ) -> RetrievalMetrics:
        return RetrievalMetrics()

    async def evaluate_generation(
        self,
        query: str,
        response: str,
        retrieved_docs: list[dict],
        ground_truth: str | None = None,
    ) -> GenerationMetrics:
        return GenerationMetrics()

    async def run_benchmark(self, test_cases: list[dict]) -> dict:
        return {"status": "skipped", "reason": "evaluator not configured"}
