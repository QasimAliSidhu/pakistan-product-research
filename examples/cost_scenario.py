"""Calculate fictional PKR scenarios using the real economics function."""
from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analytics import economics
inputs=json.loads(Path(__file__).with_name('fictional_inputs.json').read_text())
for price in [1000,1100]:
    result=economics(dict(inputs,sale_price=price))
    print(json.dumps(dict(sample_price=price,contribution=result['expected_order_profit'],startup_cash=result['investment'],warnings=result['warnings']),indent=2))
