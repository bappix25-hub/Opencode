import numpy as np
import pandas as pd
import logging
import os
import json
import time
import pickle
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple
from sklearn.preprocessing import StandardScaler, MinMaxScaler
from sklearn.neural_network import MLPRegressor
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_squared_error, mean_absolute_error
import warnings
warnings.filterwarnings('ignore')

logger = logging.getLogger("neural_engine")

NEURAL_MODELS_FILE = os.path.join(os.path.dirname(__file__), "data", "neural_models.json")
MODEL_PERFORMANCE_FILE = os.path.join(os.path.dirname(__file__), "data", "model_performance.json")

# Feature engineering configuration
FEATURE_CONFIG = {
    'price_features': ['price_change_5m', 'price_change_1h', 'price_change_24h', 'volume_5m', 'volume_1h'],
    'technical_features': ['sma', 'ema', 'rsi', 'macd', 'bollinger_width', 'atr', 'stochastics_k', 'williams_r'],
    'sentiment_features': ['social_sentiment_score', 'mention_count', 'positive_mentions', 'negative_mentions'],
    'market_features': ['market_cap_change_24h', 'total_volume_24h', 'fear_greed_index'],
    'wallet_features': ['high_risk_wallets', 'suspicious_patterns_count'],
}

# Model configuration
MODEL_CONFIGS = {
    'mlp_regressor': {
        'hidden_layer_sizes': (100, 50, 25),
        'activation': 'relu',
        'solver': 'adam',
        'alpha': 0.0001,
        'batch_size': 'auto',
        'learning_rate': 0.001,
        'max_iter': 500,
        'random_state': 42,
        'early_stopping': True,
        'validation_fraction': 0.1,
    },
    'gradient_boosting': {
        'n_estimators': 200,
        'learning_rate': 0.05,
        'max_depth': 6,
        'subsample': 0.8,
        'random_state': 42,
    },
    'random_forest': {
        'n_estimators': 100,
        'max_depth': 20,
        'min_samples_split': 5,
        'min_samples_leaf': 2,
        'random_state': 42,
    },
}

# Training configuration
TRAINING_CONFIG = {
    'lookback_period': 20,
    'forecast_horizon': 1,
    'train_size': 0.7,
    'test_size': 0.3,
    'cross_validation_folds': 3,
    'early_stopping_patience': 10,
    'min_improvement': 0.001,
}


@dataclass
class NeuralNetworkModel:
    name: str
    model: Any
    scaler_X: StandardScaler
    scaler_y: MinMaxScaler
    feature_names: List[str]
    training_history: List[dict]
    best_score: float
    last_updated: float
    performance_metrics: dict

    def predict(self, X):
        return self.model.predict(self.scaler_X.transform(X))

    def predict_proba(self, X):
        if hasattr(self.model, 'predict_proba'):
            return self.model.predict_proba(self.scaler_X.transform(X))
        return None

    def score(self, X, y):
        return self.model.score(self.scaler_X.transform(X), self.scaler_y.inverse_transform(y))


@dataclass
class PredictionResult:
    symbol: str
    timestamp: float
    predicted_price_change: float
    predicted_volume_change: float
    confidence_score: float
    model_agreement: float
    ensemble_prediction: float
    individual_predictions: Dict[str, float]
    features_used: Dict[str, float]
    risk_assessment: str


