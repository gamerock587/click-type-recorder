"""Offline regressions: no desktop input and no network traffic."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from PIL import Image
import smart_bot as sb


class AccountTests(unittest.TestCase):
    def test_ranges_are_account_numbers_not_line_offsets(self):
        self.assertEqual(sb.account_range('demo',99,97),['demo99','demo98','demo97'])
        self.assertEqual(sb.account_range('demo',100,102),['demo100','demo101','demo102'])
        self.assertEqual(sb.account_range('demo',3,3),['demo3'])

    def test_bad_input_is_rejected(self):
        for args in [('demo',0,2),('../bad',1,2),('',1,2)]:
            with self.assertRaises(ValueError):sb.account_range(*args)

    def test_optional_account_file_handles_missing_three_without_shifting_numbers(self):
        selected,missing=sb.select_accounts('demo',4,1,['demo1','demo2','demo4'])
        self.assertEqual(selected,['demo4','demo2','demo1'])
        self.assertEqual(missing,['demo3'])


class InputGuards(unittest.TestCase):
    def desktop(self):
        d=object.__new__(sb.Desktop)
        d.stop=threading.Event();d.api=Mock();d.mouse=Mock()
        d.api.GetForegroundWindow.return_value=99
        return d

    def test_old_frame_cannot_click(self):
        d=self.desktop()
        f=sb.Frame(Image.new('RGB',(1088,671)),99,(0,0,1088,671),time.monotonic()-5)
        with self.assertRaisesRegex(RuntimeError,'old screenshot'):d.click(f,500,500)
        d.mouse.press.assert_not_called()

    def test_focus_change_cannot_click(self):
        d=self.desktop();d.api.GetForegroundWindow.return_value=888
        f=sb.Frame(Image.new('RGB',(1088,671)),99,(0,0,1088,671),time.monotonic())
        with self.assertRaisesRegex(RuntimeError,'Focus changed'):d.click(f,500,500)
        d.mouse.press.assert_not_called()

    def test_f9_blocks_input(self):
        d=self.desktop();d.stop.set()
        f=sb.Frame(Image.new('RGB',(1088,671)),99,(0,0,1088,671),time.monotonic())
        with self.assertRaises(InterruptedError):d.click(f,500,500)
        d.mouse.press.assert_not_called()


class CompletionTests(unittest.TestCase):
    def make_bot(self):
        bot=object.__new__(sb.Bot)
        bot.stop=threading.Event();bot.report=lambda text:None
        bot.region='R2-US West';bot.server=1218;bot.server_choice=1218
        bot.escape_key=sb.keyboard.Key.esc;bot.checkpoint={}
        bot.desktop=Mock();bot.login=Mock();bot.tutorial=Mock()
        bot.frame=sb.Frame(Image.new('RGB',(1088,671)),99,(0,0,1088,671),time.monotonic())
        bot.rewards=Mock(return_value=bot.frame);bot.stage='';bot.run_dir=None
        return bot

    def test_failed_delivery_does_not_mark_complete_or_close(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bot=self.make_bot()
            with patch.object(sb,'BASE',root),patch.object(sb,'RUNS',root/'runs'),patch.object(sb,'TelegramClient') as client:
                client.return_value.send_image.side_effect=RuntimeError('Delivery unconfirmed')
                with self.assertRaisesRegex(RuntimeError,'Delivery unconfirmed'):
                    bot.run_account('demo99','test-secret','TEST-CODE')
                self.assertFalse((root/'smart_bot_progress.json').exists())
                bot.desktop.close.assert_not_called()
                self.assertNotIn('test-secret',(bot.run_dir/'run.log').read_text())

    def test_success_requires_all_steps_then_delivers_and_closes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bot=self.make_bot()
            with patch.object(sb,'BASE',root),patch.object(sb,'RUNS',root/'runs'),patch.object(sb,'TelegramClient') as client:
                client.return_value.send_image.return_value={'message_id':123}
                result=bot.run_account('demo99','test-secret','TEST-CODE')
                bot.login.assert_called_once_with('demo99','test-secret')
                bot.tutorial.assert_called_once_with('demo99')
                bot.rewards.assert_called_once_with('TEST-CODE','code')
                client.return_value.send_image.assert_called_once()
                bot.desktop.close.assert_called_once()
                data=json.loads((root/'smart_bot_progress.json').read_text())
                self.assertEqual(data['demo99|R2-US West|1218|TEST-CODE']['telegram_message_id'],123)
                self.assertEqual(result['status'],'complete')


class RegionTests(unittest.TestCase):
    def test_server_parser_does_not_assume_1218(self):
        for label,number in [('[S1218]Templar Wisp',1218),('[$1220] New Server',1220),('[S85] Example',85)]:
            self.assertEqual(sb.parse_server_number(label),number)
        self.assertIsNone(sb.parse_server_number('Last Played'))

    def test_completions_are_separate_for_each_region_server_and_code(self):
        data={'demo99|1218|CODE':{'status':'complete'}}
        self.assertTrue(sb.was_completed(data,'demo99','R2-US West',1218,'CODE'))
        self.assertFalse(sb.was_completed(data,'demo99','R2-US East',1218,'CODE'))
        self.assertFalse(sb.was_completed(data,'demo99','R2-US West',1219,'CODE'))
        self.assertFalse(sb.was_completed(data,'demo99','R2-US West',1218,'NEW'))


class RecoveryTests(unittest.TestCase):
    def bot(self):
        bot=object.__new__(sb.Bot)
        bot.checkpoint={};bot.account='demo99';bot.stop=Mock();bot.desktop=Mock()
        bot.log=Mock();bot.snapshot=Mock(return_value=object())
        bot.tutorial=Mock(side_effect=TimeoutError('Waiting for Barracks'))
        bot.finish_city=Mock();bot.rewards=Mock(return_value='verified bag')
        bot.screen_state=Mock(return_value='barracks')
        return bot

    def test_hidden_barracks_title_recovers_at_barracks_not_tutorial_start(self):
        bot=self.bot()
        self.assertEqual(bot.execute_workflow('CODE'),'verified bag')
        bot.finish_city.assert_called_once()
        bot.tutorial.assert_called_once_with('demo99')
        bot.rewards.assert_called_once_with('CODE','code')

    def test_advanced_mail_state_does_not_redeem_again(self):
        bot=self.bot();bot.screen_state.return_value='mail_detail'
        bot.checkpoint={'code_submitted':True}
        self.assertEqual(bot.execute_workflow('CODE'),'verified bag')
        bot.finish_city.assert_not_called()
        bot.rewards.assert_called_once_with('CODE','mail')

    def test_advanced_bag_requires_exchange_evidence(self):
        bot=self.bot()
        self.assertIsNone(bot.recovery_route('bag'))
        bot.checkpoint={'exchange_submitted':True}
        self.assertEqual(bot.recovery_route('bag'),'verify_exchange')
        bot.checkpoint['exchange_done']=True
        self.assertEqual(bot.recovery_route('bag'),'bag')

    def test_stop_during_recovery_is_never_retried(self):
        bot=self.bot();bot.desktop.check.side_effect=InterruptedError('F9')
        with self.assertRaises(InterruptedError):bot.execute_workflow('CODE')
        bot.finish_city.assert_not_called();bot.rewards.assert_not_called()

    def test_submitted_exchange_is_verified_without_repeating_purchase(self):
        bot=self.bot();f=Mock();f.image=Mock();bot.snapshot.return_value=f
        bot.checkpoint={'exchange_submitted':True};bot.read=Mock(return_value='lucky exchange')
        bot.wait_text=Mock(return_value=f);bot.click=Mock();bot.run_dir=Path('unused')
        bot.mark_checkpoint=Mock()
        bot.exchange()
        self.assertFalse(any(call.args[3]=='Exchange 15 Sylph Calls' for call in bot.click.call_args_list))
        bot.mark_checkpoint.assert_called_once_with(exchange_done=True)


class CharacterCreationTests(unittest.TestCase):
    def bot(self):
        bot=object.__new__(sb.Bot)
        bot.stop=Mock();bot.desktop=Mock();bot.vision=Mock();bot.log=Mock();bot.click=Mock()
        bot.snapshot=Mock(return_value=object())
        bot.vision.locate.return_value=(833,620)
        return bot

    def test_waits_for_text_to_paint_before_submitting_once(self):
        bot=self.bot()
        bot.vision.character_name_matches.side_effect=[False,False,True]
        bot.character_creation(object(),'demo59',False)
        bot.desktop.fill.assert_called_once()
        self.assertEqual(sum(c.args[3]=='Create Knight demo59' for c in bot.click.call_args_list),1)

    def test_retypes_after_missed_input(self):
        bot=self.bot()
        bot.vision.character_name_matches.side_effect=[False,False,False,True]
        bot.character_creation(object(),'demo59',False)
        self.assertEqual(bot.desktop.fill.call_count,2)

    def test_unverified_name_never_submits_and_enters_timeout_recovery(self):
        bot=self.bot();bot.vision.character_name_matches.return_value=False
        with self.assertRaisesRegex(TimeoutError,'after 3 attempts'):
            bot.character_creation(object(),'demo59',False)
        self.assertEqual(bot.desktop.fill.call_count,3)
        self.assertEqual(bot.click.call_count,1)  # Only class selection.

    def test_changed_screen_never_receives_name_input(self):
        bot=self.bot();bot.vision.locate.return_value=None
        with self.assertRaisesRegex(TimeoutError,'screen changed'):
            bot.character_creation(object(),'demo59',False)
        bot.desktop.fill.assert_not_called()

    def test_already_submitted_character_is_not_resubmitted(self):
        bot=self.bot();bot.character_creation(object(),'demo59',True)
        bot.click.assert_not_called();bot.desktop.fill.assert_not_called()

    def test_stop_during_name_verification_propagates(self):
        bot=self.bot();bot.snapshot.side_effect=InterruptedError('F9')
        with self.assertRaises(InterruptedError):bot.character_creation(object(),'demo59',False)
        bot.desktop.fill.assert_not_called()


# Private screenshot regression fixtures are excluded from the public source export.

if __name__=='__main__':unittest.main()
