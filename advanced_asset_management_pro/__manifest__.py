{
    'name': 'Enterprise Asset Management Pro (Advanced Depreciation)',
    'version': '16.0.1.0.0',
    'category': 'Accounting/Accounting',
    'sequence': 1,
    'summary': 'Professional Enterprise-grade asset lifecycle & depreciation suite. Requires Odoo Enterprise Accounting.',
    'description': """
# Enterprise Asset Management Pro

Elevate your Odoo 16 Enterprise Accounting with a professional-grade asset management engine. This module seamlessly integrates with the Odoo Enterprise Reporting framework to provide a state-of-the-art financial experience.

### ⚠️ Prerequisite:
This module is an **Enterprise Enhancement**. It requires the **Odoo Enterprise Accounting** (account_reports) module to be installed to provide its advanced interactive reporting features.

### Key Professional Features:
*   **Advanced Depreciation Engine**: Support for complex linear and declining balance methods with high financial precision.
*   **Native Enterprise Reporting**: Leverages the account_reports engine for a premium, interactive Depreciation Schedule.
*   **Full Asset Governance**: Manage the entire lifecycle from acquisition and modifications to final disposal.
*   **Accounting Integrity**: Deeply linked with Journal Entries and Vendor Bills for automated ledger impact.
*   **Audit-Ready**: Transparent tracking of every book value change and depreciation move.

Designed for organizations that demand Enterprise-level financial accuracy and modern reporting interfaces.
    """,
    'author': 'Custom',
    'website': 'https://yourwebsite.com',
    'license': 'LGPL-3',
    'depends': [
        'account',
        'mail',
        'account_reports',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/asset_security.xml',
        'data/account_asset_report_data.xml',
        'wizard/asset_modify_views.xml',
        'wizard/asset_depreciation_schedule_views.xml',
        'views/account_asset_views.xml',
        'views/account_asset_group_views.xml',
        'views/account_move_views.xml',
        'report/depreciation_schedule_report.xml',
    ],
    'images': [
        'static/description/banner.png',
        'static/description/reporting.png',
        'static/description/lifecycle.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'price': 69.00,
    'currency': 'EUR',
}
