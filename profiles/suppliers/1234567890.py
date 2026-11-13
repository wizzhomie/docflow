"""Example supplier profile for ФОП Коваленко Олена Іванівна (tax_id: 1234567890).

This shows how supplier-specific anchors can override or extend base invoice_ua profile.
"""

PROFILE = {
    'labels': {
        'supplier': [
            r'фоп\s+коваленко',
            r'коваленко\s+олена',
        ],
        'buyer': [
            r'північний\s+шлях',
        ],
    },
    'tax_prefixes': [
        r'фоп\s+іпн',
    ],
    'overrides': {
        'currency': 'UAH',
        'vat_rate': 20,
    },
}
