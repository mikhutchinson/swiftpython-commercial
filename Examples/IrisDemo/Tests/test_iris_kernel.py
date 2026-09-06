"""Behavioral checks for the example's evaluation and retained-model boundary."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from sklearn.pipeline import Pipeline

import numpy as np

source = Path(__file__).resolve().parents[1] / "Sources/Python/iris_kernel.py"
spec = importlib.util.spec_from_file_location("iris_kernel_test", source)
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


class IrisKernelTests(unittest.TestCase):
    def train(self, identity="test-model", **changes):
        request = {"id": identity, "dataset": "iris", "classifier": "k_neighbors", "useScaler": True}
        request.update(changes)
        return json.loads(kernel.train_model(json.dumps(request)))

    def test_test_rows_are_excluded_from_both_validation_operations(self):
        bunch = kernel._dataset("iris")
        train, test = kernel._split(bunch)
        with patch.object(kernel, "cross_val_score", wraps=kernel.cross_val_score) as cv, \
             patch.object(kernel, "learning_curve", wraps=kernel.learning_curve) as curve:
            result = self.train()
        for call in [cv.call_args, curve.call_args]:
            np.testing.assert_array_equal(call.args[1], bunch.data[train])
            np.testing.assert_array_equal(call.args[2], bunch.target[train])
            self.assertIsInstance(call.args[0], Pipeline)
        self.assertFalse(set(train) & set(test))
        self.assertEqual(set(train) | set(test), set(range(len(bunch.data))))
        self.assertEqual({p["id"] for p in result["predictions"]}, set(test))
        self.assertEqual(sum(map(sum, result["confusionMatrix"])), len(test))
        correct = sum(p["actual"] == p["predicted"] for p in result["predictions"])
        self.assertAlmostEqual(result["testAccuracy"], correct / len(test))
        for prediction in result["predictions"]:
            self.assertEqual(prediction["actual"], int(bunch.target[prediction["id"]]))
            self.assertAlmostEqual(sum(prediction["probabilities"]), 1)

    def test_prediction_reuses_fitted_model_and_rejects_old_identity(self):
        result = self.train("old")
        sample = result["predictions"][0]
        values = kernel._dataset("iris").data[sample["id"]].tolist()
        with patch.object(kernel, "_model", side_effect=AssertionError("Prediction must not fit a model")):
            predicted = json.loads(kernel.predict_sample("old", values))
        self.assertEqual(predicted["predicted"], sample["predicted"])
        self.train("new")
        with self.assertRaisesRegex(ValueError, "no longer active"):
            kernel.predict_sample("old", values)

    def test_bad_inputs_preserve_the_active_model_and_original_data(self):
        self.train("valid")
        bunch = kernel._dataset("iris")
        before = bunch.data.copy()
        for values in ([1], [float("nan")] * 4, [float("inf")] * 4, [[1, 2, 3, 4]]):
            with self.assertRaises(ValueError): kernel.predict_sample("valid", values)
        with self.assertRaises(ValueError): self.train("invalid", classifier="unknown")
        self.assertEqual(json.loads(kernel.predict_sample("valid", before[0].tolist()))["modelID"], "valid")
        edited = before[0].copy(); edited[2:] = [6.0, 2.4]
        kernel.predict_sample("valid", edited.tolist())
        np.testing.assert_array_equal(bunch.data, before)

    def test_all_datasets_have_real_feature_names_and_stable_splits(self):
        for name in ("iris", "wine", "breast_cancer"):
            first = kernel._dataset(name)
            self.assertIs(first, kernel._dataset(name))
            payload = json.loads(kernel.load_dataset(name))
            self.assertEqual(len(payload["featureNames"]), first.data.shape[1])
            train_a, test_a = kernel._split(first)
            train_b, test_b = kernel._split(first)
            np.testing.assert_array_equal(train_a, train_b)
            np.testing.assert_array_equal(test_a, test_b)
        with self.assertRaises(ValueError): kernel.load_dataset("unknown")
        self.assertEqual(len(kernel._DATASETS), 3)


if __name__ == "__main__": unittest.main()