class NeuralNetworkEngine:
    def __init__(self):
        self.models: Dict[str, NeuralNetworkModel] = {}
        self.feature_scalers: Dict[str, StandardScaler] = {}
        self.target_scalers: Dict[str, MinMaxScaler] = {}
        self.feature_cache: Dict[str, List[List[float]]] = {}
        self.target_cache: Dict[str, List[float]] = {}
        self.model_performance: Dict[str, dict] = {}
        self.prediction_history: List[PredictionResult] = []

        self._load_models()

    def _load_models(self):
        try:
            if os.path.exists(NEURAL_MODELS_FILE):
                with open(NEURAL_MODELS_FILE, "r") as f:
                    data = json.load(f)

                for model_name, model_data in data.get("models", {}).items():
                    # Recreate scaler instances
                    scaler_X = StandardScaler()
                    scaler_y = MinMaxScaler()

                    # Note: In production, you would need to save/load scaler parameters
                    # For this implementation, we'll create new scalers
                    # In a real system, you would save the scaler's fit parameters

                    # Create model
                    model_config = MODEL_CONFIGS.get(model_name, {})
                    if model_name == 'mlp_regressor':
                        model = MLPRegressor(**model_config)
                    elif model_name == 'gradient_boosting':
                        model = GradientBoostingRegressor(**model_config)
                    elif model_name == 'random_forest':
                        model = RandomForestRegressor(**model_config)
                    else:
                        continue

                    # Create model object
                    neural_model = NeuralNetworkModel(
                        name=model_name,
                        model=model,
                        scaler_X=scaler_X,
                        scaler_y=scaler_y,
                        feature_names=FEATURE_CONFIG['price_features'] + \
                                    FEATURE_CONFIG['technical_features'] + \
                                    FEATURE_CONFIG['sentiment_features'] + \
                                    FEATURE_CONFIG['market_features'] + \
                                    FEATURE_CONFIG['wallet_features'],
                        training_history=[],
                        best_score=0.0,
                        last_updated=time.time(),
                        performance_metrics={},
                    )

                    self.models[model_name] = neural_model

                logger.info(f"Neural models loaded: {len(self.models)}")

        except Exception as e:
            logger.error(f"Error loading neural models: {e}")

    def _save_models(self):
        try:
            os.makedirs(os.path.dirname(NEURAL_MODELS_FILE), exist_ok=True)
            data = {
                "models": {
                    name: {
                        "hidden_layer_sizes": MODEL_CONFIGS[name]['hidden_layer_sizes'] if name == 'mlp_regressor' else None,
                        "activation": MODEL_CONFIGS[name]['activation'] if name == 'mlp_regressor' else None,
                        "n_estimators": MODEL_CONFIGS[name]['n_estimators'] if name in ['gradient_boosting', 'random_forest'] else None,
                        "max_depth": MODEL_CONFIGS[name]['max_depth'] if name in ['gradient_boosting', 'random_forest'] else None,
                    }
                    for name in self.models.keys()
                },
                "saved_at": time.time(),
            }
            with open(NEURAL_MODELS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving neural models: {e}")

    def prepare_features(self, symbol: str, features: Dict[str, float]) -> Optional[List[float]]:
        try:
            # Extract features based on configuration
            feature_vector = []

            # Add price features
            for feat in FEATURE_CONFIG['price_features']:
                if feat in features:
                    feature_vector.append(features[feat])
                else:
                    feature_vector.append(0.0)

            # Add technical features
            for feat in FEATURE_CONFIG['technical_features']:
                if feat in features:
                    feature_vector.append(features[feat])
                else:
                    feature_vector.append(0.0)

            # Add sentiment features
            for feat in FEATURE_CONFIG['sentiment_features']:
                if feat in features:
                    feature_vector.append(features[feat])
                else:
                    feature_vector.append(0.0)

            # Add market features
            for feat in FEATURE_CONFIG['market_features']:
                if feat in features:
                    feature_vector.append(features[feat])
                else:
                    feature_vector.append(0.0)

            # Add wallet features
            for feat in FEATURE_CONFIG['wallet_features']:
                if feat in features:
                    feature_vector.append(features[feat])
                else:
                    feature_vector.append(0.0)

            return feature_vector

        except Exception as e:
            logger.error(f"Error preparing features for {symbol}: {e}")
            return None

    def train_models(self, training_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, dict]:
        results = {}

        for symbol, data_points in training_data.items():
            if len(data_points) < TRAINING_CONFIG['lookback_period'] + TRAINING_CONFIG['forecast_horizon']:
                logger.debug(f"Insufficient data for {symbol}: {len(data_points)} samples")
                continue

            # Prepare features and targets
            X = []
            y_price_change = []
            y_volume_change = []

            for i in range(len(data_points) - TRAINING_CONFIG['lookback_period']):
                window_data = data_points[i:i + TRAINING_CONFIG['lookback_period']]
                future_data = data_points[i + TRAINING_CONFIG['lookback_period']]

                # Create feature vector
                feature_vector = []
                for key in FEATURE_CONFIG['price_features']:
                    feature_vector.append(window_data[-1].get(key, 0.0))
                for key in FEATURE_CONFIG['technical_features']:
                    feature_vector.append(window_data[-1].get(f"tech_{key}", 0.0))
                for key in FEATURE_CONFIG['sentiment_features']:
                    feature_vector.append(window_data[-1].get(f"sent_{key}", 0.0))
                for key in FEATURE_CONFIG['market_features']:
                    feature_vector.append(window_data[-1].get(f"market_{key}", 0.0))
                for key in FEATURE_CONFIG['wallet_features']:
                    feature_vector.append(window_data[-1].get(f"wallet_{key}", 0.0))

                X.append(feature_vector)

                # Calculate target variables
                current_price = window_data[-1].get('price_usd', 1.0)
                future_price = future_data.get('price_usd', current_price)
                price_change = (future_price - current_price) / current_price * 100

                current_volume = window_data[-1].get('volume_5m', 0)
                future_volume = future_data.get('volume_5m', current_volume)
                volume_change = (future_volume - current_volume) / current_volume * 100 if current_volume > 0 else 0

                y_price_change.append(price_change)
                y_volume_change.append(volume_change)

            if len(X) < 10:
                logger.debug(f"Not enough samples for model training for {symbol}: {len(X)}")
                continue

            # Split data
            split_idx = int(len(X) * TRAINING_CONFIG['train_size'])
            X_train, X_test = X[:split_idx], X[split_idx:]
            y_price_train, y_price_test = y_price_change[:split_idx], y_price_change[split_idx:]
            y_volume_train, y_volume_test = y_volume_change[:split_idx], y_volume_change[split_idx:]

            # Train models for each target
            for model_name in MODEL_CONFIGS.keys():
                try:
                    # Initialize and train price change model
                    model = MODEL_CONFIGS[model_name]
                    if model_name == 'mlp_regressor':
                        regressor = MLPRegressor(**model)
                    elif model_name == 'gradient_boosting':
                        regressor = GradientBoostingRegressor(**model)
                    elif model_name == 'random_forest':
                        regressor = RandomForestRegressor(**model)

                    # Fit model
                    regressor.fit(X_train, y_price_train)

                    # Calculate score on test set
                    score = regressor.score(X_test, y_price_test)

                    # Store model
                    if model_name not in self.models:
                        self.models[model_name] = NeuralNetworkModel(
                            name=model_name,
                            model=regressor,
                            scaler_X=StandardScaler(),
                            scaler_y=MinMaxScaler(),
                            feature_names=[f"feature_{i}" for i in range(len(X[0]))],
                            training_history=[],
                            best_score=score,
                            last_updated=time.time(),
                            performance_metrics={
                                'train_score': regressor.score(X_train, y_price_train),
                                'test_score': score,
                                'mae_train': mean_absolute_error(y_price_train, regressor.predict(X_train)),
                                'mae_test': mean_absolute_error(y_price_test, regressor.predict(X_test)),
                                'r2_train': regressor.score(X_train, y_price_train),
                                'r2_test': score,
                                'samples': len(X_train),
                                'symbols': list(training_data.keys()),
                            },
                        )
                    else:
                        # Update existing model with better performance
                        if score > self.models[model_name].best_score:
                            self.models[model_name].model = regressor
                            self.models[model_name].best_score = score
                            self.models[model_name].last_updated = time.time()
                            self.models[model_name].performance_metrics.update({
                                'latest_train_score': regressor.score(X_train, y_price_train),
                                'latest_test_score': score,
                                'latest_mae_train': mean_absolute_error(y_price_train, regressor.predict(X_train)),
                                'latest_mae_test': mean_absolute_error(y_price_test, regressor.predict(X_test)),
                                'train_score_history': self.models[model_name].performance_metrics.get('train_score_history', [])[-50:] + [score],
                            })
                            self.models[model_name].scaler_y.fit(np.array(y_price_train).reshape(-1, 1))

                except Exception as e:
                    logger.error(f"Error training model {model_name}: {e}")
                    continue

            # Train volume change model (optional)
            if model_name in self.models:
                volume_regressor = MODEL_CONFIGS[model_name]
                if model_name == 'mlp_regressor':
                    vol_regressor = MLPRegressor(**volume_regressor)
                elif model_name == 'gradient_boosting':
                    vol_regressor = GradientBoostingRegressor(**volume_regressor)
                elif model_name == 'random_forest':
                    vol_regressor = RandomForestRegressor(**volume_regressor)

                vol_regressor.fit(X_train, y_volume_train)

                self.models[model_name].volume_regressor = vol_regressor

        # Save models
        self._save_models()

        # Record performance
        for model_name, model in self.models.items():
            results[model_name] = {
                'symbols_trained': len([d for d in training_data.values() if len(d) >= TRAINING_CONFIG['lookback_period']]),
                'best_score': model.best_score,
                'training_history': model.training_history,
            }

        return results

    def predict(self, symbol: str, features: Dict[str, float]) -> Optional[PredictionResult]:
        if not self.models:
            return None

        # Prepare features
        feature_vector = self.prepare_features(symbol, features)
        if feature_vector is None:
            return None

        # Make predictions with each model
        predictions = {}
        confidences = []

        for model_name, model in self.models.items():
            try:
                # Price change prediction
                price_pred = model.predict([feature_vector])[0]

                # Get model confidence
                if hasattr(model.model, 'predict_proba'):
                    proba = model.model.predict_proba([feature_vector])[0]
                    confidence = np.max(proba)
                else:
                    # For tree-based models, calculate feature importance
                    feature_importance = model.model.feature_importances_
                    confidence = np.mean(feature_importance) if len(feature_importance) > 0 else 0.5

                predictions[model_name] = {
                    'price_change': price_pred,
                    'confidence': confidence,
                }

                confidences.append(confidence)

            except Exception as e:
                logger.error(f"Error predicting with model {model_name}: {e}")
                continue

        if not predictions:
            return None

        # Calculate ensemble prediction (weighted average)
        ensemble_pred = sum(p['price_change'] * p['confidence'] for p in predictions.values()) / \
                       sum(p['confidence'] for p in predictions.values()) if sum(p['confidence'] for p in predictions.values()) > 0 else 0

        # Calculate agreement
        pred_values = [p['price_change'] for p in predictions.values()]
        if len(pred_values) > 1:
            pred_std = np.std(pred_values)
            agreement = max(0, 1 - min(pred_std / 10, 1.0))  # Normalize to 0-1
        else:
            agreement = 1.0

        # Determine risk assessment
        avg_pred = np.mean(pred_values)
        if avg_pred > 5:
            risk_assessment = 'high_bullish'
        elif avg_pred < -5:
            risk_assessment = 'high_bearish'
        elif avg_pred > 2:
            risk_assessment = 'bullish'
        elif avg_pred < -2:
            risk_assessment = 'bearish'
        else:
            risk_assessment = 'neutral'

        # Calculate volume change prediction (using volume model if available)
        volume_pred = 0.0
        if hasattr(model, 'volume_regressor'):
            try:
                volume_pred = model.volume_regressor.predict([feature_vector])[0]
            except:
                volume_pred = 0.0

        return PredictionResult(
            symbol=symbol,
            timestamp=time.time(),
            predicted_price_change=ensemble_pred,
            predicted_volume_change=volume_pred,
            confidence_score=np.mean(confidences),
            model_agreement=agreement,
            ensemble_prediction=ensemble_pred,
            individual_predictions={k: v['price_change'] for k, v in predictions.items()},
            features_used=features,
            risk_assessment=risk_assessment,
        )

    def retrain_with_new_data(self, new_data: Dict[str, List[Dict[str, Any]]]):
        logger.info(f"Retraining neural models with new data: {len(new_data)} symbols")

        # Combine new data with existing data (if any)
        all_training_data = {}

        for symbol, data in new_data.items():
            if symbol in self.models:
                # Get existing data (simplified - in production you'd keep historical data)
                existing_data = []
                all_training_data[symbol] = data + existing_data
            else:
                all_training_data[symbol] = data

        # Retrain models
        self.train_models(all_training_data)

    def get_model_performance(self) -> Dict[str, Any]:
        performance = {}
        for model_name, model in self.models.items():
            performance[model_name] = {
                'name': model.name,
                'best_score': model.best_score,
                'last_updated': model.last_updated,
                'feature_count': len(model.feature_names),
                'training_samples': model.performance_metrics.get('samples', 0),
                'symbols_trained': model.performance_metrics.get('symbols', []),
                'model_type': type(model.model).__name__,
            }

        return performance

    def get_prediction_summary(self, symbol: str) -> Dict[str, Any]:
        recent_predictions = [
            p for p in self.prediction_history
            if p.symbol == symbol and p.timestamp > time.time() - 3600
        ]

        if not recent_predictions:
            return {
                'symbol': symbol,
                'status': 'no_predictions',
                'message': 'No recent predictions available',
            }

        latest = recent_predictions[-1]

        return {
            'symbol': symbol,
            'latest_prediction': {
                'price_change': latest.predicted_price_change,
                'confidence': latest.confidence_score,
                'agreement': latest.model_agreement,
                'risk_assessment': latest.risk_assessment,
                'timestamp': latest.timestamp,
            },
            'prediction_count': len(recent_predictions),
            'avg_confidence': sum(p.confidence_score for p in recent_predictions) / len(recent_predictions),
            'avg_agreement': sum(p.model_agreement for p in recent_predictions) / len(recent_predictions),
            'risk_distribution': {
                'high_bullish': sum(1 for p in recent_predictions if p.risk_assessment == 'high_bullish'),
                'bullish': sum(1 for p in recent_predictions if p.risk_assessment == 'bullish'),
                'neutral': sum(1 for p in recent_predictions if p.risk_assessment == 'neutral'),
                'bearish': sum(1 for p in recent_predictions if p.risk_assessment == 'bearish'),
                'high_bearish': sum(1 for p in recent_predictions if p.risk_assessment == 'high_bearish'),
            },
        }

    def save(self):
        self._save_models()

    def cleanup_old_data(self, max_age_days: float = 30):
        cutoff = time.time() - (max_age_days * 86400)

        # Clean prediction history
        self.prediction_history = [
            p for p in self.prediction_history if p.timestamp >= cutoff
        ]

        # Note: Model parameters are retained for continuous learning
        # only predictions are cleaned up to save memory

        logger.info(f"Cleaned up old neural model data: {len(self.prediction_history)} predictions")

    def get_summary_stats(self) -> dict:
        return {
            'models_count': len(self.models),
            'predictions_count': len(self.prediction_history),
            'last_updated': max([m.last_updated for m in self.models.values()], default=time.time()),
            'average_model_score': np.mean([m.best_score for m in self.models.values()]) if self.models else 0,
            'model_types': [type(m.model).__name__ for m in self.models.values()],
        }


neural_engine = NeuralNetworkEngine()
