"""Provider coverage reviewed 2026-10-09. Discovery is separate from connection."""
SOURCES=[
    dict(name='Google Search',url='https://www.google.com/',state='Integrated through local OpenSERP; attempted first',
         provides='Public marketplace, supplier and research-source discovery',limits='May rate-limit requests. Search results are not a sales ledger.'),
    dict(name='Google Trends',url='https://trends.google.com/trends/',state='Integrated',
         provides='Pakistan keyword interest for the last 60 completed months',limits='Relative search interest, not product units or revenue.'),
    dict(name='Marketplace pages',url='https://www.daraz.pk/',state='Integrated; accessibility varies',
         provides='Prices, listing details, ratings and supplier discovery',limits='Visible counts do not establish monthly historical sales.'),
    dict(name='ECDB',url='https://ecdb.com/features/api',state='Public jewellery-related category summary integrated; paid API not connected',
         provides='Country/category/store market intelligence; public Pakistan Bags & Accessories summary',
         limits='Public summary is broader category data. API access requires a qualifying account; exact Pakistan/SKU history must be verified.'),
    dict(name='SellerDash',url='https://sellerdash.pk/',state='Potential source; account/export not connected',
         provides='Advertises Daraz 7-day/30-day/all-time sales views and CSV exports',
         limits='Provider claims and estimation methodology need checking. A 30-day snapshot is not a five-year monthly series.'),
    dict(name='XenonHunt',url='https://xenonhunt.com/pricing',state='Potential source; account/export not connected',
         provides='Advertises Daraz product research, keywords, sales data and export options',
         limits='Export availability depends on plan; Pakistan coverage, methodology and history must be checked with an actual export.'),
    dict(name='Similarweb',url='https://developers.similarweb.com/docs/ecommerce-dataset',state='Not connected; Daraz SKU sales coverage unverified',
         provides='Website traffic intelligence; documented ecommerce product dataset focuses on Amazon',
         limits='Traffic is not units sold. Amazon Shopper Intelligence is not evidence of Daraz product sales.'),
    dict(name='Daraz seller reports',url='https://sellercenter.daraz.pk/',state='Standard monthly CSV import supported',
         provides='Your own seller sales, returns and settlement evidence',limits='Requires seller access; covers the authorized store and available reporting dates.')
]
