import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from analytics import economics,forecast,next_month
from app import create_app
from research import extract,public_url,normalize_keyword
import research

class AnalyticsTests(unittest.TestCase):
    def test_common_jewellery_spelling(self):
        self.assertEqual(normalize_keyword('jewelary set'),'jewellery set')
        self.assertEqual(normalize_keyword('jewelry set'),'jewellery set')
        self.assertEqual(normalize_keyword('wireless earbuds'),'wireless earbuds')
    @patch('research.public_url',side_effect=lambda url:url)
    @patch('research.requests.get')
    def test_google_wrapped_result_is_resolved_before_site_filter(self,get,_public):
        response=MagicMock();response.json.return_value={'results':[dict(title='Jewelry set',url='https://www.google.com.pk/goto?url=opaque',snippet='Jewellery Pakistan',type='organic')]}
        redirect=MagicMock();redirect.__enter__.return_value=redirect;redirect.is_redirect=True;redirect.headers={'Location':'https://www.daraz.pk/products/jewellery-set.html'}
        get.side_effect=[response,redirect]
        with patch.dict(research._cooldown,{},clear=True): rows,errors=research.search('site:daraz.pk jewelary price Pakistan')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['url'],'https://www.daraz.pk/products/jewellery-set.html')
        self.assertEqual(rows[0]['engine'],'openserp:google')
        self.assertIn('/google/search',get.call_args_list[0].args[0])
        self.assertEqual(errors,[])
    @patch('research.allowed',return_value=True)
    @patch('research.fetch',return_value=('<p>The Pakistani Bags & Accessories E-Commerce Market generated an e-commerce revenue of US$189m in 2025. Jewelry & Watches is the largest category, contributing 40%.</p>','https://ecdb.com/'))
    def test_provider_context_keeps_category_scope(self,_fetch,_allowed):
        self.assertEqual(research.analytics_context('mobile accessories'),[])
        item=research.analytics_context('jewelary')[0]
        self.assertEqual(item['kind'],'category estimate')
        self.assertEqual(item['metrics'][0]['currency'],'USD')
        self.assertEqual(item['metrics'][0]['value'],189000000)
        self.assertIn('not product sales',item['metrics'][0]['basis'])
    @patch('research.time.sleep')
    @patch('research.analytics_context',return_value=[])
    @patch('research.inspect',side_effect=lambda result,kind:[dict(result,kind=kind,status='observed metadata')])
    @patch('research.search')
    def test_broad_shop_search_when_fixed_sites_have_no_results(self,search,_inspect,_context,_sleep):
        search.side_effect=lambda query,limit:(
            ([dict(title='Jewellery shop',url='https://example.com/jewellery',snippet='',engine='openserp:google')] if 'online shopping' in query else []),[])
        result=research.collect('jewelary',lambda message:None,['daraz.pk'])
        self.assertEqual(result['searched_keyword'],'jewellery')
        self.assertEqual(result['keyword'],'jewelary')
        self.assertGreaterEqual(len(result['evidence']),1)
    def test_unknown_economics_are_not_zero(self):
        e=economics({})
        for key in ('expected_order_profit','investment','margin_pct','budget_remaining','monthly_profit'):
            self.assertIsNone(e[key])
        self.assertFalse(e['calculable'])
    def test_storefront_script_is_not_a_challenge(self):
        html='<script src="recaptcha.js"></script><div class="product-card-wrapper"><h3 class="card__heading"><a href="/products/ring">Zircon ring</a></h3><div class="price price--on-sale"><span class="price-item--regular">Rs.999</span><span class="price-item--sale">Rs.525</span></div></div>'
        self.assertFalse(research.is_challenge(html))
        rows=research.extract(html,'https://example.com/')
        self.assertEqual(rows[0]['price'],525)
        self.assertEqual(rows[0]['url'],'https://example.com/products/ring')
        self.assertTrue(research.is_challenge('<title>Just a moment</title>Verify you are human'))
    def test_failed_refresh_preserves_observation(self):
        url='https://example.com/products/ring'
        merged=research.merge_evidence([dict(evidence=[dict(url=url,kind='marketplace',status='blocked')]),dict(evidence=[dict(url=url,kind='marketplace',status='observed metadata',price=525,title='Zircon ring')])],'jewelary')
        self.assertEqual(merged['evidence'][0]['price'],525)
        self.assertFalse(research.relevant_offer(dict(title='Smart Watch with Jewellery Set'),'jewelary'))
    def test_return_economics_and_cash(self):
        e=economics(dict(sale_price=1000,purchase_cost=400,commission_pct=10,payment_pct=2,
            fee_tax_pct=10,withholding_pct=1,packaging=20,shipping=30,ad_cost=40,
            return_pct=10,return_loss=100,monthly_units=100,fixed_monthly=1000,
            order_qty=50,budget=100000))
        self.assertAlmostEqual(e['platform_deductions'],142)
        self.assertAlmostEqual(e['expected_order_profit'],317.2)
        self.assertAlmostEqual(e['monthly_profit'],30720)
        self.assertGreater(e['investment'],e['inventory_cash'])
        self.assertFalse(e['ready'])
    def test_invalid_costs(self):
        for p in [dict(sale_price=float('nan')),dict(return_pct=101),dict(moq=10,order_qty=5),dict(verified_fees=True),dict(verified_quote='false')]:
            with self.assertRaises(ValueError): economics(p)
    def test_full_refunds(self):
        e=economics(dict(return_pct=100,return_loss=50,ad_cost=20,sale_price=1000,monthly_units=10))
        self.assertEqual(e['expected_order_profit'],-70)
        self.assertIsNone(e['break_even_price'])
    def test_seasonal_forecast_and_gap(self):
        rows=[dict(date=next_month('2020-09',i),metric='units',geo='PK',value=[10,20,50,25,30,80,120,40,50,60,90,70][i%12]) for i in range(60)]
        f=forecast(rows)
        self.assertTrue(f['five_year_coverage'])
        self.assertEqual(f['model'],'seasonal-naive')
        self.assertEqual(f['mae'],0)
        self.assertEqual(len(f['points']),12)
        self.assertFalse(forecast(rows[:5]+rows[6:])['available'])
    def test_extract_does_not_convert_reviews_to_sales(self):
        html='<script type="application/ld+json">'+json.dumps({'@type':'Product','name':'Fan','offers':{'price':'1,999','priceCurrency':'PKR'},'aggregateRating':{'ratingValue':4.8,'reviewCount':120}})+'</script>'
        p=extract(html,'https://example.com/fan')[0]
        self.assertEqual(p['price'],1999)
        self.assertEqual(p['reviews'],120)
        self.assertIsNone(p['sold_count'])
    def test_private_network_blocked(self):
        with self.assertRaises(ValueError): public_url('http://127.0.0.1/private')
    def test_priceoye_uses_offer_not_struck_retail_price(self):
        html='<div class="productBox"><a href="/wireless-earbuds/acme/model"><h4 class="p-title">Acme earbuds</h4><div class="price-box">Rs 3,599</div><div class="price-diff-retail">Rs 6,400</div></a></div>'
        p=extract(html,'https://priceoye.pk/wireless-earbuds')[0]
        self.assertEqual(p['price'],3599)
        self.assertEqual(p['source_page'],'https://priceoye.pk/wireless-earbuds')
    def test_sparse_interest_is_not_a_zero_sales_prediction(self):
        rows=[dict(date=next_month('2020-09',i),metric='interest',geo='PK',value=0) for i in range(60)]
        self.assertFalse(forecast(rows)['available'])

