"""PKR cash economics and conservative, backtested monthly forecasts."""
import calendar
import math
from datetime import date
import numpy as np

DEFAULTS = dict(sale_price=0, purchase_cost=0, inbound=0, duty=0, packaging=0,
    shipping=0, commission_pct=0, payment_pct=0, fee_tax_pct=0,
    withholding_pct=0, other_fee=0, ad_cost=0, return_pct=0,
    return_loss=0, monthly_units=0, order_qty=50, moq=1, fixed_monthly=0,
    setup_cost=0, buffer_pct=15, payout_days=14, supplier_lead_days=14,
    budget=100000, verified_fees=False, fee_source='', fee_date='',
    supplier_name='', supplier_url='', quote_date='', verified_quote=False)

def validate_inputs(raw):
    out = DEFAULTS.copy()
    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        if isinstance(default, bool):
            if not isinstance(raw[key], bool):
                raise ValueError(f'{key} must be true or false')
            out[key] = raw[key]
        elif isinstance(default, str):
            out[key] = str(raw[key])[:1000]
        else:
            value = float(raw[key])
            if not math.isfinite(value) or value < 0 or value > 1e10:
                raise ValueError(f'{key} must be a finite nonnegative number')
            if key.endswith('_pct') and value > 100:
                raise ValueError(f'{key} must be between 0 and 100')
            if key in ('moq', 'order_qty') and (value < 1 or value != int(value)):
                raise ValueError(f'{key} must be a positive whole number')
            out[key] = value
    if out['order_qty'] < out['moq']:
        raise ValueError('Order quantity must meet supplier MOQ')
    for key in ('fee_date','quote_date'):
        if out[key]:
            parsed = date.fromisoformat(out[key])
            if parsed > date.today():
                raise ValueError(f'{key} cannot be in the future')
    if out['verified_fees'] and not (out['fee_source'] and out['fee_date']):
        raise ValueError('Confirmed fees require a source and effective date')
    if out['verified_quote'] and not (out['supplier_name'] and out['quote_date']):
        raise ValueError('Confirmed supplier quote requires a supplier and quote date')
    return out

def economics(raw):
    p = validate_inputs(raw)
    price = p['sale_price']
    landed = p['purchase_cost'] + p['inbound'] + p['duty']
    commission = price*p['commission_pct']/100
    payment = price*p['payment_pct']/100
    fee_tax = (commission+payment)*p['fee_tax_pct']/100
    withholding = price*p['withholding_pct']/100
    fees = commission+payment+fee_tax+withholding+p['other_fee']
    # monthly_units = placed orders. Refunded returns produce no revenue;
    # return_loss includes irrecoverable inventory, freight and unreversed fees.
    rate = p['return_pct']/100
    success_profit = price-landed-fees-p['packaging']-p['shipping']
    expected = (1-rate)*success_profit-rate*p['return_loss']-p['ad_cost']
    cost_base = landed+p['packaging']+p['shipping']+p['other_fee']
    variable_rate = (p['commission_pct']+p['payment_pct'])/100*(1+p['fee_tax_pct']/100)+p['withholding_pct']/100
    denom = (1-rate)*(1-variable_rate)
    break_even = ((1-rate)*cost_base+rate*p['return_loss']+p['ad_cost'])/denom if denom > 0 else None
    monthly = expected*p['monthly_units']-p['fixed_monthly']
    inventory = landed*p['order_qty']
    operating = (p['ad_cost']+p['packaging']+p['shipping']+rate*p['return_loss'])*p['order_qty']
    # Reserve a second stock tranche while cash is tied in supply + settlement.
    working = landed*p['monthly_units']/30*(p['payout_days']+p['supplier_lead_days'])
    base = inventory+operating+working+p['setup_cost']+p['fixed_monthly']
    buffer = base*p['buffer_pct']/100
    investment = base+buffer
    warnings = []
    if not p['verified_fees']: warnings.append('Fee assumptions are unconfirmed; verify in Daraz Seller Center.')
    if not p['verified_quote']: warnings.append('Purchase cost is an assumption until a supplier quote is confirmed.')
    if p['purchase_cost'] <= 0: warnings.append('Enter a product purchase cost before using these results.')
    if p['sale_price'] <= 0: warnings.append('Enter the expected selling price.')
    if p['monthly_units'] <= 0: warnings.append('Monthly orders are unspecified. Monthly profit is not a sales forecast.')
    if variable_rate >= 1: warnings.append('Fees consume 100% or more of sale price.')
    if p['verified_fees'] and (date.today()-date.fromisoformat(p['fee_date'])).days > 90:
        warnings.append('Fee verification is over 90 days old; recheck it.')
    if p['verified_quote'] and (date.today()-date.fromisoformat(p['quote_date'])).days > 30:
        warnings.append('Supplier quote is over 30 days old; recheck availability and price.')
    result = dict(landed_cost=landed, commission=commission, payment_fee=payment,
        fee_tax=fee_tax, withholding=withholding, platform_deductions=fees,
        successful_order_profit=success_profit, expected_order_profit=expected,
        margin_pct=expected/price*100 if price else 0, break_even_price=break_even,
        monthly_profit=monthly, inventory_cash=inventory, operating_cash=operating,
        working_capital=working, contingency=buffer, investment=investment,
        budget_remaining=p['budget']-investment,
        break_even_orders=math.ceil(p['fixed_monthly']/expected) if expected>0 else None,
        payback_months=investment/monthly if monthly>0 else None,
        ready=p['verified_fees'] and p['verified_quote'] and price>0 and landed>0,
        warnings=warnings)
    result['scenarios'] = []
    for name, price_factor, volume_factor, cost_factor in [('Downside',.9,.65,1.1),('Base',1,1,1),('Upside',1.05,1.25,1)]:
        sp = price*price_factor
        profit = (1-rate)*(sp*(1-variable_rate)-landed*cost_factor-p['packaging']-p['shipping']-p['other_fee'])-rate*p['return_loss']-p['ad_cost']
        result['scenarios'].append(dict(name=name,monthly_orders=p['monthly_units']*volume_factor,
            order_profit=round(profit,2),monthly_profit=round(profit*p['monthly_units']*volume_factor-p['fixed_monthly'],2)))
    complete=price>0 and (p['purchase_cost']>0 or rate==1)
    result['calculable']=complete
    if not complete:
        for key in ('successful_order_profit','expected_order_profit','margin_pct','break_even_price','monthly_profit','break_even_orders','payback_months'):
            result[key]=None
        result['scenarios']=[]
    if p['purchase_cost']<=0:
        for key in ('landed_cost','inventory_cash','working_capital','contingency','investment','budget_remaining'):
            result[key]=None
    if p['monthly_units']<=0:
        result['monthly_profit']=None
        result['payback_months']=None
        for scenario in result['scenarios']: scenario['monthly_profit']=None
    return {k:round(v,2) if isinstance(v,float) else v for k,v in result.items()}

