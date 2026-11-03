"""Verify local name-fix installation, exact revision checks and rollback."""
from tests import test_update_status_ui_deploy as deploy_tests


class RouterNameDeployTests(deploy_tests.UpdateStatusDeployTests):
    deploy_file = 'deploy-router-name-fix.ps1'
    backup_name = 'hub.py.bak-before-router-name-fix-v120'
    success_marker = 'ROUTER_NAME_FIX_V120_Install_OK'