class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.app=create_app(Path(self.temp.name)); self.client=self.app.test_client()
        self.token=self.client.get('/api/bootstrap').json['token']; self.headers={'X-Research-Token':self.token}
        self.pid=self.client.post('/api/products',json={'name':'test product'},headers=self.headers).json['id']
    def tearDown(self): self.temp.cleanup()
    def test_csrf_and_host(self):
        self.assertEqual(self.client.post('/api/products',json={'name':'another'}).status_code,403)
        self.assertEqual(self.client.get('/api/products',headers={'Host':'attacker.example'}).status_code,403)
    def test_source_directory_does_not_claim_provider_connected(self):
        sources=self.client.get('/api/sources').json
        sellerdash=next(s for s in sources if s['name']=='SellerDash')
        self.assertIn('not connected',sellerdash['state'])
    def test_direct_collection_rejects_private_address(self):
        r=self.client.post(f'/api/products/{self.pid}/jobs',headers=self.headers,json=dict(kind='page',url='http://127.0.0.1/private'))
        self.assertEqual(r.status_code,400,r.json)
    def test_import_and_forecast(self):
        raw='date,metric,value,geo,source\n'+'\n'.join(f'{next_month("2020-09",i)},units,{20+i},PK,own seller report' for i in range(60))
        r=self.client.post(f'/api/products/{self.pid}/history',data={'file':(io.BytesIO(raw.encode()),'history.csv')},headers=self.headers)
        self.assertEqual(r.status_code,200,r.json)
        self.assertTrue(r.json['forecasts']['units']['available'])
        self.assertEqual(len(r.json['history']),60)
    def test_import_duplicate_rejected_atomically(self):
        raw='date,metric,value,geo,source\n2024-01,units,1,PK,test\n2024-01,units,2,PK,test'
        r=self.client.post(f'/api/products/{self.pid}/history',data={'file':(io.BytesIO(raw.encode()),'history.csv')},headers=self.headers)
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.client.get(f'/api/products/{self.pid}').json['history'],[])
    def test_native_trends_csv(self):
        raw='Category: All categories\n\nMonth,test product: (Pakistan)\n2024-01,40\n2024-02,55'
        r=self.client.post(f'/api/products/{self.pid}/history',data={'file':(io.BytesIO(raw.encode()),'trends.csv')},headers=self.headers)
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['history'][0]['metric'],'interest')
    def test_save_and_export(self):
        r=self.client.put(f'/api/products/{self.pid}/inputs',headers=self.headers,json=dict(sale_price=2000,purchase_cost=800))
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json['economics']['expected_order_profit'],1200)
        self.assertIn('test product',self.client.get('/api/export').text)
    def test_compare_and_select_supplier(self):
        for supplier,cost in [('Supplier A',500),('Supplier B',400)]:
            r=self.client.post(f'/api/products/{self.pid}/quotes',headers=self.headers,json=dict(
                supplier=supplier,unit_cost=cost,inbound=20,duty=10,moq=100,lead_days=7,quote_date='2026-01-01',confirmed=True))
            self.assertEqual(r.status_code,201,r.json)
        p=r.json; self.assertEqual(p['quotes'][0]['supplier'],'Supplier B')
        qid=p['quotes'][0]['id']
        r=self.client.post(f'/api/products/{self.pid}/quotes/{qid}/select',headers=self.headers,json={})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['inputs']['purchase_cost'],400)
        self.assertEqual(r.json['inputs']['order_qty'],100)
        self.assertEqual(r.json['economics']['landed_cost'],430)
    @patch('app.POOL.submit')
    def test_job_queue(self,submit):
        r=self.client.post(f'/api/products/{self.pid}/jobs',json=dict(kind='research'),headers=self.headers)
        self.assertEqual(r.status_code,202,r.json)
        submit.assert_called_once()
        r=self.client.post(f'/api/products/{self.pid}/jobs',json=dict(kind='trends'),headers=self.headers)
        self.assertEqual(r.status_code,409)

if __name__=='__main__': unittest.main()
