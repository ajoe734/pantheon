import json
import random
from datetime import datetime, timedelta

def generate_data():
    instruments = [f"TWSE_{i:04d}" for i in range(50)]
    start_date = datetime(2024, 1, 2)
    periods = 750
    
    data = []
    for inst in instruments:
        price = random.uniform(10, 1000)
        for i in range(periods):
            date = start_date + timedelta(days=i)
            # Skip weekends
            if date.weekday() >= 5:
                continue
            
            open_p = price
            close_p = open_p * (1 + random.uniform(-0.02, 0.02))
            high_p = max(open_p, close_p) * (1 + random.uniform(0, 0.01))
            low_p = min(open_p, close_p) * (1 - random.uniform(0, 0.01))
            vol = random.randint(100000, 1000000)
            
            data.append({
                "instrument": inst,
                "date": date.strftime("%Y-%m-%d"),
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": vol
            })
            price = close_p
            
    return {
        "dataset_id": "dataset:tw-equity-ohlcv-top50-2024-daily",
        "strategy_id": "tw-cross-sectional-equity-alpha",
        "source_strategy_spec_id": "qlib-tw-cross-sectional-alpha-spec-v1",
        "data_frequency": "daily",
        "source_dataset_refs": ["dataset:tw-equity-ohlcv-top50-2024-daily"],
        "records": data
    }

with open("services/research/qlib/examples/smoke_dataset.json", "w") as f:
    json.dump(generate_data(), f, indent=2)
