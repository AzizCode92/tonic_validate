from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, DefaultDict, List, Tuple, Union, overload
from tonic_validate.classes.benchmark import Benchmark, BenchmarkItem

from tonic_validate.classes.llm_response import LLMResponse
from tonic_validate.classes.run import Run, RunData
from tonic_validate.metrics.answer_consistency_metric import AnswerConsistencyMetric
from tonic_validate.metrics.answer_similarity_metric import AnswerSimilarityMetric
from tonic_validate.metrics.augmentation_precision_metric import (
    AugmentationPrecisionMetric,
)

from tonic_validate.metrics.metric import Metric
from tonic_validate.services.openai_service import OpenAIService


class ValidateScorer:
    def __init__(
        self,
        metrics: List[Metric] = [
            AnswerSimilarityMetric(),
            AugmentationPrecisionMetric(),
            AnswerConsistencyMetric(),
        ],
        model_evaluator: str = "gpt-4-turbo-preview",
    ):
        self.metrics = metrics
        self.model_evaluator = model_evaluator

    def _score_item_rundata(self, response: LLMResponse) -> RunData:
        scores: dict[str, float] = {}
        # We cache per response, so we need to create a new OpenAIService
        openai_service = OpenAIService(self.model_evaluator)
        for metric in self.metrics:
            score = metric.score(response, openai_service)
            scores[metric.name] = score

        benchmark_item = response.benchmark_item
        return RunData(
            scores,
            benchmark_item["question"],
            benchmark_item["answer"],
            response.llm_answer,
            response.llm_context_list,
        )

    def score_run(self, responses: List[LLMResponse], parallelism: int = 1) -> Run:
        """Calculate metric scores for a list of LLMResponse objects.

        Parameters
        ----------
        responses: List[LLMResponse]
            The list of LLMResponse objects to be scored.
        parallelism: int
            The number of threads to use for scoring.

        Returns
        -------
        Run
            The Run object containing the scores and other data.
        """
        run_data: list[RunData] = []

        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            run_data = list(executor.map(self._score_item_rundata, responses))

        # Used to calculate overall score
        total_scores: DefaultDict[str, float] = defaultdict(float)
        num_scores: DefaultDict[str, int] = defaultdict(int)

        for item in run_data:
            for metric_name, score in item.scores.items():
                total_scores[metric_name] += score
                num_scores[metric_name] += 1

        overall_scores: dict[str, float] = {
            metric: total / num_scores[metric] for metric, total in total_scores.items()
        }

        return Run(overall_scores, run_data, None)

    @overload
    def score(
        self,
        benchmark: Benchmark,
        callback: Callable[[str], Tuple[str, List[str]]],
        callback_parallelism=1,
        scoring_parallelism=1,
    ) -> Run:
        ...

    @overload
    def score(
        self,
        benchmark: List[BenchmarkItem],
        callback: Callable[[str], Tuple[str, List[str]]],
        callback_parallelism=1,
        scoring_parallelism=1,
    ) -> Run:
        ...

    def score(
        self,
        benchmark: Union[Benchmark, List[BenchmarkItem]],
        callback: Callable[[str], Tuple[str, List[str]]],
        callback_parallelism=1,
        scoring_parallelism=1,
    ) -> Run:
        """Calculate metric scores for a benchmark.

        Parameters
        ----------
        benchmark: Benchmark
            The benchmark to be scored. Can either be a Benchmark object or a list of BenchmarkItem objects.
        callback: Callable[[str], Tuple[str, List[str]]]
            A callback function that takes a question and returns a tuple of the llm response and the retrieved context list.
        callback_parallelism: int
            The number of threads to use for the callback function.
        scoring_parallelism: int
            The number of threads to use for scoring.

        Returns
        -------
        Run
            The Run object containing the scores and other data.
        """
        responses: list[LLMResponse] = []

        def create_response(item: BenchmarkItem) -> LLMResponse:
            llm_answer, llm_context_list = callback(item["question"])
            return LLMResponse(llm_answer, llm_context_list, item)

        items = benchmark.items if isinstance(benchmark, Benchmark) else benchmark

        with ThreadPoolExecutor(max_workers=callback_parallelism) as executor:
            responses = list(executor.map(create_response, items))

        return self.score_run(responses, scoring_parallelism)
