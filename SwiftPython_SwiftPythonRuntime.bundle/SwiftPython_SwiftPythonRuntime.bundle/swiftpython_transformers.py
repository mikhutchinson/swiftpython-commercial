from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import transformers as _tf


@dataclass
class TextClassificationPrediction:
    label: str
    score: float


@dataclass
class TokenizationBoundaryRow:
    index: int
    originalTokenCount: int
    effectiveTokenCount: int
    wasTruncated: bool


@dataclass
class TokenizationBoundaryReport:
    rows: list[TokenizationBoundaryRow]
    totalOriginalTokens: int
    totalEffectiveTokens: int
    truncatedRowCount: int


class Pipeline:
    def __init__(self, inner: Any):
        self._inner = inner

    @property
    def tokenizer(self) -> Any:
        return self._inner.tokenizer

    def __call__(self, inputs: Any, *args: Any, **kwargs: Any) -> Any:
        return self._inner(inputs, *args, **kwargs)

    def classify(self, inputs: Any, **kwargs: Any) -> list[list[TextClassificationPrediction]]:
        raw = self._inner(inputs, **kwargs)
        # Match current Swift behavior:
        # - sequence input + top-1 list[dict] => one row per input ([[dict], ...])
        # - scalar input + top-k list[dict]   => single row ([dict, ...])
        if isinstance(inputs, (list, tuple)) and isinstance(raw, list) and raw and isinstance(raw[0], dict):
            raw = [[item] for item in raw]
        return _normalize_classification(raw)

    def tokenizeInputIDs(
        self,
        inputs: Any,
        truncation: bool = True,
        padding: bool = False,
        **kwargs: Any,
    ) -> list[list[int]]:
        encoded = self.tokenizer(inputs, truncation=truncation, padding=padding, **kwargs)
        return _normalize_input_ids(encoded)

    def inputTokenCount(
        self,
        inputs: Any,
        truncation: bool = True,
        padding: bool = False,
        **kwargs: Any,
    ) -> int:
        rows = self.tokenizeInputIDs(inputs, truncation=truncation, padding=padding, **kwargs)
        return sum(len(row) for row in rows)

    def tokenizationBoundaryReport(
        self,
        inputs: Any,
        maxLength: Optional[int] = None,
        padding: bool = False,
        **kwargs: Any,
    ) -> TokenizationBoundaryReport:
        effective_kwargs = dict(kwargs)
        if maxLength is not None:
            effective_kwargs["max_length"] = maxLength

        original_rows = self.tokenizeInputIDs(inputs, truncation=False, padding=False, **kwargs)
        effective_rows = self.tokenizeInputIDs(
            inputs,
            truncation=True,
            padding=padding,
            **effective_kwargs,
        )

        if len(original_rows) != len(effective_rows):
            raise RuntimeError(
                f"Tokenizer boundary report shape mismatch: original rows ({len(original_rows)}) != "
                f"effective rows ({len(effective_rows)})."
            )

        rows: list[TokenizationBoundaryRow] = []
        total_original = 0
        total_effective = 0
        truncated_rows = 0

        for idx, (original, effective) in enumerate(zip(original_rows, effective_rows)):
            original_count = len(original)
            effective_count = len(effective)
            was_truncated = effective_count < original_count
            if was_truncated:
                truncated_rows += 1
            total_original += original_count
            total_effective += effective_count
            rows.append(
                TokenizationBoundaryRow(
                    index=idx,
                    originalTokenCount=original_count,
                    effectiveTokenCount=effective_count,
                    wasTruncated=was_truncated,
                )
            )

        return TokenizationBoundaryReport(
            rows=rows,
            totalOriginalTokens=total_original,
            totalEffectiveTokens=total_effective,
            truncatedRowCount=truncated_rows,
        )


def pipeline(
    task: Optional[str] = None,
    model: Optional[str] = None,
    tokenizer: Optional[str] = None,
    framework: Optional[str] = None,
    device: Optional[Any] = None,
    **kwargs: Any,
) -> Pipeline:
    return Pipeline(
        _tf.pipeline(
            task=task,
            model=model,
            tokenizer=tokenizer,
            framework=framework,
            device=device,
            **kwargs,
        )
    )


# Passthroughs for parity with existing module surface
PreTrainedTokenizerBase = _tf.PreTrainedTokenizerBase
PreTrainedModel = _tf.PreTrainedModel
PretrainedConfig = _tf.PretrainedConfig
AutoTokenizer = _tf.AutoTokenizer
AutoModel = _tf.AutoModel
AutoModelForSequenceClassification = _tf.AutoModelForSequenceClassification
AutoConfig = _tf.AutoConfig


def _normalize_classification(output: Any) -> list[list[TextClassificationPrediction]]:
    def parse_prediction(item: Any) -> TextClassificationPrediction:
        if not isinstance(item, dict):
            raise RuntimeError("Expected dict in text-classification output")
        label = item.get("label") or item.get("entity_group")
        if label is None:
            raise RuntimeError("Expected key 'label' (or 'entity_group') in text-classification output dictionary")
        score = item.get("score")
        if score is None:
            raise RuntimeError("Expected key 'score' in text-classification output dictionary")
        return TextClassificationPrediction(label=str(label), score=float(score))

    if isinstance(output, dict):
        return [[parse_prediction(output)]]

    if not isinstance(output, list):
        raise RuntimeError("Unsupported text-classification output type; expected dict/list/list-of-lists")

    if len(output) == 0:
        return []

    first = output[0]
    if isinstance(first, dict):
        return [[parse_prediction(item) for item in output]]

    if isinstance(first, list):
        return [[parse_prediction(item) for item in inner] for inner in output]

    raise RuntimeError("Unsupported text-classification list element type; expected dict or list")


def _normalize_input_ids(encoded: Any) -> list[list[int]]:
    if hasattr(encoded, "input_ids"):
        ids_raw = encoded.input_ids
    elif isinstance(encoded, dict) and "input_ids" in encoded:
        ids_raw = encoded["input_ids"]
    else:
        try:
            ids_raw = encoded["input_ids"]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Tokenizer output did not provide input_ids.") from exc

    if hasattr(ids_raw, "tolist"):
        ids_raw = ids_raw.tolist()
    elif not isinstance(ids_raw, (list, tuple)):
        ids_raw = list(ids_raw)

    if len(ids_raw) == 0:
        return []

    if isinstance(ids_raw[0], int):
        return [[int(x) for x in ids_raw]]

    rows: list[list[int]] = []
    for row in ids_raw:
        if hasattr(row, "tolist"):
            row_items = row.tolist()
        elif isinstance(row, (list, tuple)):
            row_items = row
        else:
            row_items = list(row)
        rows.append([int(x) for x in row_items])
    return rows
