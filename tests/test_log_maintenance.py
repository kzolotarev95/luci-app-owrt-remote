"""Retention policy, safe archive selection, disk levels and HTTP diagnostics."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("log_maintenance", ROOT / "vps/log-maintenance.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MaintenanceTests(unittest.TestCase):
    def test_managed_journald_policy_is_loaded_after_common_size_dropin(self):
        self.assertGreater(
            Path('/etc/systemd/journald.conf.d/zz-owrt-remote-limits.conf').name,
            Path('/etc/systemd/journald.conf.d/size.conf').name)

    def test_custom_hub_rule_is_preserved_during_install_and_rollback(self):
        rule = '/var/log/owrt-remote*.log {\n daily\n rotate 7\n size 50M\n missingok\n notifempty\n compress\n delaycompress\n copytruncate\n}\n'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy, installed, runtime = root / 'owrt-remote', root / 'maintenance.py', root / 'run'
            legacy.write_text(rule)
            def atomic(path, data, mode=0o600):
                path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data if isinstance(data, bytes) else data.encode())
            effective = []
            def command(args, **kwargs):
                if args[0] == 'logrotate':
                    effective.append(Path(args[-1]).read_text())
                return subprocess.CompletedProcess(args, 0, stdout='inactive\n')
            with mock.patch.object(m, 'LEGACY', legacy), mock.patch.object(m, 'INSTALL_PATH', installed), \
                    mock.patch.object(m, 'RUN', runtime), mock.patch.object(m, 'CONFIGS', {'/etc/nginx/conf.d/owrt-remote-log-policy.conf': ''}), \
                    mock.patch.object(m, 'atomic', side_effect=atomic), mock.patch.object(m, 'command', side_effect=command), \
                    mock.patch.object(m.shutil, 'which', side_effect=lambda name: None if name == 'nginx' else '/bin/'+name), \
                    mock.patch.object(m, 'flatten_config', side_effect=lambda _: legacy.read_text()), \
                    mock.patch.object(m, 'validate_journal_config'), mock.patch.object(m.os, 'chown', create=True), \
                    mock.patch('sys.stdout', new=io.StringIO()):
                self.assertFalse(m.legacy_policy())
                m.install(backup=root / 'backup')
                self.assertEqual(legacy.read_text(), rule)
                self.assertIn('/var/log/owrt-remote*.log', effective[0])
                self.assertIn('maxsize 20M', effective[0])
                self.assertIn('copytruncate', effective[0])
                m.restore(root / 'backup')
                self.assertEqual(legacy.read_text(), rule)
                self.assertFalse(installed.exists())

    def nginx_paths(self, filenames, missing=(), resolved=None, module_text=None, uid=0, mode=0o100644):
        class ConfigPath:
            def __init__(self, filename):
                self.filename = filename
            def resolve(self):
                return ConfigPath((resolved or {}).get(self.filename, self.filename))
            def __str__(self):
                return self.filename
            def is_file(self):
                return self.filename not in missing
            def stat(self):
                return type('Info', (), {'st_mode': mode, 'st_uid': uid, 'st_size': 80})()
            def read_text(self):
                if self.filename.startswith('/etc/letsencrypt/'):
                    raise AssertionError('Certbot TLS policy must not be edited')
                if self.filename.startswith('/usr/share/nginx/'):
                    return module_text or 'load_module modules/ngx_http_geoip2_module.so;\n'
                return 'http { access_log /var/log/nginx/custom.log; }\n'
        rendered = ''.join('# configuration file ' + filename + ':\n' for filename in filenames)
        return (mock.patch.object(m, 'Path', ConfigPath), mock.patch.object(m, 'command',
                return_value=subprocess.CompletedProcess([], 0, rendered)))

    def test_certbot_exact_ssl_policy_is_allowed_read_only(self):
        paths, command = self.nginx_paths(['/etc/nginx/nginx.conf', '/etc/letsencrypt/options-ssl-nginx.conf'])
        with paths, command:
            self.assertEqual(m.nginx_policies(), {})

    def test_certbot_exception_does_not_allow_other_external_configs(self):
        for filename in ['/etc/letsencrypt/other.conf', '/etc/letsencrypt/options-ssl-nginx.conf.bak',
                         '/tmp/options-ssl-nginx.conf', '/etc/nginx-evil/options.conf']:
            with self.subTest(filename=filename):
                paths, command = self.nginx_paths([filename])
                with paths, command, self.assertRaisesRegex(RuntimeError, 'Unexpected nginx configuration'):
                    m.nginx_policies()

    def test_missing_or_escaped_certbot_policy_is_rejected(self):
        filename = '/etc/letsencrypt/options-ssl-nginx.conf'
        for missing, resolved in [([filename], {}), ([], {filename:'/tmp/tls.conf'})]:
            paths, command = self.nginx_paths([filename], missing, resolved)
            with paths, command, self.assertRaisesRegex(RuntimeError, 'Unexpected nginx configuration'):
                m.nginx_policies()

    def test_distro_module_includes_are_allowed_without_modifying_them(self):
        target = '/usr/share/nginx/modules-available/mod-http-geoip2.conf'
        link = '/etc/nginx/modules-enabled/50-mod-http-geoip2.conf'
        for filename, resolved in [(target, {}), (link, {link: target})]:
            with self.subTest(filename=filename):
                paths, command = self.nginx_paths(['/etc/nginx/nginx.conf', filename], resolved=resolved)
                with paths, command:
                    self.assertEqual(m.nginx_policies(), {})
        paths, command = self.nginx_paths([target], module_text='# packaged module\nload_module /usr/lib/nginx/modules/ngx_http_geoip2_module.so;\n')
        with paths, command:
            self.assertEqual(m.nginx_policies(), {})

    def test_module_exception_rejects_unsafe_permissions_and_non_module_directives(self):
        target = '/usr/share/nginx/modules-available/mod-http-geoip2.conf'
        for options in ({'uid': 1000}, {'mode': 0o100666}, {'mode': 0o040755},
                        {'module_text': 'access_log /var/log/nginx/access.log;'},
                        {'module_text': 'load_module /tmp/evil.so;'},
                        {'module_text': 'load_module modules/../evil.so;'},
                        {'module_text': 'load_module modules/ngx_http_geoip2_module.so;\ninclude /tmp/site.conf;'}):
            with self.subTest(options=options):
                paths, command = self.nginx_paths([target], **options)
                with paths, command, self.assertRaisesRegex(RuntimeError, 'nginx module'):
                    m.nginx_policies()

    def test_module_exception_cannot_allow_other_external_or_missing_files(self):
        target = '/usr/share/nginx/modules-available/mod-http-geoip2.conf'
        for filename, missing, resolved in [
                ('/usr/share/nginx/modules-available/site.conf', [], {}),
                ('/usr/share/nginx/modules-available/mod-http-geoip2.conf.bak', [], {}),
                ('/usr/share/nginx/modules-available-evil/mod-http-geoip2.conf', [], {}),
                (target, [target], {}), (target, [], {target: '/tmp/module.conf'})]:
            paths, command = self.nginx_paths([filename], missing=missing, resolved=resolved)
            with paths, command, self.assertRaisesRegex(RuntimeError, 'Unexpected nginx configuration'):
                m.nginx_policies()

    def test_only_exact_old_project_rule_can_be_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'owrt-remote'
            rule = '/var/log/nginx/*.log {\n daily\n rotate 7\n size 50M\n missingok\n notifempty\n compress\n delaycompress\n copytruncate\n}\n'
            path.write_text(rule)
            self.assertTrue(m.legacy_policy(path))
            path.write_text(rule.replace('rotate 7', 'rotate 30'))
            self.assertFalse(m.legacy_policy(path))
            path.write_text(rule.replace('copytruncate', 'postrotate\n echo custom hook\n endscript'))
            self.assertFalse(m.legacy_policy(path))

    def test_retention_preserves_native_reopen_hooks(self):
        text = '''weekly
/var/log/syslog
/var/log/auth.log
{
    rotate 7
    weekly
    delaycompress
    postrotate
        if true; then
            echo "{ brace inside shell }"
        fi
    endscript
}
'''
        result, patterns = m.retention_config(text)
        self.assertEqual(patterns, ["/var/log/syslog", "/var/log/auth.log"])
        self.assertIn('echo "{ brace inside shell }"', result)
        self.assertGreater(result.index("daily"), result.index("endscript"))
        self.assertIn("maxsize 20M", result)
        self.assertIn("nodelaycompress", result)

    def test_non_log_paths_and_ambiguous_rules_fail_before_rotation(self):
        for source in ("/var/lib/owrt-remote/hub.db {\n}\n", "/var/log/../lib/data {\n}\n",
                       "/var/log/a { rotate 1 }\n", "/var/log/a {\nolddir /home/user\n}\n"):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                m.retention_config(source)

    def test_approved_service_logs_are_allowed_but_other_external_targets_are_rejected(self):
        source = ("/etc/csqtt/csqtt.log {\n    daily\n}\n"
                  "/root/freeze-watch.log {\n    daily\n}\n"
                  "/root/scripts/*.log {\n    weekly\n}\n")
        result, patterns = m.retention_config(source)
        self.assertEqual(patterns, ["/etc/csqtt/csqtt.log", "/root/freeze-watch.log", "/root/scripts/*.log"])
        self.assertIn("maxsize 20M", result)
        for target in ("/etc/csqtt/other.log", "/etc/csqtt/csqtt.log.*", "/opt/csqtt/csqtt.log",
                       "/root/other.log", "/root/scripts/private.txt"):
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, "outside /var/log"):
                m.retention_config(target + " {\n}\n")

    def test_archive_names_do_not_match_active_or_user_files(self):
        for suffix in (".1", ".20.gz", ".old", "-20261119.gz", ".20261119-032000.xz"):
            self.assertTrue(m.archive_name("syslog", "syslog" + suffix), suffix)
        for name in ("syslog", "syslog.db", "syslog.backup.tar.gz", "user.gz", "syslog.log", "syslog.old.key"):
            self.assertFalse(m.archive_name("syslog", name), name)

    def test_prune_only_old_closed_archives_and_keep_active_inode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            active = root / "access.log"
            active.write_bytes(b"active")
            old, fresh, opened, unrelated = [root / x for x in ("access.log.2.gz", "access.log.1", "access.log.old", "notes.gz")]
            for p in (old, fresh, opened, unrelated):
                p.write_bytes(b"archive")
            for p in (old, opened, unrelated):
                os.utime(p, (100, 100))
            st = opened.stat()
            inode = active.stat().st_ino
            removed, size = m.prune_archives([active], now=100000, opened={(st.st_dev, st.st_ino)})
            self.assertEqual((removed, size), (1, 7))
            self.assertFalse(old.exists())
            self.assertTrue(all(p.exists() for p in (active, fresh, opened, unrelated)))
            self.assertEqual(active.stat().st_ino, inode)
            self.assertEqual(m.prune_archives([active], emergency=True, opened={(st.st_dev, st.st_ino)})[0], 1)
            self.assertTrue(opened.exists())

    @unittest.skipIf(os.name == "nt", "Requires Unix link semantics")
    def test_symlinks_and_hardlinks_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); active = root / "a.log"; active.write_text("active")
            user = root / "user"; user.write_text("private")
            (root / "a.log.1").symlink_to(user)
            os.link(user, root / "a.log.2")
            self.assertEqual(m.prune_archives([active], emergency=True, opened=set()), (0, 0))
            self.assertEqual(user.read_text(), "private")

    def test_disk_checks_root_and_separate_log_mount(self):
        usage = type("Usage", (), {"total": 110, "used": 91, "free": 9})()
        with mock.patch.object(m.shutil, "disk_usage", return_value=usage) as get:
            levels = m.disk_levels()
            self.assertEqual([x[0] for x in levels], ["/", "/var/log"])
            self.assertTrue(all(x[2] == "CRITICAL" for x in levels))
            self.assertTrue(all(x[1] == 91 for x in levels))
            self.assertEqual(get.call_count, 2)
        for used, expected in ((75, 'OK'), (75.1, 'WARN'), (85, 'WARN'), (90, 'WARN'), (90.1, 'CRITICAL')):
            usage = type('Usage', (), {'used': used, 'free': 100 - used})()
            with self.subTest(used=used), mock.patch.object(m.shutil, 'disk_usage', return_value=usage):
                self.assertEqual(m.disk_levels()[0][2], expected)

    @unittest.skipIf(os.name == 'nt', 'Requires fcntl lock')
    def test_disk_pressure_selects_aggressive_archives_and_smaller_journal_budget(self):
        for percent, emergency in ((84.9, False), (85, False), (85.1, True)):
            with self.subTest(percent=percent), tempfile.TemporaryDirectory() as temp:
                with mock.patch.object(m, 'STATE', Path(temp) / 'state'), \
                     mock.patch.object(m, 'RUN', Path(temp) / 'run'), \
                     mock.patch.object(m, 'flatten_config', return_value=''), \
                     mock.patch.object(m, 'disk_levels', return_value=[('/', percent, 'WARN'), ('/var/log', 20, 'OK')]), \
                     mock.patch.object(m, 'prune_archives', return_value=(0, 0)) as prune, \
                     mock.patch.object(m, 'record'), mock.patch('sys.stdout', new=io.StringIO()), \
                     mock.patch.object(m, 'command', return_value=subprocess.CompletedProcess([], 0, '')) as command:
                    m.run()
                    self.assertTrue(all(call.kwargs['emergency'] == emergency for call in prune.call_args_list))
                    self.assertTrue(any('--vacuum-size=' + ('10M' if emergency else '50M') in call.args[0] for call in command.call_args_list))

    def test_later_journald_override_cannot_silently_disable_limit(self):
        text = '[Journal]\nSystemMaxUse=50M\nSystemKeepFree=500M\nMaxRetentionSec=7day\nCompress=yes\n'
        with mock.patch.object(m, 'command', return_value=subprocess.CompletedProcess([], 0, text)):
            m.validate_journal_config()
        with mock.patch.object(m, 'command', return_value=subprocess.CompletedProcess([], 0, text + '\n[Journal]\nSystemMaxUse=1G\n')):
            with self.assertRaisesRegex(RuntimeError, 'overrides SystemMaxUse'):
                m.validate_journal_config()

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX nginx paths')
    def test_nginx_modifies_existing_logger_without_adding_duplicate(self):
        text = 'http {\n    access_log /var/log/nginx/access.log;\n    access_log /var/log/nginx/custom.log;\n}\n'
        path = Path('/etc/nginx/nginx.conf')
        with mock.patch.object(m, 'command', return_value=subprocess.CompletedProcess([], 0, '# configuration file /etc/nginx/nginx.conf:\n')), \
             mock.patch.object(Path, 'resolve', return_value=path), \
             mock.patch.object(Path, 'is_file', return_value=True), \
             mock.patch.object(Path, 'read_text', return_value=text):
            configs = m.nginx_policies()
        changed = configs[str(path)]
        self.assertEqual(changed.count('access_log '), 2)
        self.assertIn('access.log combined if=$owrt_remote_access_log;', changed)
        self.assertIn('access_log /var/log/nginx/custom.log;', changed)

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX nginx paths')
    def test_existing_custom_nginx_condition_is_not_silently_overwritten(self):
        with mock.patch.object(m, 'command', return_value=subprocess.CompletedProcess([], 0, '# configuration file /etc/nginx/nginx.conf:\n')), \
             mock.patch.object(Path, 'resolve', return_value=Path('/etc/nginx/nginx.conf')), \
             mock.patch.object(Path, 'is_file', return_value=True), \
             mock.patch.object(Path, 'read_text', return_value='access_log /var/log/nginx/access.log combined if=$custom;'):
            with self.assertRaisesRegex(RuntimeError, 'manual review'):
                m.nginx_policies()

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX file permissions')
    def test_result_log_rollover_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'maintenance.log'
            path.write_bytes(b'x' * 100000)
            with mock.patch.object(m, 'RESULT', path), mock.patch('sys.stdout', new=io.StringIO()):
                m.record('OK', 'done')
            self.assertLess(path.stat().st_size, 200)
            self.assertEqual(path.with_suffix('.log.1').stat().st_size, 65536)

    @unittest.skipIf(os.name == 'nt', 'Requires fcntl lock')
    def test_daily_guard_parallel_lock_and_failed_run_are_explicit(self):
        import datetime
        import fcntl
        with tempfile.TemporaryDirectory() as temp:
            state, runtime = Path(temp) / 'state', Path(temp) / 'run'
            state.mkdir(); runtime.mkdir()
            with mock.patch.object(m, 'STATE', state), mock.patch.object(m, 'RUN', runtime), \
                 mock.patch.object(m, 'record'), mock.patch('sys.stdout', new=io.StringIO()) as output, \
                 mock.patch.object(m, 'flatten_config', side_effect=RuntimeError('invalid policy')) as parse:
                with (runtime / 'lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    m.run()
                    self.assertFalse(parse.called)
                with self.assertRaisesRegex(RuntimeError, 'invalid policy'):
                    m.run()
                self.assertIn('"ok": false', (state / 'result.json').read_text())
                self.assertEqual((state / 'last-attempt').read_text().strip(), datetime.date.today().isoformat())
                parse.reset_mock()
                m.run()
                self.assertFalse(parse.called)
                self.assertIn('already', output.getvalue())
                with self.assertRaisesRegex(RuntimeError, 'invalid policy'):
                    m.run(force=True)


if __name__ == "__main__":
    unittest.main()
