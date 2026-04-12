from sqlalchemy import Column, Integer, String, Float, Date, Time, Text, ForeignKey, DateTime, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base
from datetime import datetime



Base = declarative_base()


class FoodDBItem(Base):
    __tablename__ = "food_db_items"
    id = Column(Integer, primary_key=True)
    barcode = Column(String(20))
    product_name = Column(String(500), nullable=False)
    brand = Column(String(255))
    calories = Column(Float)
    protein_g = Column(Float)
    carbs_g = Column(Float)
    fat_g = Column(Float)
    fiber_g = Column(Float)
    sugar_g = Column(Float)
    saturated_fat_g = Column(Float)
    serving_size_g = Column(Float)
    calcium_mg = Column(Float)
    magnesium_mg = Column(Float)
    iron_mg = Column(Float)
    zinc_mg = Column(Float)
    potassium_mg = Column(Float)
    sodium_mg = Column(Float)
    phosphorus_mg = Column(Float)
    vitamin_a_mcg = Column(Float)
    vitamin_c_mg = Column(Float)
    vitamin_d_mcg = Column(Float)
    vitamin_b12_mcg = Column(Float)
    folate_mcg = Column(Float)
    source = Column(String(50))
    values_per = Column(String(15))
    source_id = Column(String(255))
    data = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MealLog(Base):
    __tablename__ = "meals_log"
    id = Column(Integer, primary_key=True)
    meal_name = Column(String(255), nullable=False)
    meal_category = Column(String(30))
    meal_date = Column(Date, nullable=False)
    meal_time = Column(Time)
    calories = Column(Float)
    protein_g = Column(Float)
    carbs_g = Column(Float)
    fat_g = Column(Float)
    fiber_g = Column(Float)
    sugar_g = Column(Float)
    calcium_mg = Column(Float)
    magnesium_mg = Column(Float)
    iron_mg = Column(Float)
    source = Column(String(50))
    confidence_score = Column(Float)
    food_db_item_id = Column(Integer, ForeignKey("food_db_items.id"))
    original_photo_path = Column(String(500))
    notes = Column(Text)
    data = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow)


class ExerciseLog(Base):
    __tablename__ = "exercise_log"
    id = Column(Integer, primary_key=True)
    exercise_date = Column(Date, nullable=False)
    exercise_time = Column(Time)
    activity = Column(String(255), nullable=False)
    duration_min = Column(Integer)
    calories = Column(Integer)
    source = Column(String(50), default="screenshot")
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("exercise_date", "exercise_time", "activity", name="uq_exercise_dedupe"),
        Index("ix_exercise_log_date", "exercise_date"),
    )


class FitbitDailyStats(Base):
    __tablename__ = "fitbit_daily_stats"
    id = Column(Integer, primary_key=True)
    stat_date = Column(Date, nullable=False, unique=True)
    steps = Column(Integer)
    resting_hr = Column(Integer)
    activity_calories = Column(Integer)
    calories_out = Column(Integer)
    lightly_active_min = Column(Integer)
    fairly_active_min = Column(Integer)
    very_active_min = Column(Integer)
    sedentary_min = Column(Integer)
    distance_km = Column(Float)
    sleep_minutes = Column(Integer)
    sleep_deep_min = Column(Integer)
    sleep_light_min = Column(Integer)
    sleep_rem_min = Column(Integer)
    sleep_efficiency = Column(Integer)
    sleep_start = Column(Time)
    sleep_end = Column(Time)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (Index("ix_fitbit_daily_stats_date", "stat_date"),)


class ConversationHistory(Base):
    __tablename__ = "conversation_history"
    id = Column(Integer, primary_key=True)
    message_text = Column(Text, nullable=False)
    response_text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
