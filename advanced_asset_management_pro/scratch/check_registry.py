import os
import sys

# Add odoo path to sys.path
odoo_path = '/home/digitz/odoo16'
sys.path.append(odoo_path)

import odoo
from odoo.modules.registry import Registry

def check_model():
    # Load configuration
    config_file = '/home/digitz/odoo16/odoo.conf'
    odoo.tools.config.parse_config(['-c', config_file])
    
    db_name = odoo.tools.config['db_name']
    if not db_name:
        print("No database specified in config")
        return

    registry = Registry(db_name)
    with registry.cursor() as cr:
        env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
        print(f"Checking for 'account.asset' in registry of database: {db_name}")
        if 'account.asset' in env.registry:
            print("Model 'account.asset' is present in registry.")
        else:
            print("Model 'account.asset' is NOT present in registry.")
            
        # Check installed modules
        modules = env['ir.module.module'].search([('name', '=', 'advanced_asset_management_pro')])
        if modules:
            print(f"Module 'advanced_asset_management_pro' state: {modules.state}")
        else:
            print("Module 'advanced_asset_management_pro' not found in ir.module.module")

if __name__ == '__main__':
    check_model()
