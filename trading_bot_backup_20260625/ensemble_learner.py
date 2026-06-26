import numpy as np
import pandas as pd
import logging
import os
import json
import time
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from sklearn.ensemble import VotingRegressor, StackingRegressor
from sklearn.linear_model import LinearRegression
import warnings
warnings.filterwarnings('ignore')

logger = logging.getLogger("ensemble_learner")

ENSEMBLE_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "ensemble_models.json")
ENSEMBLE_PERFORMANCE_FILE = os.path.join(os.path.dirname(__file__), "data", "ensemble_performance.json")

# Ensemble model configurations
ENSEMBLE_CONFIGS = {
    'voting_regressor': {
        'estimators': ['mlp_regressor', 'gradient_boosting', 'random_forest'],
        'weights': None,  # Equal weights
        'method': 'hard',  # 'hard' or 'soft'
    },
    'stacking_regressor': {
        'base_estimators': ['mlp_regressor', 'gradient_boosting'],
        'final_estimator': LinearRegression(),
        'passthrough': False,
    },
    'blending_regressor': {
        'blending': True,
        'regularization': 0.1,
    },
}

# Meta-feature generation
META_FEATURE_CONFIG = {
    'model_performance': True,  # Model prediction error on validation set
    'prediction_consensus': True,  # Agreement between models
    'feature_importance': True,  # Which features models use
    'historical_performance': True,  # Past accuracy
    'complexity_score': True,  # Model complexity
}


@dataclass
class BaseModelInfo:
    name: str
    model: Any
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    prediction_error: float
    feature_importance: List[float]
    last_trained: float
    training_samples: int


@dataclass
class EnsembleModel:
    name: str
    model: Any
    base_models: List[BaseModelInfo]
    meta_features: Dict[str, float]
    performance_history: List[dict]
    created_at: float
    last_updated: float
    configuration: dict

    def predict(self, X):
        return self.model.predict(X)

    def predict_proba(self, X):
        if hasattr(self.model, 'predict_proba'):
            return self.model.predict_proba(X)
        return None


@dataclass
class TrainingSample:
    features: List[float]
    target: float
    timestamp: float
    model_predictions: Dict[str, float]
    meta_features: Dict[str, float]
    source_symbol: str


