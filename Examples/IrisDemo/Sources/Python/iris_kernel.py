"""Persistent scikit-learn service for Iris's app-owned worker."""

import json
import time

import numpy as np
from sklearn.datasets import load_breast_cancer, load_iris, load_wine
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import cross_val_score, learning_curve, train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

_DATASETS = {}
_ACTIVE_MODEL = None


def _dataset(kind):
    if kind not in _DATASETS:
        loaders = {"iris": load_iris, "wine": load_wine, "breast_cancer": load_breast_cancer}
        if kind not in loaders:
            raise ValueError(f"Unsupported dataset: {kind}")
        _DATASETS[kind] = loaders[kind](as_frame=False)
    return _DATASETS[kind]


def _split(bunch):
    return train_test_split(
        np.arange(len(bunch.target)), test_size=0.25, random_state=42,
        shuffle=True, stratify=bunch.target,
    )


def _model(classifier, scale):
    if classifier == "logistic_regression":
        estimator = LogisticRegression(random_state=42, max_iter=2000)
    elif classifier == "random_forest":
        estimator = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=1)
    elif classifier == "k_neighbors":
        estimator = KNeighborsClassifier(n_neighbors=5, n_jobs=1)
    else:
        raise ValueError(f"Unsupported classifier: {classifier}")
    return make_pipeline(StandardScaler(), estimator) if scale else estimator


def load_dataset(kind):
    bunch = _dataset(kind)
    return json.dumps({
        "featureNames": [str(name).replace("_", " ") for name in bunch.feature_names],
        "classNames": [str(name) for name in bunch.target_names],
        "points": bunch.data.tolist(), "targets": bunch.target.tolist(),
    }, allow_nan=False)


def train_model(payload_json):
    global _ACTIVE_MODEL
    started = time.perf_counter()
    spec = json.loads(payload_json)
    kind, classifier, scale = spec["dataset"], spec["classifier"], spec["useScaler"]
    if not isinstance(scale, bool) or not isinstance(spec["id"], str) or not spec["id"]:
        raise ValueError("Invalid training request")
    bunch = _dataset(kind)
    train_rows, test_rows = _split(bunch)
    x_train, y_train = bunch.data[train_rows], bunch.target[train_rows]
    x_test, y_test = bunch.data[test_rows], bunch.target[test_rows]

    # Reserve the test cohort first. CV and learning curves see training rows
    # only, and each fold fits its own scaler through the Pipeline.
    scores = cross_val_score(_model(classifier, scale), x_train, y_train, cv=5, scoring="accuracy", n_jobs=1)
    sizes, train_scores, validation_scores = learning_curve(
        _model(classifier, scale), x_train, y_train, cv=5,
        scoring="accuracy", shuffle=True, random_state=42, n_jobs=1,
        train_sizes=np.linspace(0.3, 1.0, 5),
    )
    model = _model(classifier, scale)
    model.fit(x_train, y_train)
    predicted = model.predict(x_test)
    probabilities = model.predict_proba(x_test)
    labels = np.arange(len(bunch.target_names))
    result = {
        "id": spec["id"], "dataset": kind, "classifier": classifier, "useScaler": scale,
        "trainCount": len(train_rows), "testCount": len(test_rows),
        "testAccuracy": float(np.mean(predicted == y_test)),
        "cvAccuracyMean": float(np.mean(scores)), "cvAccuracyStd": float(np.std(scores)),
        "confusionMatrix": confusion_matrix(y_test, predicted, labels=labels).tolist(),
        "classificationReport": classification_report(
            y_test, predicted, labels=labels, target_names=bunch.target_names, digits=2, zero_division=0),
        "learningCurveTrainSizes": sizes.tolist(),
        "learningCurveMeanTrainScores": np.mean(train_scores, axis=1).tolist(),
        "learningCurveMeanTestScores": np.mean(validation_scores, axis=1).tolist(),
        "predictions": [
            {"id": int(row), "actual": int(actual), "predicted": int(guess), "probabilities": probability.tolist()}
            for row, actual, guess, probability in zip(test_rows, y_test, predicted, probabilities)
        ],
        "elapsedSeconds": time.perf_counter() - started,
    }
    encoded = json.dumps(result, allow_nan=False)
    # Publish a complete model only after fitting and result validation succeed.
    # Only one model is retained; repeated training cannot grow a model cache.
    _ACTIVE_MODEL = (spec["id"], model, bunch.data.shape[1])
    return encoded


def predict_sample(model_id, values):
    if _ACTIVE_MODEL is None or _ACTIVE_MODEL[0] != model_id:
        raise ValueError("That model is no longer active. Train again before predicting.")
    _, model, width = _ACTIVE_MODEL
    sample = np.asarray(values, dtype=np.float64)
    if sample.shape != (width,) or not np.isfinite(sample).all():
        raise ValueError("Prediction requires one finite value for each feature")
    probabilities = model.predict_proba(sample.reshape(1, -1))[0]
    return json.dumps({
        "modelID": model_id,
        "predicted": int(model.classes_[int(np.argmax(probabilities))]),
        "probabilities": probabilities.tolist(),
    }, allow_nan=False)
