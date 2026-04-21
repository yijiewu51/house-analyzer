"""
ML price prediction model using GradientBoostingRegressor.
Trained on community listings to predict fair unit price per sqm.
"""
import logging
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_percentage_error
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    predicted_price: int
    confidence_low: int
    confidence_high: int
    discount_pct: float  # positive means actual < predicted (undervalued)
    price_value_score: float  # 0-100
    feature_importances: dict
    mape: Optional[float] = None  # model accuracy on test set


ORIENTATION_ENCODING = {
    "南北通透": 6, "南向": 5, "东南": 4, "西南": 3,
    "东向": 2, "西向": 1, "北向": 0, "东北": 0, "西北": 0,
}

DECORATION_ENCODING = {
    "豪华装修": 3, "精装修": 2, "简装修": 1, "毛坯": 0,
}

SCHOOL_TIER_ENCODING = {
    "顶级学区": 3, "优质学区": 2, "普通学区": 1, "弱学区": 0, "无学区信息": 1,
}


def _encode_features(listings_df: pd.DataFrame) -> pd.DataFrame:
    df = listings_df.copy()
    df = df.assign(
        orientation_enc=df["orientation"].map(ORIENTATION_ENCODING).fillna(2),
        decoration_enc=df["decoration"].map(DECORATION_ENCODING).fillna(1),
        school_tier_enc=(
            df["school_tier"].map(SCHOOL_TIER_ENCODING).fillna(1)
            if "school_tier" in df.columns
            else pd.Series([1] * len(df), index=df.index)
        ),
        room_count=df["layout"].str.extract(r"(\d+)室")[0].astype(float).fillna(2),
    )
    return df


FEATURE_COLS = [
    "area_sqm", "floor_ratio", "age_years",
    "orientation_enc", "decoration_enc", "school_tier_enc",
    "room_count",
    "amenity_score", "noise_score", "sunlight_score",
]


class PriceModel:
    def __init__(self):
        self.model = None
        self.feature_cols = FEATURE_COLS
        self.mape = None
        self.district_avg = None

    def train(self, listings: list[dict]) -> "PriceModel":
        df = pd.DataFrame(listings)
        df = _encode_features(df)

        # Ensure enrichment score columns exist (may be missing for scraped data)
        for col in ["amenity_score", "noise_score", "sunlight_score"]:
            if col not in df.columns:
                df[col] = 65.0

        # Filter extreme outliers
        q_low = df["unit_price_sqm"].quantile(0.05)
        q_high = df["unit_price_sqm"].quantile(0.95)
        df = df[(df["unit_price_sqm"] >= q_low) & (df["unit_price_sqm"] <= q_high)]

        self.district_avg = int(df["unit_price_sqm"].mean())

        if len(df) < 8:
            logger.warning(f"Only {len(df)} samples — using heuristic model")
            self.model = None
            return self

        available_cols = [c for c in self.feature_cols if c in df.columns]
        X = df[available_cols].fillna(df[available_cols].median())
        y = df["unit_price_sqm"]

        if len(df) >= 20:
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        else:
            X_train, X_test, y_train, y_test = X, X, y, y

        self.model = GradientBoostingRegressor(
            n_estimators=120,
            learning_rate=0.08,
            max_depth=4,
            subsample=0.85,
            random_state=42,
        )
        self.model.fit(X_train, y_train)

        y_pred_test = self.model.predict(X_test)
        self.mape = mean_absolute_percentage_error(y_test, y_pred_test)
        self.available_cols = available_cols
        logger.info(f"Price model trained on {len(X_train)} samples, MAPE={self.mape:.2%}")
        return self

    def predict(self, listing: dict) -> PredictionResult:
        df = pd.DataFrame([listing])
        df = _encode_features(df)

        for col in ["amenity_score", "noise_score", "sunlight_score"]:
            if col not in df.columns:
                df[col] = 65.0

        actual_price = listing.get("unit_price_sqm", self.district_avg or 60000)

        if self.model is None:
            # Heuristic fallback
            predicted = self._heuristic_predict(listing)
        else:
            cols = getattr(self, "available_cols", self.feature_cols)
            available = [c for c in cols if c in df.columns]
            X = df[available].fillna(65)
            predicted = int(self.model.predict(X)[0])

        # Confidence interval: ±8% (simplified)
        margin = predicted * 0.08
        conf_low = int(predicted - margin)
        conf_high = int(predicted + margin)

        discount_pct = (predicted - actual_price) / predicted * 100

        # Price value score: sigmoid of discount %
        price_value_score = _sigmoid(discount_pct / 8) * 100

        # Feature importances
        importances = {}
        if self.model is not None:
            cols = getattr(self, "available_cols", self.feature_cols)
            available = [c for c in cols if c in df.columns]
            for col, imp in zip(available, self.model.feature_importances_):
                importances[col] = round(float(imp), 4)
        else:
            importances = {"area_sqm": 0.25, "orientation_enc": 0.20, "floor_ratio": 0.15,
                           "decoration_enc": 0.15, "age_years": 0.10, "school_tier_enc": 0.10,
                           "amenity_score": 0.05}

        return PredictionResult(
            predicted_price=predicted,
            confidence_low=conf_low,
            confidence_high=conf_high,
            discount_pct=round(discount_pct, 2),
            price_value_score=round(price_value_score, 1),
            feature_importances=importances,
            mape=self.mape,
        )

    def _heuristic_predict(self, listing: dict) -> int:
        base = self.district_avg or 60000
        orient_mult = {6: 1.08, 5: 1.03, 4: 1.02, 3: 1.00, 2: 0.98, 1: 0.95, 0: 0.92}
        decor_mult = {3: 1.10, 2: 1.05, 1: 1.00, 0: 0.94}
        orient_enc = ORIENTATION_ENCODING.get(listing.get("orientation", "东向"), 2)
        decor_enc = DECORATION_ENCODING.get(listing.get("decoration", "简装修"), 1)
        age_disc = max(0, listing.get("age_years", 10) * 0.007)
        floor_ratio = listing.get("floor_ratio", 0.5)
        floor_mult = 1.01 if floor_ratio > 0.5 else (0.97 if floor_ratio < 0.15 else 1.0)
        return int(base * orient_mult.get(orient_enc, 1.0) * decor_mult.get(decor_enc, 1.0)
                   * (1 - age_disc) * floor_mult)


def _sigmoid(x: float) -> float:
    return 1 / (1 + np.exp(-x))
