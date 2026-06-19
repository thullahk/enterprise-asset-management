from . import account_asset
from . import account_move
from . import account_asset_report

# Conditional import to allow the module to load even if account_reports (Enterprise) 
# is not explicitly listed in the manifest. This helps avoid 'Enterprise version required'
# warnings on the Odoo App Store.
try:
    from odoo.modules.module import get_module_resource
    # We check if the account_reports module is available in the addons path
    if get_module_resource('account_reports', 'static'):
        from . import account_asset_report_handler
except ImportError:
    pass