class EnsembleLearner:
    def __init__(self):
        self.base_models: Dict[str, BaseModelInfo] = {}
        self.ensemble_models: Dict[str, EnsembleModel] = {}
        self.training_samples: List[TrainingSample] = []
        self.meta_feature_generators: Dict[str, callable] = {}
        self.model_performance_history: Dict[str, List[dict]] = defaultdict(list)
        self.current_ensemble: Optional[EnsembleModel] = None

        self._load()
        self._initialize_meta_feature_generators()

    def _load(self):
        try:
            if os.path.exists(ENSEMBLE_DATA_FILE):
                with open(ENSEMBLE_DATA_FILE, "r") as f:
                    data = json.load(f)

                # Load base models
                for model_name, model_data in data.get("base_models", {}).items():
                    self.base_models[model_name] = BaseModelInfo(**model_data)

                # Load ensemble models
                for ensemble_name, ensemble_data in data.get("ensemble_models", {}).items():
                    ensemble = EnsembleModel(
                        name=ensemble_name,
                        model=ensemble_data.get("model"),
                        base_models=[BaseModelInfo(**bm) for bm in ensemble_data.get("base_models", [])],
                        meta_features=ensemble_data.get("meta_features", {}),
                        performance_history=ensemble_data.get("performance_history", []),
                        created_at=ensemble_data.get("created_at", time.time()),
                        last_updated=ensemble_data.get("last_updated", time.time()),
                        configuration=ensemble_data.get("configuration", {}),
                    )
                    self.ensemble_models[ensemble_name] = ensemble

            logger.info(f"Ensemble learner loaded: {len(self.base_models)} base models, {len(self.ensemble_models)} ensemble models")
        except Exception as e:
            logger.error(f"Error loading ensemble learner: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(ENSEMBLE_DATA_FILE), exist_ok=True)
            data = {
                "base_models": {
                    name: asdict(model)
                    for name, model in self.base_models.items()
                },
                "ensemble_models": {
                    name: asdict(model)
                    for name, model in self.ensemble_models.items()
                },
                "saved_at": time.time(),
            }
            with open(ENSEMBLE_DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving ensemble learner: {e}")

    def _initialize_meta_feature_generators(self):
        # Define meta-feature generators for different aspects
        self.meta_feature_generators = {
            'model_agreement': self._generate_model_agreement,
            'prediction_consensus': self._generate_prediction_consensus,
            'model_uncertainty': self._generate_model_uncertainty,
            'feature_diversity': self._generate_feature_diversity,
            'historical_accuracy': self._generate_historical_accuracy,
        }

    def register_base_model(self, model_name: str, model: Any, initial_accuracy: float = 0.5):
        """Register a base model for ensemble learning"""
        if model_name in self.base_models:
            logger.warning(f"Model {model_name} already registered")
            return

        self.base_models[model_name] = BaseModelInfo(
            name=model_name,
            model=model,
            accuracy=initial_accuracy,
            precision=initial_accuracy,
            recall=initial_accuracy,
            f1_score=initial_accuracy,
            prediction_error=1.0 - initial_accuracy,
            feature_importance=[0.0] * 10,  # Placeholder
            last_trained=time.time(),
            training_samples=0,
        )
        logger.info(f"Registered base model: {model_name}")

    def generate_training_samples(self, training_data: Dict[str, List[dict]]) -> List[TrainingSample]:
        """Generate training samples from training data"""
        samples = []

        for symbol, data_points in training_data.items():
            if len(data_points) < 10:
                continue

            for i in range(len(data_points) - 1):
                # Use this point as current observation
                current_point = data_points[i]

                # Get predictions from all base models (simplified - in practice, you'd use actual model predictions)
                model_predictions = {}
                for model_name, model_info in self.base_models.items():
                    # Generate synthetic prediction based on simple heuristic
                    prediction = self._generate_model_prediction(model_name, current_point)
                    model_predictions[model_name] = prediction

                # Generate meta-features
                meta_features = self._generate_meta_features(symbol, current_point, model_predictions)

                # Create training sample
                sample = TrainingSample(
                    features=[
                        current_point.get('price_change_5m', 0),
                        current_point.get('volume_5m', 0),
                        current_point.get('price_change_1h', 0),
                        current_point.get('volume_1h', 0),
                        current_point.get('social_sentiment_score', 0),
                        current_point.get('fear_greed_index', 0),
                    ],
                    target=current_point.get('price_change_5m', 0),
                    timestamp=current_point.get('timestamp', time.time()),
                    model_predictions=model_predictions,
                    meta_features=meta_features,
                    source_symbol=symbol,
                )

                samples.append(sample)

        logger.info(f"Generated {len(samples)} training samples")
        return samples

    def _generate_model_prediction(self, model_name: str, data_point: dict) -> float:
        # Simplified prediction generation based on simple heuristics
        # In practice, this would use actual model predictions
        weights = {
            'mlp_regressor': 0.4,
            'gradient_boosting': 0.3,
            'random_forest': 0.3,
        }

        base_prediction = 0.0
        weight_sum = 0.0

        for m_name, weight in weights.items():
            if m_name == 'mlp_regressor':
                pred = self._mlp_prediction(data_point)
            elif m_name == 'gradient_boosting':
                pred = self._gb_prediction(data_point)
            else:
                pred = self._rf_prediction(data_point)

            base_prediction += pred * weight
            weight_sum += weight

        return base_prediction / weight_sum if weight_sum > 0 else 0.0

    def _mlp_prediction(self, data_point: dict) -> float:
        # Simple MLP-like prediction
        price_change = data_point.get('price_change_5m', 0)
        sentiment = data_point.get('social_sentiment_score', 0)
        volume = data_point.get('volume_5m', 0)

        # Non-linear combination
        pred = (price_change * 0.5 + 
                sentiment * 0.3 + 
                np.tanh(volume / 10000) * 0.2)

        return pred

    def _gb_prediction(self, data_point: dict) -> float:
        # Gradient boosting-like prediction
        price_change = data_point.get('price_change_5m', 0)
        sentiment = data_point.get('social_sentiment_score', 0)

        # Tree-like decision
        if price_change > 5 and sentiment > 0.5:
            return 8.0
        elif price_change > 2:
            return 4.0
        elif price_change < -5 and sentiment < -0.5:
            return -8.0
        elif price_change < -2:
            return -4.0
        else:
            return 0.0

    def _rf_prediction(self, data_point: dict) -> float:
        # Random forest-like prediction
        price_change = data_point.get('price_change_5m', 0)
        volume = data_point.get('volume_5m', 0)

        # Vote across multiple decision trees
        predictions = []
        for threshold in [-10, -5, 0, 5, 10]:
            if price_change > threshold * 0.8 and volume > threshold * 100:
                predictions.append(threshold * 0.5)

        return np.mean(predictions) if predictions else 0.0

    def _generate_meta_features(self, symbol: str, data_point: dict,
                               model_predictions: Dict[str, float]) -> Dict[str, float]:
        meta_features = {}

        for generator_name, generator_func in self.meta_feature_generators.items():
            meta_features[generator_name] = generator_func(symbol, data_point, model_predictions)

        return meta_features

    def _generate_model_agreement(self, symbol: str, data_point: dict,
                                  model_predictions: Dict[str, float]) -> float:
        if len(model_predictions) < 2:
            return 1.0

        pred_values = list(model_predictions.values())
        mean_pred = np.mean(pred_values)

        # Calculate agreement (1 - normalized std deviation)
        agreement = 1.0 - min(np.std(pred_values) / np.std(pred_values) if np.std(pred_values) > 0 else 0, 1.0)

        return agreement

    def _generate_prediction_consensus(self, symbol: str, data_point: dict,
                                       model_predictions: Dict[str, float]) -> float:
        if len(model_predictions) < 2:
            return 0.5

        pred_values = list(model_predictions.values())

        # Calculate consensus based on directional agreement
        bullish_count = sum(1 for p in pred_values if p > 0)
        bearish_count = sum(1 for p in pred_values if p < 0)
        neutral_count = sum(1 for p in pred_values if p == 0)

        total = len(pred_values)
        if total == 0:
            return 0.5

        consensus = max(bullish_count, bearish_count, neutral_count) / total

        return consensus

    def _generate_model_uncertainty(self, symbol: str, data_point: dict,
                                    model_predictions: Dict[str, float]) -> float:
        if len(model_predictions) < 2:
            return 0.0

        pred_values = list(model_predictions.values())

        # Uncertainty based on standard deviation
        uncertainty = np.std(pred_values)

        # Normalize to 0-1 scale
        return min(uncertainty / 5.0, 1.0)

    def _generate_feature_diversity(self, symbol: str, data_point: dict,
                                    model_predictions: Dict[str, float]) -> float:
        # This would require tracking which features each model uses
        # Simplified version: based on number of similar predictions
        return 0.7  # Placeholder

    def _generate_historical_accuracy(self, symbol: str, data_point: dict,
                                     model_predictions: Dict[str, float]) -> float:
        # This would require access to historical performance data
        # Simplified version: based on current prediction confidence
        return 0.6  # Placeholder

    def create_ensemble_model(self, name: str, ensemble_type: str = 'voting_regressor',
                            model_names: List[str] = None) -> EnsembleModel:
        if model_names is None:
            model_names = list(self.base_models.keys())

        if not model_names:
            raise ValueError("No base models available for ensemble")

        # Create ensemble based on type
        if ensemble_type == 'voting_regressor':
            model = VotingRegressor(
                estimators=[(name, self.base_models[name].model) for name in model_names]
            )
        elif ensemble_type == 'stacking_regressor':
            base_estimators = []
            for model_name in model_names[:-1]:
                base_estimators.append((model_name, self.base_models[model_name].model))

            final_estimator = LinearRegression()
            model = StackingRegressor(
                estimators=base_estimators,
                final_estimator=final_estimator,
            )
        elif ensemble_type == 'blending_regressor':
            # Blending is more complex, simplified version
            model = LinearRegression()
        else:
            raise ValueError(f"Unknown ensemble type: {ensemble_type}")

        # Create ensemble model
        ensemble = EnsembleModel(
            name=name,
            model=model,
            base_models=[self.base_models[name] for name in model_names],
            meta_features={},
            performance_history=[],
            created_at=time.time(),
            last_updated=time.time(),
            configuration={
                'ensemble_type': ensemble_type,
                'base_models': model_names,
            },
        )

        self.ensemble_models[name] = ensemble

        logger.info(f"Created ensemble model: {name} with {len(model_names)} base models")
        return ensemble

    def train_ensemble_models(self, training_samples: List[TrainingSample]) -> Dict[str, dict]:
        if not training_samples:
            return {}

        results = {}

        for ensemble_name, ensemble in self.ensemble_models.items():
            try:
                # Prepare training data
                X = [sample.features for sample in training_samples]
                y = [sample.target for sample in training_samples]

                # Train ensemble model
                ensemble.model.fit(X, y)

                # Update meta-features
                ensemble.meta_features = self._calculate_meta_features_for_ensemble(
                    ensemble, training_samples
                )

                # Calculate performance
                train_score = ensemble.model.score(X, y)

                # Record performance
                performance_record = {
                    'timestamp': time.time(),
                    'train_score': train_score,
                    'training_samples': len(training_samples),
                    'ensemble_size': len(ensemble.base_models),
                }

                ensemble.performance_history.append(performance_record)
                ensemble.last_updated = time.time()

                # Update base model scores
                for base_model in ensemble.base_models:
                    # In practice, you would calculate actual base model performance
                    base_model.accuracy = min(base_model.accuracy + 0.01, 1.0)
                    base_model.last_trained = time.time()

                results[ensemble_name] = {
                    'train_score': train_score,
                    'base_models_count': len(ensemble.base_models),
                    'performance_history': performance_record,
                }

                logger.info(f"Trained ensemble model: {ensemble_name} with score {train_score:.3f}")

            except Exception as e:
                logger.error(f"Error training ensemble model {ensemble_name}: {e}")
                continue

        # Save models
        self._save()

        return results

    def _calculate_meta_features_for_ensemble(self, ensemble: EnsembleModel,
                                            training_samples: List[TrainingSample]) -> Dict[str, float]:
        meta_features = {}

        for generator_name in self.meta_feature_generators.keys():
            meta_features[generator_name] = self._generate_meta_feature_for_ensemble(
                generator_name, ensemble, training_samples
            )

        return meta_features

    def _generate_meta_feature_for_ensemble(self, generator_name: str,
                                          ensemble: EnsembleModel,
                                          training_samples: List[TrainingSample]) -> float:
        if generator_name == 'model_agreement':
            # Calculate agreement across base models on training samples
            agreements = []
            for sample in training_samples:
                pred_values = list(sample.model_predictions.values())
                if len(pred_values) >= 2:
                    mean_pred = np.mean(pred_values)
                    agreement = 1.0 - min(np.std(pred_values) / np.std(pred_values) if np.std(pred_values) > 0 else 0, 1.0)
                    agreements.append(agreement)

            return np.mean(agreements) if agreements else 0.5

        elif generator_name == 'prediction_consensus':
            # Calculate consensus on target variable
            target_values = [sample.target for sample in training_samples]
            pred_consensus = sum(1 for t in target_values if abs(t) < 1.0) / len(target_values)
            return pred_consensus

        else:
            # Default values for other generators
            return 0.5

    def predict_with_ensemble(self, ensemble_name: str, X: List[float]) -> Optional[float]:
        if ensemble_name not in self.ensemble_models:
            return None

        ensemble = self.ensemble_models[ensemble_name]

        try:
            return ensemble.model.predict([X])[0]
        except Exception as e:
            logger.error(f"Error predicting with ensemble {ensemble_name}: {e}")
            return None

    def get_ensemble_recommendations(self, symbol: str, features: Dict[str, float],
                                    ensemble_name: str = None) -> Dict[str, Any]:
        if not self.ensemble_models:
            return {'status': 'no_ensembles', 'message': 'No ensemble models available'}

        # Use default ensemble if not specified
        if ensemble_name is None:
            ensemble_name = next(iter(self.ensemble_models.keys()))

        ensemble = self.ensemble_models[ensemble_name]

        # Prepare features
        X = self._prepare_features_for_prediction(features)
        if X is None:
            return {'status': 'feature_error', 'message': 'Could not prepare features'}

        # Get prediction
        prediction = self.predict_with_ensemble(ensemble_name, X)
        if prediction is None:
            return {'status': 'prediction_error', 'message': 'Could not generate prediction'}

        # Calculate confidence based on base model agreement
        confidence = self._calculate_prediction_confidence(ensemble, X)

        # Determine recommendation
        if prediction > 5:
            recommendation = 'strong_buy'
            risk_level = 'high'
        elif prediction > 2:
            recommendation = 'buy'
            risk_level = 'medium'
        elif prediction < -5:
            recommendation = 'strong_sell'
            risk_level = 'high'
        elif prediction < -2:
            recommendation = 'sell'
            risk_level = 'medium'
        else:
            recommendation = 'hold'
            risk_level = 'low'

        return {
            'symbol': symbol,
            'prediction': prediction,
            'confidence': confidence,
            'recommendation': recommendation,
            'risk_level': risk_level,
            'ensemble_name': ensemble_name,
            'base_models': [bm.name for bm in ensemble.base_models],
        }

    def _prepare_features_for_prediction(self, features: Dict[str, float]) -> Optional[List[float]]:
        # Similar to prepare_features in NeuralNetworkEngine
        feature_vector = []

        for key in FEATURE_CONFIG['price_features']:
            if key in features:
                feature_vector.append(features[key])
            else:
                feature_vector.append(0.0)

        for key in FEATURE_CONFIG['technical_features']:
            if key in features:
                feature_vector.append(features[key])
            else:
                feature_vector.append(0.0)

        for key in FEATURE_CONFIG['sentiment_features']:
            if key in features:
                feature_vector.append(features[key])
            else:
                feature_vector.append(0.0)

        for key in FEATURE_CONFIG['market_features']:
            if key in features:
                feature_vector.append(features[key])
            else:
                feature_vector.append(0.0)

        for key in FEATURE_CONFIG['wallet_features']:
            if key in features:
                feature_vector.append(features[key])
            else:
                feature_vector.append(0.0)

        return feature_vector if feature_vector else None

    def _calculate_prediction_confidence(self, ensemble: EnsembleModel, X: List[float]) -> float:
        # Calculate confidence based on base model agreement and individual predictions
        if not ensemble.base_models:
            return 0.5

        # Get predictions from base models
        base_predictions = []
        for base_model in ensemble.base_models:
            try:
                # Simplified prediction
                base_pred = self._generate_model_prediction(base_model.name, {})
                base_predictions.append(base_pred)
            except:
                continue

        if not base_predictions:
            return 0.5

        # Calculate agreement
        pred_mean = np.mean(base_predictions)
        pred_std = np.std(base_predictions) if len(base_predictions) > 1 else 0.0

        # Confidence based on agreement (1 - normalized std deviation)
        confidence = 1.0 - min(pred_std / 5.0, 1.0)

        return confidence

    def save(self):
        self._save()

    def cleanup_old_data(self, max_age_days: float = 30):
        cutoff = time.time() - (max_age_days * 86400)

        # Clean training samples
        self.training_samples = [
            s for s in self.training_samples if s.timestamp >= cutoff
        ]

        # Clean ensemble performance history
        for ensemble in self.ensemble_models.values():
            ensemble.performance_history = [
                p for p in ensemble.performance_history if p['timestamp'] >= cutoff
            ]

        logger.info(f"Cleaned up old ensemble data: {len(self.training_samples)} training samples")

    def get_summary_stats(self) -> dict:
        return {
            'base_models_count': len(self.base_models),
            'ensemble_models_count': len(self.ensemble_models),
            'training_samples_count': len(self.training_samples),
            'average_base_accuracy': np.mean([m.accuracy for m in self.base_models.values()]) if self.base_models else 0,
            'last_training': max([m.last_trained for m in self.base_models.values()], default=time.time()),
        }


ensemble_learner = EnsembleLearner()
