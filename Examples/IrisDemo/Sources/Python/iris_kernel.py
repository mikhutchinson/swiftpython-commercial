"""
iris_kernel.py

Runtime-side sklearn service boundary for the IRIS demo.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

import numpy as np
from sklearn.datasets import load_breast_cancer, load_iris, load_wine
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import cross_val_score, learning_curve, train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import StandardScaler


def _dataset(kind: str) -> Tuple[Any, list[str], list[str]]:
    if kind == "iris":
        bunch = load_iris(as_frame=False)
        return (
            bunch,
            ["Sepal Length", "Sepal Width", "Petal Length", "Petal Width"],
            ["Setosa", "Versicolor", "Virginica"],
        )
    if kind == "wine":
        bunch = load_wine(as_frame=False)
        return (
            bunch,
            [
                "Alcohol",
                "Malic acid",
                "Ash",
                "Alcalinity",
                "Mg",
                "Phenols",
                "Flavanoids",
                "Nonflavanoid phenols",
                "Proanthocyanins",
                "Color intensity",
                "Hue",
                "OD280/OD315",
                "Proline",
            ],
            ["Class 0", "Class 1", "Class 2"],
        )
    if kind == "breast_cancer":
        bunch = load_breast_cancer(as_frame=False)
        return (
            bunch,
            [f"F{i + 1}" for i in range(30)],
            ["Malignant", "Benign"],
        )
    raise ValueError(f"Unsupported dataset kind: {kind}")


def _estimator(classifier: str):
    if classifier == "logistic_regression":
        return LogisticRegression(random_state=42, max_iter=2000)
    if classifier == "random_forest":
        return RandomForestClassifier(n_estimators=100, random_state=42)
    if classifier == "k_neighbors":
        return KNeighborsClassifier(n_neighbors=5)
    raise ValueError(f"Unsupported classifier: {classifier}")


def load_dataset(kind: str) -> str:
    bunch, feature_names, class_names = _dataset(kind)
    x = np.asarray(bunch.data, dtype=float)
    y = np.asarray(bunch.target, dtype=int)
    payload = {
        "featureNames": feature_names,
        "classNames": class_names,
        "points": x.tolist(),
        "targets": [int(v) for v in y.tolist()],
    }
    return json.dumps(payload)


def train_model(payload_json: str) -> str:
    spec: Dict[str, Any] = json.loads(payload_json)
    kind = str(spec.get("dataset", "iris"))
    classifier = str(spec.get("classifier", "logistic_regression"))
    use_scaler = bool(spec.get("useScaler", True))

    bunch, _, class_names = _dataset(kind)
    x = np.asarray(bunch.data, dtype=float)
    y = np.asarray(bunch.target, dtype=int)
    data_for_cv_and_split = StandardScaler().fit_transform(x) if use_scaler else x

    cv_scores = cross_val_score(
        _estimator(classifier),
        data_for_cv_and_split,
        y,
        scoring="accuracy",
        cv=5,
    )
    cv_mean = float(np.mean(cv_scores))
    cv_std = float(np.std(cv_scores))

    x_train, x_test, y_train, y_test = train_test_split(
        data_for_cv_and_split,
        y,
        test_size=0.25,
        random_state=42,
        shuffle=True,
        stratify=y,
    )

    model = _estimator(classifier)
    model.fit(x_train, y_train)
    test_accuracy = float(model.score(x_test, y_test))
    y_pred = model.predict(x_test)

    matrix = confusion_matrix(y_test, y_pred).astype(int).tolist()
    report = classification_report(
        y_test,
        y_pred,
        target_names=class_names,
        digits=2,
    )

    lc_train_sizes: list[int] = []
    lc_mean_train: list[float] = []
    lc_mean_test: list[float] = []
    try:
        train_sizes, train_scores, test_scores = learning_curve(
            _estimator(classifier),
            data_for_cv_and_split,
            y,
            cv=5,
            scoring="accuracy",
            shuffle=True,
            random_state=42,
        )
        lc_train_sizes = [int(v) for v in train_sizes.tolist()]
        lc_mean_train = [float(v) for v in np.mean(train_scores, axis=1).tolist()]
        lc_mean_test = [float(v) for v in np.mean(test_scores, axis=1).tolist()]
    except Exception:
        pass

    result = {
        "modelName": spec.get("classifierName", classifier),
        "testAccuracy": test_accuracy,
        "cvAccuracyMean": cv_mean,
        "cvAccuracyStd": cv_std,
        "confusionMatrix": matrix,
        "classificationReport": report,
        "learningCurveTrainSizes": lc_train_sizes,
        "learningCurveMeanTrainScores": lc_mean_train,
        "learningCurveMeanTestScores": lc_mean_test,
    }
    return json.dumps(result)
