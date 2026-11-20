"""Exercise GUI choices and batch behavior without opening windows or sending input."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import queue
import smart_bot_gui as gui


def variable(value):
    v=Mock();v.get.return_value=value;return v


class InterfaceTests(unittest.TestCase):
    def app(self):
        app=object.__new__(gui.App)
        app.worker=None;app.events=queue.Queue();app.stop=threading.Event()
        app.start=variable('96');app.end=variable('95');app.prefix=variable('demo')
        app.region=variable('R2-US East');app.server_mode=variable('First server (top left)')
        app.server=variable('1218');app.code=variable('NEW-CODE');app.password=variable('test-secret')
        app.listed=variable(True);app.remember=variable(False);app.skip=variable(True)
        app.inputs=[Mock()];app.start_button=Mock();app.region_box=Mock();app.mode_box=Mock();app.server_entry=Mock()
        return app

    def test_region_and_first_server_are_saved_and_passed_to_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'accounts.txt').write_text('demo95\ndemo96\n')
            (root/'.telegram_credentials').write_bytes(b'test placeholder')
            app=self.app()
            with patch.object(gui,'BASE',root),patch.object(gui,'RecorderInstance'),patch.object(gui.threading,'Thread') as worker,patch.object(gui.messagebox,'showerror') as errors:
                app.start_run()
                errors.assert_not_called()
                self.assertEqual(worker.call_args.kwargs['args'][-2:],('R2-US East','first'))
                data=json.loads((root/'smart_bot_settings.json').read_text())
                self.assertEqual(data['region'],'R2-US East')
                self.assertEqual(data['server_mode'],'First server (top left)')
                self.assertNotIn('test-secret',json.dumps(data))

    def test_exact_server_is_not_replaced_by_first_server(self):
        app=self.app();app.server_mode=variable('Server number');app.server=variable('1225')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'accounts.txt').write_text('demo95\ndemo96\n');(root/'.telegram_credentials').touch()
            with patch.object(gui,'BASE',root),patch.object(gui,'RecorderInstance'),patch.object(gui.threading,'Thread') as worker,patch.object(gui.messagebox,'showerror') as errors:
                app.start_run();errors.assert_not_called()
                self.assertEqual(worker.call_args.kwargs['args'][-1],1225)

    def test_first_server_is_frozen_for_remaining_accounts(self):
        app=self.app();app.lock=Mock();app.account_started=None
        with tempfile.TemporaryDirectory() as folder,patch.object(gui,'BASE',Path(folder)),patch.object(gui,'Bot') as factory:
            bot=factory.return_value;bot.server=1225
            app.run(['demo96','demo95'],'password','CODE',True,'R2-US East','first')
            self.assertEqual(bot.run_account.call_count,2)
            self.assertEqual(bot.server_choice,1225)
            self.assertIsNone(app.lock)


if __name__=='__main__':unittest.main()
