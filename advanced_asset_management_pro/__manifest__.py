{
    'name': 'Advanced Asset Management & Depreciation (Accounting Kit Compatible)',
    'version': '16.0.1.0.0',
    'category': 'Accounting/Accounting',
    'sequence': 1,
    'summary': 'Professional asset lifecycle & depreciation suite. Compatible with Odoo 16 Full Accounting Kit.',
    'description': """
# Advanced Asset Management & Lifecycle Suite

Professional-grade asset management engine for Odoo 16. This module is specifically designed to work seamlessly with the **Odoo 16 Full Accounting Kit**, bringing Enterprise-level asset tracking to your Community environment.

### ⚠️ Prerequisite:
This module requires the **Odoo 16 Full Accounting Kit** (or any module providing the account_reports framework) to be installed to enable its advanced interactive reporting features.

### Key Professional Features:
*   **Advanced Depreciation Engine**: Support for complex linear and declining balance methods with high financial precision.
*   **Interactive Reporting**: Uses the reporting framework for a premium, drill-down Depreciation Schedule.
*   **Full Asset Governance**: Manage the entire lifecycle from acquisition and modifications to final disposal.
*   **Accounting Integrity**: Deeply linked with Journal Entries and Vendor Bills for automated ledger impact.
*   **Audit-Ready**: Transparent tracking of every book value change and depreciation move.

The perfect companion for Odoo 16 users looking for a robust, high-performance asset management solution.
    """,
    'author': 'Rahmathullah/Digitz Technologies',
    'website': 'https://digitztech.com/',
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
    'price': 9.00,
    'currency': 'EUR',
}