def next_month(month, step=1):
    year, number = map(int,month.split('-'))
    count = year*12+number-1+step
    return f'{count//12:04d}-{count%12+1:02d}'

def predict(values, horizon, model):
    y = np.asarray(values, dtype=float)
    if model == 'seasonal-naive':
        return np.array([y[-12+(i%12)] for i in range(horizon)])
    if model == 'recent-mean':
        return np.repeat(np.mean(y[-6:]),horizon)
    # A damped recent trend, not an unbounded five-year extrapolation.
    recent=y[-24:]; x=np.arange(len(recent))
    slope, intercept=np.polyfit(x,recent,1)
    steps=np.arange(1,horizon+1)
    return np.maximum(0, intercept+slope*(len(recent)-1)+slope*np.cumsum(.85**steps))

def forecast(rows, horizon=12):
    if len(rows)<24:
        return dict(available=False,reason='At least 24 consecutive completed months are needed for a backtested forecast.')
    ordered=sorted(rows,key=lambda r:r['date'])
    for a,b in zip(ordered,ordered[1:]):
        if next_month(a['date']) != b['date']:
            return dict(available=False,reason='History has missing months. Fill gaps from the source; gaps are not zero sales.')
    metrics={r['metric'] for r in ordered}; geos={r['geo'] for r in ordered}
    if len(metrics)!=1 or geos!={'PK'}:
        raise ValueError('Forecast requires one metric and Pakistan (PK) history')
    y=np.array([r['value'] for r in ordered])
    if next(iter(metrics))=='interest' and np.count_nonzero(y)<12:
        return dict(available=False,reason='Pakistan search interest is too sparse for a useful forecast (fewer than 12 nonzero months). Try a broader keyword. Zero search interest does not establish zero sales.')
    holdout=min(12,len(y)-18)
    candidates=['recent-mean','damped-trend','seasonal-naive']
    scores=[]
    for model in candidates:
        residual=[]
        for i in range(len(y)-holdout,len(y)):
            residual.append(float(y[i]-predict(y[:i],1,model)[0]))
        scores.append(dict(model=model,mae=float(np.mean(np.abs(residual))),residual=residual))
    best=min(scores,key=lambda s:s['mae'])
    projected=predict(y,horizon,best['model'])
    spread=float(np.quantile(np.abs(best['residual']),.9))
    metric=next(iter(metrics))
    points=[]
    for i,value in enumerate(projected):
        width=spread*math.sqrt(i+1)
        cap=100 if metric=='interest' else float('inf')
        points.append(dict(date=next_month(ordered[-1]['date'],i+1),value=round(min(cap,max(0,float(value))),2),
            low=round(min(cap,max(0,float(value)-width)),2),high=round(min(cap,max(0,float(value)+width)),2)))
    previous=float(np.mean(y[-24:-12])); current=float(np.mean(y[-12:]))
    return dict(available=True,metric=metric,geo='PK',months=len(y),five_year_coverage=len(y)>=60,
        start=ordered[0]['date'],end=ordered[-1]['date'],model=best['model'],mae=round(best['mae'],2),
        backtest_months=holdout,models=[dict(model=s['model'],mae=round(s['mae'],2)) for s in scores],
        yoy_pct=round((current/previous-1)*100,2) if previous>0 else None,points=points,
        note='Bands are residual-based scenario ranges, not calibrated confidence intervals. Search interest is not sales. Independent keyword scales cannot compare absolute demand.')
